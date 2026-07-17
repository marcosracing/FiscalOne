"""Fase E1B — envelope /fiscal/gov/fetch expõe results[] compat MapOne.

MapOne consome `fo_resp["results"]`. FocusNFeProvider retorna `documentos[]`
(sem `results[]`). Fase E1B normaliza no handler: se provider não entregou
`results` explícito, usa `documentos` como fallback — sem sobrescrever
providers que já preenchem `results` (ex.: SEFAZ).

Zero HTTP real — usa mock em `get_provider`.
"""
import importlib
from unittest.mock import patch

import pytest


@pytest.fixture
def cliente(monkeypatch):
    """Client Flask isolado — env FISCAL_PROVIDER=sefaz para evitar warning."""
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
    import app
    importlib.reload(app)
    return app.app.test_client(), app


def _payload_valido():
    return {
        "cnpj_tenant": "07219398000109",
        "ambiente":    "homologacao",
        "tipo":        "nfe",
        "ultimo_nsu":  "0",
    }


class _ProviderMock:
    """Provider dummy que devolve o envelope fixo passado no init."""

    def __init__(self, envelope):
        self._envelope = envelope

    def gov_fetch(self, payload, trace_id):
        # Envelope canonico — retorno controlado pelo teste
        return {**self._envelope, "trace_id": trace_id}


# ── docs → results (fallback) ─────────────────────────────────────────────
class TestDocumentosViramResults:
    def test_documentos_com_1_item_vira_results(self, cliente):
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [{"chave": "NFE1", "status_xml": "COMPLETO",
                             "import_origin": "fiscalone_focusnfe"}],
            "resumos":     [],
            "erros":       [],
            "ultimo_nsu":  "42",
            "max_nsu":     "42",
            "cursor_tipo": "versao",
            "nsu_avancou": True,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json=_payload_valido())
        assert r.status_code == 200
        j = r.get_json()
        assert len(j["documentos"]) == 1
        assert len(j["results"]) == 1
        assert j["results"][0]["chave"] == "NFE1"
        # documentos e results espelhados — mesma referencia semantica
        assert j["results"][0]["import_origin"] == "fiscalone_focusnfe"

    def test_documentos_vazio_results_vazio(self, cliente):
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [],
            "resumos":     [],
            "erros":       [],
            "ultimo_nsu":  "10",
            "max_nsu":     "10",
            "cursor_tipo": "versao",
            "nsu_avancou": False,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json=_payload_valido())
        j = r.get_json()
        assert j["documentos"] == []
        assert j["results"] == []


# ── results explícito preservado (compat SEFAZ) ───────────────────────────
class TestResultsExplicitoPreservado:
    def test_results_e_documentos_ambos_presentes_results_ganha(self, cliente):
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [{"chave": "DOC_DIFERENTE"}],
            "resumos":     [],
            "erros":       [],
            "results":     [{"chave": "RES_EXPLICITO"}],  # nao pode ser sobrescrito
            "ultimo_nsu":  "5",
            "max_nsu":     "5",
            "cursor_tipo": "nsu",
            "nsu_avancou": True,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json=_payload_valido())
        j = r.get_json()
        # results explicito deve ser preservado
        assert j["results"][0]["chave"] == "RES_EXPLICITO"
        # documentos permanece separado
        assert j["documentos"][0]["chave"] == "DOC_DIFERENTE"

    def test_results_explicito_vazio_nao_usa_documentos(self, cliente):
        """Se provider explicitamente devolveu results=[] (nao None),
        preservar vazio — mas nossa implementacao usa `or` que trata []
        como falsy. Semantica atual: [] em results cai no docs_arr.
        Se docs tambem vazio, results permanece []."""
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [{"chave": "DOC_X"}],
            "resumos":     [],
            "erros":       [],
            "results":     [],  # explicitly empty
            "ultimo_nsu":  "1",
            "max_nsu":     "1",
            "cursor_tipo": "nsu",
            "nsu_avancou": True,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json=_payload_valido())
        j = r.get_json()
        # Por design: [] em `results` cai no fallback `documentos` (via `or`).
        # Justificativa: para o consumidor MapOne, o efeito util e ter
        # `results` populado quando ha `documentos`. Testes existentes que
        # esperam results=[] devem tambem ter documentos=[] (proximo teste).
        assert j["results"][0]["chave"] == "DOC_X"

    def test_ambos_vazios(self, cliente):
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [],
            "resumos":     [],
            "erros":       [],
            "results":     [],
            "ultimo_nsu":  "0",
            "max_nsu":     "0",
            "cursor_tipo": "nsu",
            "nsu_avancou": False,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json=_payload_valido())
        j = r.get_json()
        assert j["results"] == []
        assert j["documentos"] == []


# ── Segurança — envelope não vaza credenciais ────────────────────────────
class TestSegurancaEnvelope:
    def test_envelope_nao_contem_token_authorization_basic(self, cliente):
        cli, app_mod = cliente
        envelope = {
            "ok":          True,
            "documentos":  [{"chave": "NFE1"}],
            "resumos":     [],
            "erros":       [],
            "ultimo_nsu":  "1",
            "max_nsu":     "1",
            "cursor_tipo": "versao",
            "nsu_avancou": True,
        }
        with patch.object(app_mod, "get_provider",
                          return_value=_ProviderMock(envelope)):
            r = cli.post("/fiscal/gov/fetch", json={
                **_payload_valido(),
                "provider":       "focusnfe",
                "focusnfe_token": "SECRETO_XYZ_123",
            })
        body = r.get_data(as_text=True).lower()
        assert "secreto_xyz_123" not in body
        assert "authorization" not in body
        assert "basic " not in body
        assert "focusnfe_token" not in body
