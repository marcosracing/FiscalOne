"""
Envelope /fiscal/gov/fetch — acao e nsu_avancou por cStat SEFAZ / status ADN.

Regra:
  cStat 138 (ou docs>0)      → acao=DOCUMENTOS,    nsu_avancou=True
  cStat 137 (nenhum doc)     → acao=SEM_DOCUMENTO, nsu_avancou=True
  cStat 656 (consumo)        → acao=REJEITADO,     nsu_avancou=False
  cStat 589 (NSU>maxNSU)     → acao=REJEITADO,     nsu_avancou=False
  Erro tecnico FiscalOne     → acao=ERRO,          nsu_avancou=False
"""
import importlib
import pytest


@pytest.fixture
def cli(monkeypatch):
    """FiscalOne em homologacao (nao exige flags de producao)."""
    monkeypatch.setenv("FISCALONE_AMBIENTE", "homologacao")
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    import app
    importlib.reload(app)
    return app.app.test_client()


def _stub_provider(monkeypatch, result_dict):
    """Substitui SefazProvider.gov_fetch para devolver dict pre-fabricado."""
    from providers.sefaz_provider import SefazProvider
    monkeypatch.setattr(
        SefazProvider, "gov_fetch",
        lambda self, payload, trace_id: dict(result_dict, cert_fonte="test"),
    )


# ═══════════════════════════════════════════════════════════════════
# Classificador direto
# ═══════════════════════════════════════════════════════════════════

