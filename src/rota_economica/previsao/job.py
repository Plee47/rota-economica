"""Job semanal da previsão no Databricks: backtest, registro, promoção, lote e monitoramento.

Ponto de entrada `rota-previsao`. Roda depois das camadas, no mesmo job. Tudo o que decide algo
está em funções puras testadas (`avaliacao`, `monitor`); aqui fica só a ligação com Spark e MLflow.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
import tempfile
from pathlib import Path

import pandas as pd

import rota_economica
from rota_economica.previsao import avaliacao, dados, monitor
from rota_economica.previsao.modelos import Gradiente, Ingenuo, em_reais

log = logging.getLogger("rota-previsao")
ORIGENS_BACKTEST = 52
RETREINO_SEMANAS = 4  # no backtest, retreina a cada 4 semanas, como em produção
APELIDO = "campeao"
EXPERIMENTO = "/Shared/rota-economica/previsao-diesel"


class ModeloPyfunc:
    """Embrulho para o MLflow: recebe as features de origem e devolve preços em R$/L."""

    def __init__(self, modelo):
        self.modelo = modelo

    def prever_em_reais(self, origem: pd.DataFrame) -> pd.DataFrame:
        return em_reais(origem, self.modelo.prever(origem))


def _pyfunc_classe():
    import mlflow.pyfunc

    class Previsor(mlflow.pyfunc.PythonModel):
        def load_context(self, context):
            with open(context.artifacts["modelo"], "rb") as f:
                self.embrulho = pickle.load(f)

        def predict(self, context, model_input, params=None):
            return self.embrulho.prever_em_reais(model_input)

    return Previsor


def _parametro_codigo() -> str:
    """`code_paths` no MLflow recente, `code_path` nas versões antigas (como a do serverless)."""
    import inspect

    import mlflow.pyfunc

    parametros = inspect.signature(mlflow.pyfunc.log_model).parameters
    return "code_paths" if "code_paths" in parametros else "code_path"


def _backtest(s: pd.DataFrame):
    origens = list(sorted(s["semana"].unique())[-ORIGENS_BACKTEST - max(dados.HORIZONTES) : -4])
    r_ing = avaliacao.backtest(s, Ingenuo, origens)
    r_gra = avaliacao.backtest(s, Gradiente, origens, retreinar_a_cada=RETREINO_SEMANAS)
    return r_ing, r_gra


def _metricas_do_campeao(cliente, nome: str):
    try:
        versao = cliente.get_model_version_by_alias(nome, APELIDO)
    except Exception:  # ainda não há campeão
        return None, None
    tags = versao.tags or {}
    if "mae" not in tags:
        return versao, None
    m = avaliacao.Metricas(float(tags["mae"]), float(tags["mape"]), float(tags["cobertura"]), 0)
    return versao, m


def resumo_do_modelo(campeao, decisao, m_ing, m_gra, horizontes, alerta, semana) -> dict:
    """O que o site mostra sobre o modelo em produção."""
    return {
        "semana_dados": str(pd.Timestamp(semana).date()),
        "campeao": campeao,
        "decisao": {"promovido": decisao.promover, "motivo": decisao.motivo},
        "backtest": {"ingenuo": m_ing.como_dict(), "gradiente": m_gra.como_dict()},
        "por_horizonte": {
            str(h): {k: float(v) for k, v in linha.items()} for h, linha in horizontes.iterrows()
        },
        "monitoramento": {
            "alerta": alerta.disparou,
            "mae_recente": alerta.mae_recente,
            "mae_referencia": alerta.mae_referencia,
            "mensagem": alerta.mensagem,
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rota-previsao")
    p.add_argument("--esquema", required=True, help="catalogo.schema das tabelas e do modelo")
    p.add_argument("--exportacao", type=Path, help="pasta onde gravar modelo.json para o site")
    p.add_argument("--experimento", default=EXPERIMENTO, help="experimento do MLflow (do bundle)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import mlflow
    from mlflow import MlflowClient
    from mlflow.models import infer_signature
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experimento)
    cliente = MlflowClient()
    nome_modelo = f"{args.esquema}.previsor_diesel"

    gold_uf = spark.table(f"{args.esquema}.gold_diesel_semanal_uf").toPandas()
    gold_uf.attrs.clear()  # Spark Connect: métricas do plano em attrs
    s = dados.series(gold_uf)
    log.info("%d séries, %d semanas", s["serie"].nunique(), s["semana"].nunique())

    with mlflow.start_run(run_name="semanal") as run:
        r_ing, r_gra = _backtest(s)
        m_ing, m_gra = avaliacao.metricas(r_ing), avaliacao.metricas(r_gra)
        mlflow.log_params({"origens_backtest": ORIGENS_BACKTEST, "series": s["serie"].nunique()})
        mlflow.log_metrics({f"ingenuo_{k}": v for k, v in m_ing.como_dict().items()})
        mlflow.log_metrics({f"gradiente_{k}": v for k, v in m_gra.como_dict().items()})
        for h, linha in avaliacao.por_horizonte(r_gra).iterrows():
            mlflow.log_metric(f"gradiente_mae_h{h}", linha["mae"])

        # Modelo final: o gradiente treinado com todo o histórico.
        final = Gradiente().treinar(dados.exemplos(s))
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "modelo.pkl"
            caminho.write_bytes(pickle.dumps(ModeloPyfunc(final)))
            exemplo = dados.origem(s, s["semana"].max()).dropna(subset=["preco"]).head(5)
            # O Unity Catalog exige entrada e saída declaradas: a assinatura sai de uma previsão
            # real do modelo final (o MLflow antigo não a deduz sozinho do exemplo).
            assinatura = infer_signature(exemplo, ModeloPyfunc(final).prever_em_reais(exemplo))
            info = mlflow.pyfunc.log_model(
                artifact_path="previsor",  # aceito pelas versões 2 e 3 do MLflow
                python_model=_pyfunc_classe()(),
                artifacts={"modelo": str(caminho)},
                input_example=exemplo,
                signature=assinatura,
                registered_model_name=nome_modelo,
                # O código do pacote vai junto do modelo: o Model Serving não instala
                # pacotes que não estão no PyPI.
                **{_parametro_codigo(): [str(Path(rota_economica.__file__).parent)]},
                pip_requirements=["scikit-learn", "pandas", "numpy"],
            )
        versao = info.registered_model_version
        for k, v in m_gra.como_dict().items():
            cliente.set_model_version_tag(nome_modelo, versao, k, str(v))

        _, m_campeao = _metricas_do_campeao(cliente, nome_modelo)
        decisao = avaliacao.decidir(m_gra, m_campeao, m_ing)
        mlflow.set_tags({"promovido": str(decisao.promover), "motivo": decisao.motivo})
        log.info("versão %s: %s", versao, decisao.motivo)
        if decisao.promover:
            cliente.set_registered_model_alias(nome_modelo, APELIDO, versao)

        # Previsão em lote com o campeão (que pode ser a versão anterior). Se nenhum modelo
        # nunca bateu a base ingênua, quem prevê é a própria base, e isso fica registrado.
        origem = dados.origem(s, s["semana"].max()).dropna(subset=["preco"])
        versao_campeao, _ = _metricas_do_campeao(cliente, nome_modelo)
        if versao_campeao is not None:
            campeao = mlflow.pyfunc.load_model(f"models:/{nome_modelo}@{APELIDO}")
            previsoes = campeao.predict(origem)
            nome_campeao = f"gradiente v{versao_campeao.version}"
            referencia = float((versao_campeao.tags or {}).get("mae", m_gra.mae))
        else:
            base = Ingenuo().treinar(dados.exemplos(s))
            previsoes = ModeloPyfunc(base).prever_em_reais(origem)
            nome_campeao = "base ingênua"
            referencia = m_ing.mae
        previsoes["modelo"] = nome_campeao
        previsoes["run_id"] = run.info.run_id
        tabela = f"{args.esquema}.previsoes_diesel"
        if spark.catalog.tableExists(tabela):
            semana = origem["semana"].max().date()
            spark.sql(f"DELETE FROM {tabela} WHERE semana_origem = DATE '{semana}'")
        spark.createDataFrame(previsoes).write.mode("append").saveAsTable(tabela)

        # Monitoramento: previsões antigas contra o preço real.
        todas = spark.table(tabela).toPandas()
        todas.attrs.clear()
        conferidas = monitor.conferir(todas, s)
        alerta = monitor.avaliar(conferidas, referencia)
        mlflow.log_metric("monitor_mae_recente", alerta.mae_recente or 0.0)
        log.info("monitoramento: %s", alerta.mensagem)
        if args.exportacao:
            resumo = resumo_do_modelo(
                nome_campeao, decisao, m_ing, m_gra, avaliacao.por_horizonte(r_gra),
                alerta, origem["semana"].max(),
            )  # fmt: skip
            args.exportacao.mkdir(parents=True, exist_ok=True)
            (args.exportacao / "modelo.json").write_text(
                json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        if alerta.disparou:
            raise RuntimeError(alerta.mensagem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
