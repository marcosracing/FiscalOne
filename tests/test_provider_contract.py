"""Contrato do GovProvider ABC + FocusNFeProvider (Fase 2 HTTP — sem token vira FOCUS_TOKEN_AUSENTE)."""
import os
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


class TestFocusNFeSemToken:
    """Sem token, provider retorna envelope estruturado FOCUS_TOKEN_AUSENTE.

    Fase 2 HTTP: gov_fetch nao e mais stub; sem token, falha localmente
    com envelope controlado antes de qualquer HTTP.
    """

    def test_gov_fetch_sem_token_retorna_erro_estruturado(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"
        assert r["provider"] == "focusnfe"
        assert r["trace_id"] == "fo-t"
        assert "FOCUSNFE_TOKEN" in r["erro"]

    def test_consultar_dfe_nsu_delega_para_gov_fetch(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.consultar_dfe_nsu(b"", b"", "07219398000109", "0", "homologacao", "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"

    def test_nao_vaza_token_em_envelope_sem_token(self, monkeypatch):
        """Envelope nao deve conter authorization, base64, senha, api_key."""
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        payload = str(r).lower()
        assert "authorization" not in payload
        assert "password" not in payload
        assert "api_key" not in payload
        assert "basic " not in payload


class TestSefazProviderCompleto:
    """SefazProvider satisfaz o contrato abstrato (nao levanta TypeError)."""

    def test_instanciavel(self):
        p = SefazProvider()
        assert hasattr(p, "gov_fetch")
        assert hasattr(p, "consultar_dfe_nsu")
