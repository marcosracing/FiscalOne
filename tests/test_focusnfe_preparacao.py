"""FocusNFe Fase 2-prep — infraestrutura sem HTTP real.

Cobre:
- `_masked_token()` nunca vaza valor completo.
- `EmissaoProibida` levantada apenas em `emitir_*`.
- `gov_fetch` e `consultar_dfe_nsu` continuam stubados
  (PROVIDER_NAO_IMPLEMENTADO) — Fase 2 (HTTP real) e prep separada.
- `FocusNFeProvider.__init__` le envs sem fail-fast global.
- `FOCUSNFE_BASE_URL` tem `/` final removido.
- `FOCUSNFE_TIMEOUT` invalido cai para 30.
- `_require_token()` levanta apenas quando chamado sem token.
- `ImportOrigin` inclui `fiscalone_focusnfe`.
"""
import pytest

from providers.focusnfe_provider import (
    FocusNFeProvider,
    EmissaoProibida,
    _masked_token,
)
from schemas import VALID_IMPORT_ORIGIN


# ── _masked_token ───────────────────────────────────────────────────────────
class TestMaskedToken:
    def test_none_ausente(self):
        assert _masked_token(None) == "***[ausente]"

    def test_vazio_ausente(self):
        assert _masked_token("") == "***[ausente]"

    def test_curto_mascara_completa(self):
        assert _masked_token("abc") == "***"

    def test_exatamente_4_chars_mascara_completa(self):
        # <=4 nao mostra ultimos 4 (seria o token inteiro)
        assert _masked_token("abcd") == "***"

    def test_mostra_ultimos_4(self):
        assert _masked_token("abcdef") == "***cdef"

    def test_token_longo_mostra_apenas_ultimos_4(self):
        long_token = "sk_live_" + "x" * 40
        masked = _masked_token(long_token)
        assert masked.startswith("***")
        assert masked.endswith("xxxx")
        assert long_token not in masked

    def test_nao_vaza_token_completo(self):
        secret = "TOKEN_SECRETO_DE_PRODUCAO_1234"
        masked = _masked_token(secret)
        assert secret not in masked
        assert "TOKEN_SECRETO" not in masked


# ── EmissaoProibida — apenas em emitir_* ────────────────────────────────────
class TestEmissaoBloqueada:
    def setup_method(self):
        self.p = FocusNFeProvider()

    def test_emitir_cte_levanta(self):
        with pytest.raises(EmissaoProibida):
            self.p.emitir_cte({"chave": "x"})

    def test_emitir_mdfe_levanta(self):
        with pytest.raises(EmissaoProibida):
            self.p.emitir_mdfe({"chave": "x"})

    def test_emissao_proibida_e_runtime_error(self):
        assert issubclass(EmissaoProibida, RuntimeError)


# ── gov_fetch / consultar_dfe_nsu — Fase 2 HTTP (sem token = FOCUS_TOKEN_AUSENTE) ─
class TestSemTokenErroEstruturado:
    """Fase 2 HTTP: gov_fetch nao e mais stub. Sem token, falha localmente
    com envelope FOCUS_TOKEN_AUSENTE antes de qualquer HTTP."""

    def test_gov_fetch_sem_token(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"
        assert r["provider"] == "focusnfe"
        assert r["trace_id"] == "fo-t"

    def test_consultar_dfe_nsu_delega_e_falha_sem_token(self, monkeypatch):
        # consultar_dfe_nsu agora delega para gov_fetch (Fase 2 HTTP).
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.consultar_dfe_nsu(b"", b"", "07219398000109", "0", "homologacao", "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"

    def test_metodos_nao_emissao_permanecem_stub(self, monkeypatch):
        # cancelar/encerrar/incluir_condutor NAO sao emitir_* — continuam stub
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        assert p.cancelar_cte("x", "y")["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"
        assert p.encerrar_mdfe("x")["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"
        assert p.incluir_condutor_mdfe("x", {})["codigo"] == "PROVIDER_NAO_IMPLEMENTADO"


# ── __init__ e leitura segura de envs ───────────────────────────────────────
class TestInitSemFailFast:
    def test_init_sem_token_nao_levanta(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        # __init__ nao pode falhar so porque token esta ausente — sao stubs.
        p = FocusNFeProvider()
        assert p._token == ""

    def test_init_com_token_le_valor(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123456")
        p = FocusNFeProvider()
        assert p._token == "abc123456"

    def test_base_url_remove_barra_final(self, monkeypatch):
        # Fase 2 HTTP: resolucao lazy via _base_url_for(). _base_url_env guarda
        # o valor bruto; a normalizacao (rstrip /) acontece no metodo.
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://x.example.com/v2/")
        p = FocusNFeProvider()
        assert p._base_url_for("producao") == "https://x.example.com/v2"

    def test_base_url_default_sem_env(self, monkeypatch):
        # Fase 2 HTTP: default seguro passou a ser homologacao (nao producao).
        # Producao so via FOCUSNFE_BASE_URL explicito ou ambiente='producao'.
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        monkeypatch.delenv("FOCUSNFE_AMBIENTE", raising=False)
        p = FocusNFeProvider()
        assert p._base_url_for(None) == "https://homologacao.focusnfe.com.br/v2"
        assert p._base_url_for("producao") == "https://api.focusnfe.com.br/v2"

    def test_timeout_invalido_cai_para_30(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TIMEOUT", "nao-e-numero")
        p = FocusNFeProvider()
        assert p._timeout == 30

    def test_timeout_valido_le_int(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TIMEOUT", "45")
        p = FocusNFeProvider()
        assert p._timeout == 45

    def test_timeout_ausente_cai_para_30(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TIMEOUT", raising=False)
        p = FocusNFeProvider()
        assert p._timeout == 30


# ── _require_token — fail-fast local para uso futuro (Fase 2) ───────────────
class TestRequireToken:
    def test_sem_token_levanta(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        with pytest.raises(RuntimeError, match="FOCUSNFE_TOKEN"):
            p._require_token()

    def test_com_token_retorna_valor(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "meu-token-de-teste")
        p = FocusNFeProvider()
        assert p._require_token() == "meu-token-de-teste"


# ── ImportOrigin inclui fiscalone_focusnfe ──────────────────────────────────
class TestImportOriginFocusNFe:
    def test_fiscalone_focusnfe_e_valido(self):
        assert "fiscalone_focusnfe" in VALID_IMPORT_ORIGIN

    def test_origins_existentes_preservados(self):
        # Nao remover valores existentes — todos os que estavam antes continuam.
        for esperado in (
            "fiscalone_gov_fetch",
            "fiscalone_sefaz",
            "fiscalone_upload",
            "fiscalone_nfse_adn",
            "fiscalone_email",
            "fiscalone_reparse",
        ):
            assert esperado in VALID_IMPORT_ORIGIN
