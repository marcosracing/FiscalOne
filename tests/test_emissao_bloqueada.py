"""Emissao fiscal continua bloqueada por design em toda rota emissora."""
import importlib
import pytest


@pytest.fixture
def cli(monkeypatch):
    """Producao com todas as 3 flags ligadas — emissao AINDA deve ser 403."""
    monkeypatch.setenv("FISCALONE_AMBIENTE", "producao")
    monkeypatch.setenv("FISCALONE_ENABLE_PRODUCAO", "1")
    monkeypatch.setenv("MAPONE_FISCAL_PRODUCAO_READY", "1")
    monkeypatch.setenv("FISCALONE_DFE_RECEBIDO_ONLY", "1")
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    import app
    importlib.reload(app)
    return app.app.test_client()


ROTAS_EMISSAO = [
    ("POST",   "/fiscal/nfe"),
    ("POST",   "/fiscal/cte"),
    ("POST",   "/fiscal/mdfe"),
    ("DELETE", "/fiscal/nfe/35260607219398000109550010000001231000001231"),
    ("DELETE", "/fiscal/cte/35260607219398000109570010000001231000001231"),
    ("POST",   "/fiscal/nfe/35260607219398000109550010000001231000001231/inutilizar"),
    ("POST",   "/fiscal/nfe/35260607219398000109550010000001231000001231/cce"),
    ("POST",   "/fiscal/mdfe/50260607219398000109580010000001231000001231/encerrar"),
    ("POST",   "/fiscal/mdfe/50260607219398000109580010000001231000001231/condutor"),
]


@pytest.mark.parametrize("metodo,rota", ROTAS_EMISSAO)
def test_rota_emissao_bloqueada_403(cli, metodo, rota):
    r = cli.open(rota, method=metodo, json={})
    assert r.status_code == 403, f"{metodo} {rota} nao devolveu 403"
    j = r.get_json()
    assert j["codigo"] == "EMISSAO_BLOQUEADA"
    assert j["ok"] is False


def test_emissao_bloqueada_por_design_no_health(cli):
    r = cli.get("/fiscal/health")
    j = r.get_json()
    assert j["emissao_ativa"] is False
    assert j["emissao_bloqueada_por_design"] is True
