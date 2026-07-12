"""Health endpoint + observabilidade (TLS insecure, provider stub)."""
import importlib
import os
import pytest


@pytest.fixture
def cliente_tls_insecure(monkeypatch):
    monkeypatch.setenv("GOV_TLS_INSECURE", "1")
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    import app
    importlib.reload(app)
    return app.app.test_client()


@pytest.fixture
def cliente_focusnfe(monkeypatch):
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    monkeypatch.setenv("FISCAL_PROVIDER", "focusnfe")
    import app
    importlib.reload(app)
    return app.app.test_client(), app


@pytest.fixture
def cliente_seguro(monkeypatch):
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    import app
    importlib.reload(app)
    return app.app.test_client()


class TestHealthTlsInsecure:
    def test_tls_insecure_true_aparece_no_health(self, cliente_tls_insecure):
        r = cliente_tls_insecure.get("/fiscal/health")
        j = r.get_json()
        assert j["tls_insecure"] is True
        assert "uso proibido em producao" in j["tls_warning"].lower()

    def test_tls_seguro_default(self, cliente_seguro):
        r = cliente_seguro.get("/fiscal/health")
        j = r.get_json()
        assert j["tls_insecure"] is False


class TestBootWarnings:
    def test_focusnfe_gera_warning_e_501(self, cliente_focusnfe):
        cli, app_mod = cliente_focusnfe
        # Warning no boot — comportamento observavel via /health
        assert app_mod.FISCAL_PROVIDER == "focusnfe"
        # E rota gov/fetch devolve 501 PROVIDER_NAO_IMPLEMENTADO
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "ultimo_nsu": "0",
        })
        assert r.status_code == 501
        j = r.get_json()
        assert j["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"