class TestClassificadorAcao:
    def test_cstat_138_com_docs_documentos(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch({"cstat": "138"}, 3)
        assert a == "DOCUMENTOS"
        assert _nsu_avancou(a) is True

    def test_cstat_138_sem_docs_ainda_documentos(self):
        # cstat 138 e sinal definitivo; contagem 0 pode ocorrer em edge case
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch({"cstat": "138"}, 0)
        assert a == "DOCUMENTOS"
        assert _nsu_avancou(a) is True

    def test_cstat_137_sem_documento(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch({"cstat": "137"}, 0)
        assert a == "SEM_DOCUMENTO"
        assert _nsu_avancou(a) is True

    def test_cstat_656_rejeitado_nao_avanca(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch(
            {"cstat": "656", "xmotivo": "Consumo indevido"}, 0)
        assert a == "REJEITADO"
        assert _nsu_avancou(a) is False

    def test_cstat_589_rejeitado(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch({"cstat": "589"}, 0)
        assert a == "REJEITADO"
        assert _nsu_avancou(a) is False

    def test_erro_tecnico_fiscalone_e_erro(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        for cod in ("CERT_NAO_CONFIGURADO", "SEFAZ_HTTP_ERRO",
                    "NFSE_ADN_HTTP_ERRO", "TLS_ERRO", "PROVIDER_NAO_IMPLEMENTADO"):
            a = _classificar_acao_gov_fetch({"codigo": cod}, 0)
            assert a == "ERRO", f"{cod} deveria virar ERRO, veio {a}"
            assert _nsu_avancou(a) is False

    def test_nfse_adn_documentos_localizados(self):
        # NFS-e via ADN nao tem cstat, tem status
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch(
            {"cstat": None, "status": "DOCUMENTOS_LOCALIZADOS"}, 2)
        assert a == "DOCUMENTOS"
        assert _nsu_avancou(a) is True

    def test_nfse_adn_sem_documento(self):
        from app import _classificar_acao_gov_fetch, _nsu_avancou
        a = _classificar_acao_gov_fetch(
            {"cstat": None, "status": "SEM_DOCUMENTO"}, 0)
        assert a == "SEM_DOCUMENTO"
        assert _nsu_avancou(a) is True


# ═══════════════════════════════════════════════════════════════════
# Envelope end-to-end via /fiscal/gov/fetch
# ═══════════════════════════════════════════════════════════════════

class TestEnvelopeGovFetch:
    def _payload_base(self):
        return {
            "cnpj_tenant": "07219398000109",
            "ambiente":    "homologacao",
            "tipo":        "nfe",
            "ultimo_nsu":  "7400",
        }

    def test_cstat_138_docs_envelope_completo(self, monkeypatch, cli):
        _stub_provider(monkeypatch, {
            "ok": True, "cstat": "138", "xmotivo": "Documentos localizados",
            "ultimo_nsu": "000000000007414", "max_nsu": "000000000007414",
            "cooldown_recomendado_seg": 0,
            "documentos": [{"chave": "x", "status_xml": "COMPLETO"}],
            "resumos": [], "erros": [], "results": [],
        })
        r = cli.post("/fiscal/gov/fetch", json=self._payload_base())
        j = r.get_json()
        assert j["ok"] is True
        assert j["acao"] == "DOCUMENTOS"
        assert j["nsu_avancou"] is True
        assert j["cstat"] == "138"
        assert j["xmotivo"] == "Documentos localizados"
        assert j["ultimo_nsu_antes"] == "7400"
        assert j["ultimo_nsu"] == "000000000007414"
        assert j["max_nsu"] == "000000000007414"

    def test_cstat_137_sem_docs(self, monkeypatch, cli):
        _stub_provider(monkeypatch, {
            "ok": True, "cstat": "137", "xmotivo": "Nenhum documento",
            "ultimo_nsu": "000000000007414", "max_nsu": "000000000007414",
            "cooldown_recomendado_seg": 3600,
            "documentos": [], "resumos": [], "erros": [], "results": [],
        })
        r = cli.post("/fiscal/gov/fetch", json=self._payload_base())
        j = r.get_json()
        assert j["acao"] == "SEM_DOCUMENTO"
        assert j["nsu_avancou"] is True
        assert j["cstat"] == "137"
        assert j["cooldown_recomendado_seg"] == 3600

    def test_cstat_656_rejeitado(self, monkeypatch, cli):
        _stub_provider(monkeypatch, {
            "ok": True, "cstat": "656",
            "xmotivo": "Rejeicao: Consumo Indevido",
            "ultimo_nsu": "000000000007400", "max_nsu": "000000000007414",
            "cooldown_recomendado_seg": 3900,
            "documentos": [], "resumos": [], "erros": [], "results": [],
        })
        r = cli.post("/fiscal/gov/fetch", json=self._payload_base())
        j = r.get_json()
        assert j["acao"] == "REJEITADO"
        assert j["nsu_avancou"] is False
        assert j["cstat"] == "656"
        assert "Consumo" in j["xmotivo"]
        assert j["ultimo_nsu_antes"] == "7400"
        # MapOne NAO deve avancar NSU no CtrlOne
        assert j["cooldown_recomendado_seg"] == 3900

    def test_erro_tecnico_sem_avancar_nsu(self, cli):
        """Sem cert configurado → CERT_NAO_CONFIGURADO → acao=ERRO."""
        r = cli.post("/fiscal/gov/fetch", json=self._payload_base())
        j = r.get_json()
        assert j["acao"] == "ERRO"
        assert j["nsu_avancou"] is False
        assert j["codigo"] == "CERT_NAO_CONFIGURADO"
        assert j["ultimo_nsu_antes"] == "7400"

    def test_envelope_sempre_tem_campos_obrigatorios(self, monkeypatch, cli):
        """cstat, xmotivo, acao, nsu_avancou devem sempre existir no envelope."""
        _stub_provider(monkeypatch, {
            "ok": True, "cstat": "138", "xmotivo": "Documentos localizados",
            "ultimo_nsu": "1", "max_nsu": "1",
            "documentos": [{"chave": "x"}], "resumos": [], "erros": [], "results": [],
        })
        r = cli.post("/fiscal/gov/fetch", json=self._payload_base())
        j = r.get_json()
        for campo in ("cstat", "xmotivo", "acao", "nsu_avancou",
                      "ultimo_nsu_antes", "ultimo_nsu", "max_nsu"):
            assert campo in j, f"envelope sem campo obrigatorio: {campo}"

    def test_nfse_adn_envelope(self, monkeypatch, cli):
        """NFS-e via ADN — cstat null, mas envelope ainda expoe acao/nsu_avancou."""
        _stub_provider(monkeypatch, {
            "ok": True, "cstat": None, "xmotivo": None,
            "provider": "nfse_nacional",
            "ambiente_adn": "producao",
            "status": "DOCUMENTOS_LOCALIZADOS",
            "status_processamento": "DOCUMENTOS_LOCALIZADOS",
            "ultimo_nsu": "125700", "max_nsu": "125800",
            "cooldown_recomendado_seg": 0,
            "documentos": [{"chave": "x", "status_xml": "COMPLETO"}],
            "resumos": [], "erros": [], "results": [],
        })
        payload = self._payload_base()
        payload["tipo"] = "nfse"
        payload["ultimo_nsu"] = "125643"
        r = cli.post("/fiscal/gov/fetch", json=payload)
        j = r.get_json()
        assert j["acao"] == "DOCUMENTOS"
        assert j["nsu_avancou"] is True
        assert j["status"] == "DOCUMENTOS_LOCALIZADOS"
        assert j["ultimo_nsu_antes"] == "125643"
        assert j["ultimo_nsu"] == "125700"
