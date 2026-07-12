"""Contrato do GovProvider ABC + FocusNFeProvider PROVIDER_NAO_IMPLEMENTADO."""
import pytest
from providers import GovProvider
from providers.focusnfe_provider import FocusNFeProvider
from providers.sefaz_provider import SefazProvider


class TestGovProviderABC:
    def test_nao_pode_instanciar_abstrato(self):
        with pytest.raises(TypeError):
            GovProvider()

    def test_metodos_abstratos_declarados(self):
        assert "gov_fetch" in GovProvider.__abstractmethods__
        assert "consultar_dfe_nsu" in GovProvider.__abstractmethods__


class TestFocusNFeStub:
    """Nao pode ter caminho silencioso. Sempre PROVIDER_NAO_IMPLEMENTADO."""

    def setup_method(self):
        self.p = FocusNFeProvider()

    def test_gov_fetch_estruturado(self):
        r = self.p.gov_fetch({"cnpj_tenant": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"
        assert r["provider"] == "focusnfe"
        assert r["trace_id"] == "fo-t"
        assert "Provider nao implementa" in r["erro"]

    def test_consultar_dfe_nsu_estruturado(self):
        r = self.p.consultar_dfe_nsu(b"", b"", "00", "0", "homologacao", "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"

    def test_nao_vaza_token(self):
        """Envelope nao deve conter FOCUSNFE_TOKEN, base_url completo, etc."""
        r = self.p.gov_fetch({}, "fo-t")
        payload = str(r)
        assert "token" not in payload.lower()
        assert "focusnfe.com.br" not in payload.lower()
        assert "password" not in payload.lower()


class TestSefazProviderCompleto:
    """SefazProvider satisfaz o contrato abstrato (nao levanta TypeError)."""

    def test_instanciavel(self):
        p = SefazProvider()
        assert hasattr(p, "gov_fetch")
        assert hasattr(p, "consultar_dfe_nsu")
