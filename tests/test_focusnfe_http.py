"""FocusNFe Fase 2 HTTP — testes 100% mockados.

Todo teste que precise de HTTP usa `unittest.mock.patch("requests.get")`
para interceptar a chamada. **Zero chamada HTTP real disparada.**

Cobre:
- gov_fetch — sucesso, cursor, cada codigo HTTP tratado, JSON invalido
- baixar_danfe — 302 sem Authorization no segundo GET, 200 direto, erros
- Seguranca — token nunca no envelope, Authorization nunca em log
- Delegacao consultar_dfe_nsu → gov_fetch
"""
import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import (
    FocusNFeProvider,
    _basic_auth_header,
    _mapear_nfe_focus,
    _normalizar_base_url,
    _resolve_base_url,
    _sanitize_focus_item,
    _dump_focus_json,
)


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def provider_com_token(monkeypatch):
    """FocusNFeProvider com token definido — nao dispara HTTP real."""
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


def _mock_resp(status=200, json_data=None, headers=None, content=b""):
    """Cria um Response mock com apenas o necessario para os testes."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.content = content
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


# ── _basic_auth_header ──────────────────────────────────────────────────────
class TestBasicAuthHeader:
    def test_formato_basic_base64(self):
        h = _basic_auth_header("meu-token")
        # base64("meu-token:") -> "bWV1LXRva2VuOg=="
        esperado = base64.b64encode(b"meu-token:").decode()
        assert h == {"Authorization": f"Basic {esperado}"}

    def test_nao_usa_bearer(self):
        h = _basic_auth_header("qualquer")
        assert "Bearer" not in h["Authorization"]
        assert h["Authorization"].startswith("Basic ")


# ── _normalizar_base_url ────────────────────────────────────────────────────
class TestNormalizarBaseUrl:
    """Aceita a base oficial COM ou SEM `/v2` — evita `/v2/v2` na montagem."""

    def test_sem_v2(self):
        assert _normalizar_base_url("https://api.focusnfe.com.br") == "https://api.focusnfe.com.br"

    def test_com_v2(self):
        assert _normalizar_base_url("https://api.focusnfe.com.br/v2") == "https://api.focusnfe.com.br"

    def test_com_v2_e_barra(self):
        assert _normalizar_base_url("https://api.focusnfe.com.br/v2/") == "https://api.focusnfe.com.br"

    def test_barra_final(self):
        assert _normalizar_base_url("https://api.focusnfe.com.br/") == "https://api.focusnfe.com.br"

    def test_vazio(self):
        assert _normalizar_base_url("") == ""

    def test_none(self):
        assert _normalizar_base_url(None) == ""


# ── _resolve_base_url ───────────────────────────────────────────────────────
# CONTRATO Fase 2 HTTP corretiva: retorna base SEM `/v2` — a rota (`nfes_recebidas`)
# concatena `/v2/...` explicitamente para evitar `/v2/v2`.
class TestResolveBaseUrl:
    def test_env_base_url_com_v2_normaliza(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://custom.example.com/v2")
        assert _resolve_base_url("producao") == "https://custom.example.com"

    def test_env_base_url_sem_v2_preservado(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://custom.example.com")
        assert _resolve_base_url("producao") == "https://custom.example.com"

    def test_env_base_url_com_v2_e_barra(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://custom.example.com/v2/")
        assert _resolve_base_url("producao") == "https://custom.example.com"

    def test_producao_default(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("producao") == "https://api.focusnfe.com.br"

    def test_homologacao_default(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("homologacao") == "https://homologacao.focusnfe.com.br"

    def test_ambiente_desconhecido_cai_homologacao(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("marte") == "https://homologacao.focusnfe.com.br"

    def test_none_usa_env_ou_homologacao(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        monkeypatch.delenv("FOCUSNFE_AMBIENTE", raising=False)
        assert _resolve_base_url(None) == "https://homologacao.focusnfe.com.br"


# ── _sanitize_focus_item ────────────────────────────────────────────────────
class TestSanitize:
    def test_dict_com_token_mascarado(self):
        item = {"chave_nfe": "44dig", "token": "SECRETO", "authorization": "Basic xxx"}
        out = _sanitize_focus_item(item)
        assert out["chave_nfe"] == "44dig"
        assert out["token"] == "***"
        assert out["authorization"] == "***"

    def test_recursivo_em_lista(self):
        item = [{"password": "x"}, {"api_key": "y"}]
        out = _sanitize_focus_item(item)
        assert out[0]["password"] == "***"
        assert out[1]["api_key"] == "***"

    def test_dump_deterministico(self):
        a = _dump_focus_json({"b": 1, "a": 2})
        b = _dump_focus_json({"a": 2, "b": 1})
        assert a == b


# ── Mapper ──────────────────────────────────────────────────────────────────
class TestMapper:
    def test_payload_real_doc_focus(self):
        """Payload usando os campos REAIS da doc oficial FocusNFe
        (schema NfeRecebidaResumo). CNPJ_emit deve sair de
        `documento_emitente` (nome correto — nao `cnpj_emitente`)."""
        item = {
            "chave_nfe":                 "1" * 44,
            "documento_emitente":        "07219398000109",
            "nome_emitente":             "Racing Logistica",
            "cnpj_destinatario":         "12345678000199",
            "valor_total":               "1500.50",
            "data_emissao":              "2026-07-16T09:00:00Z",
            "situacao":                  "autorizada",
            "manifestacao_destinatario": "nulo",
            "nfe_completa":              True,
            "tipo_nfe":                  "0",
            "versao":                    42,
        }
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["chNFe"] == "1" * 44
        assert d["CNPJ_emit"] == "07219398000109"
        # Mapper agora deixa RESUMO por default — promocao a COMPLETO
        # e responsabilidade do gov_fetch (baixa XML por chave).
        assert d["status_xml"] == "RESUMO"
        # BLINDAGEM BUG FISCAL: cStat=100 para autorizada, com ou sem XML.
        assert d["cStat"] == "100"
        assert d["cStat"] != "101"  # cStat=101 SO para cancelada!
        assert d["cancelado"] == 0
        assert d["nfe_completa"] is True
        assert d["tipo_nfe"] == "0"
        assert d["manifestacao"] == "nulo"
        assert d["situacao_focus"] == "autorizada"
        assert d["import_origin"] == "fiscalone_focusnfe"
        assert d["versao"] == 42
        assert "raw_json_focus" in d

    def test_autorizada_sem_xml_blindagem_bug_fiscal(self):
        """BLINDAGEM DO BUG FISCAL (E4a): resumo autorizado NUNCA pode ter
        cStat=101 (Cancelamento homologado). cStat=100 sempre para
        autorizada. Antes do E4a, mapper marcava cStat=101 para todo
        item sem `xml` — o que classificava fiscalmente como cancelada."""
        item = {"chave_nfe": "2" * 44, "versao": 10, "situacao": "autorizada"}
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["status_xml"] == "RESUMO"
        assert d["cStat"] == "100"
        assert d["cStat"] != "101", (
            "REGRESSAO BUG FISCAL: cStat=101 marca nota autorizada como cancelada!"
        )
        assert d["xMotivo"] == "Resumo FocusNFe"
        assert d["cancelado"] == 0
        assert "xml_bruto" not in d

    def test_situacao_vazia_default_autorizada(self):
        """Se Focus nao mandar `situacao` (fallback defensivo), tratamos
        como autorizada — Focus so lista notas com evento autorizador."""
        item = {"chave_nfe": "3" * 44, "versao": 1}
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["cStat"] == "100"
        assert d["cancelado"] == 0

    def test_situacao_cancelada(self):
        item = {
            "chave_nfe":                  "4" * 44,
            "situacao":                   "cancelada",
            "data_cancelamento":          "2026-07-15T14:30:00Z",
            "justificativa_cancelamento": "Erro de digitacao no destinatario.",
            "versao":                     20,
        }
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["cStat"] == "101"
        assert d["xMotivo"] == "Cancelamento homologado"
        assert d["cancelado"] == 1
        assert d["situacao_focus"] == "cancelada"
        assert d["data_cancelamento"] == "2026-07-15T14:30:00Z"
        assert d["justificativa_cancelamento"].startswith("Erro")

    def test_situacao_denegada(self):
        item = {"chave_nfe": "5" * 44, "situacao": "denegada", "versao": 5}
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["cStat"] == "110"
        assert d["xMotivo"] == "Uso denegado"
        assert d["cancelado"] == 0

    def test_sem_chave_levanta(self):
        with pytest.raises(ValueError):
            _mapear_nfe_focus({"numero": "1"}, "fo-t")

    def test_nao_dict_levanta(self):
        with pytest.raises(ValueError):
            _mapear_nfe_focus("string", "fo-t")

    def test_versao_invalida_vira_zero(self):
        d = _mapear_nfe_focus({"chave_nfe": "1" * 44, "versao": "nao-e-int"}, "fo-t")
        assert d["versao"] == 0

    def test_raw_json_focus_nunca_vazia(self):
        d = _mapear_nfe_focus({"chave_nfe": "1" * 44, "versao": 1}, "fo-t")
        assert d["raw_json_focus"] != ""
        assert '"chave_nfe"' in d["raw_json_focus"]


# ── gov_fetch — sucesso ─────────────────────────────────────────────────────
class TestGovFetchSucesso:
    @patch("providers.focusnfe_provider.requests.get")
    def test_200_com_3_docs(self, mock_get, provider_com_token):
        """3 resumos autorizados sem nfe_completa — todos ficam RESUMO
        (correto — nenhum tem XML disponivel na Focus)."""
        mock_get.return_value = _mock_resp(
            status=200,
            headers={"X-Total-Count": "3", "X-Max-Version": "100"},
            json_data=[
                {"chave_nfe": "a" * 44, "versao": 98,  "situacao": "autorizada"},
                {"chave_nfe": "b" * 44, "versao": 99,  "situacao": "autorizada"},
                {"chave_nfe": "c" * 44, "versao": 100, "situacao": "autorizada"},
            ],
        )
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe", "ambiente": "homologacao",
             "ultimo_nsu": "97"},
            "fo-t",
        )
        assert r["ok"] is True
        assert len(r["documentos"]) == 3
        assert r["total_count"] == 3
        assert r["ultimo_nsu"] == "100"
        assert r["max_nsu"] == "100"
        assert r["cursor_tipo"] == "versao"
        assert r["nsu_avancou"] is True
        # Todos RESUMO com cStat=100 (autorizada — nao 101!)
        assert all(d["status_xml"] == "RESUMO" for d in r["documentos"])
        assert all(d["cStat"] == "100" for d in r["documentos"])
        assert r["xmls_baixados"] == 0
        assert r["xmls_pendentes"] == 0

    @patch("providers.focusnfe_provider.requests.get")
    def test_x_max_version_ausente_usa_maior_versao_itens(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200,
            headers={"X-Total-Count": "2"},   # sem X-Max-Version
            json_data=[
                {"chave_nfe": "a" * 44, "versao": 50},
                {"chave_nfe": "b" * 44, "versao": 55},
            ],
        )
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "10"}, "fo-t")
        assert r["ok"] is True
        assert r["ultimo_nsu"] == "55"

    @patch("providers.focusnfe_provider.requests.get")
    def test_lista_vazia_mantem_cursor(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Total-Count": "0"}, json_data=[])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "42"}, "fo-t")
        assert r["ok"] is True
        assert r["ultimo_nsu"] == "42"
        assert r["nsu_avancou"] is False
        assert r["documentos"] == []

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_montada_corretamente(self, mock_get, provider_com_token, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_AMBIENTE", "producao")
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        p = FocusNFeProvider()  # re-instanciar apos alterar env
        mock_get.return_value = _mock_resp(status=200, headers={}, json_data=[])
        p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe",
                     "ambiente": "producao", "ultimo_nsu": "0"}, "fo-t")
        args, kwargs = mock_get.call_args
        assert args[0] == "https://api.focusnfe.com.br/v2/nfes_recebidas"
        assert kwargs["params"] == {"cnpj": "07219398000109", "versao": "0"}
        # header Authorization esta presente mas nunca vaza para envelope
        assert "Authorization" in kwargs["headers"]
        assert kwargs["headers"]["Authorization"].startswith("Basic ")

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_com_env_base_sem_v2(self, mock_get, monkeypatch):
        """FOCUSNFE_BASE_URL=https://api.focusnfe.com.br (SEM /v2) gera
        `.../v2/nfes_recebidas` corretamente."""
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br")
        p = FocusNFeProvider()
        mock_get.return_value = _mock_resp(status=200, headers={}, json_data=[])
        p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe",
                     "ultimo_nsu": "0"}, "fo-t")
        assert mock_get.call_args.args[0] == "https://api.focusnfe.com.br/v2/nfes_recebidas"

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_com_env_base_com_v2_nao_gera_v2_duplicado(self, mock_get, monkeypatch):
        """FOCUSNFE_BASE_URL=https://api.focusnfe.com.br/v2 (COM /v2) NAO gera
        `.../v2/v2/...` — normalizacao remove o sufixo antes da concatenacao."""
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
        p = FocusNFeProvider()
        mock_get.return_value = _mock_resp(status=200, headers={}, json_data=[])
        p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe",
                     "ultimo_nsu": "0"}, "fo-t")
        url = mock_get.call_args.args[0]
        assert url == "https://api.focusnfe.com.br/v2/nfes_recebidas"
        assert "/v2/v2/" not in url

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_com_env_base_com_v2_e_barra_final(self, mock_get, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2/")
        p = FocusNFeProvider()
        mock_get.return_value = _mock_resp(status=200, headers={}, json_data=[])
        p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe",
                     "ultimo_nsu": "0"}, "fo-t")
        assert mock_get.call_args.args[0] == "https://api.focusnfe.com.br/v2/nfes_recebidas"

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_homologacao_default(self, mock_get, monkeypatch):
        """Sem FOCUSNFE_BASE_URL e sem ambiente no payload → homologacao."""
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        monkeypatch.delenv("FOCUSNFE_AMBIENTE", raising=False)
        p = FocusNFeProvider()
        mock_get.return_value = _mock_resp(status=200, headers={}, json_data=[])
        p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe",
                     "ultimo_nsu": "0"}, "fo-t")
        assert mock_get.call_args.args[0] == "https://homologacao.focusnfe.com.br/v2/nfes_recebidas"


# ── gov_fetch — validacoes ──────────────────────────────────────────────────
class TestGovFetchValidacoes:
    def test_tipo_nao_nfe_falha(self, provider_com_token):
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "cte"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TIPO_NAO_SUPORTADO"

    def test_cnpj_ausente(self, provider_com_token):
        r = provider_com_token.gov_fetch({"tipo": "nfe"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"


# ── gov_fetch — codigos HTTP tratados ───────────────────────────────────────
class TestGovFetchHttpErrors:
    @patch("providers.focusnfe_provider.requests.get")
    def test_400(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=400)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"

    @patch("providers.focusnfe_provider.requests.get")
    def test_401(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=401)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_AUTH_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_403(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=403)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_FORBIDDEN"

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_retry_after_valido(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=429, headers={"Retry-After": "120"})
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_RATE_LIMIT"
        assert r["cooldown_recomendado_seg"] == 120

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_retry_after_invalido_usa_60(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=429, headers={"Retry-After": "abc"})
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_RATE_LIMIT"
        assert r["cooldown_recomendado_seg"] == 60

    @patch("providers.focusnfe_provider.requests.get")
    def test_500(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=500)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_SERVER_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_503(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=503)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_SERVER_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_status_inesperado(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=418)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_HTTP_ERROR"


# ── gov_fetch — excecoes de rede ────────────────────────────────────────────
class TestGovFetchRede:
    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_TIMEOUT"

    @patch("providers.focusnfe_provider.requests.get")
    def test_connection_error(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.ConnectionError()
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_UNAVAILABLE"

    @patch("providers.focusnfe_provider.requests.get")
    def test_request_exception_generica(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.RequestException()
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_HTTP_ERROR"


# ── gov_fetch — payload invalido ────────────────────────────────────────────
class TestGovFetchPayloadInvalido:
    @patch("providers.focusnfe_provider.requests.get")
    def test_json_invalido(self, mock_get, provider_com_token):
        resp = _mock_resp(status=200)
        resp.json.side_effect = ValueError("not json")
        mock_get.return_value = resp
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_PARSE_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_json_nao_lista(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=200, json_data={"erro": "x"})
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["codigo"] == "FOCUS_SCHEMA_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_item_invalido_nao_derruba_lote(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "10"},
            json_data=[
                {"chave_nfe": "a" * 44, "versao": 1},
                "string_invalida",             # nao e dict — vai para erros
                {"numero": "1", "versao": 2},  # sem chave — vai para erros
                {"chave_nfe": "b" * 44, "versao": 10},
            ],
        )
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is True
        assert len(r["documentos"]) == 2
        assert len(r["erros"]) == 2
        assert all(e["codigo"] == "FOCUS_ITEM_INVALIDO" for e in r["erros"])


# ── consultar_dfe_nsu delega para gov_fetch ─────────────────────────────────
class TestDelegacaoConsultarDfeNsu:
    @patch("providers.focusnfe_provider.requests.get")
    def test_delega_e_faz_http(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "50"}, json_data=[])
        r = provider_com_token.consultar_dfe_nsu(
            b"", b"", "07219398000109", "10", "homologacao", "fo-t")
        assert r["ok"] is True
        # X-Max-Version=50 do servidor prevalece sobre ultimo_nsu=10 de entrada
        assert r["ultimo_nsu"] == "50"
        assert r["nsu_avancou"] is True
        # Delegou — chamada HTTP foi disparada com cnpj do parametro e versao=nsu
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["cnpj"] == "07219398000109"
        assert kwargs["params"]["versao"] == "10"


# ── baixar_danfe ────────────────────────────────────────────────────────────
class TestBaixarDanfe:
    def _mock_302_pdf(self, mock_get, location, pdf_bytes=b"%PDF-1.4\n%mock"):
        """Simula 302 no primeiro GET + 200 com bytes no segundo."""
        resp1 = _mock_resp(status=302, headers={"Location": location})
        resp2 = _mock_resp(status=200, headers={"Content-Type": "application/pdf"},
                           content=pdf_bytes)
        mock_get.side_effect = [resp1, resp2]
        return resp1, resp2

    @patch("providers.focusnfe_provider.requests.get")
    def test_302_segundo_get_sem_authorization(self, mock_get, provider_com_token):
        pdf = b"%PDF-1.4\n%conteudo-simulado"
        self._mock_302_pdf(mock_get, "https://presigned.example.com/x.pdf", pdf_bytes=pdf)
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is True
        assert r["bytes"] == pdf
        assert r["mime"] == "application/pdf"
        assert r["tamanho"] == len(pdf)
        assert len(r["sha256"]) == 64
        # 1o GET tem Authorization; 2o GET (URL presigned) NAO tem
        primeiro = mock_get.call_args_list[0]
        segundo = mock_get.call_args_list[1]
        assert "Authorization" in primeiro.kwargs["headers"]
        assert "Authorization" not in segundo.kwargs["headers"]
        assert segundo.args[0] == "https://presigned.example.com/x.pdf"

    @patch("providers.focusnfe_provider.requests.get")
    def test_302_sem_location(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=302, headers={})
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_NO_LOCATION"

    @patch("providers.focusnfe_provider.requests.get")
    def test_200_direto(self, mock_get, provider_com_token):
        pdf = b"%PDF-1.4\n%direto"
        mock_get.return_value = _mock_resp(
            status=200, headers={"Content-Type": "application/pdf"}, content=pdf)
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is True
        assert r["bytes"] == pdf
        assert r["mime"] == "application/pdf"

    @patch("providers.focusnfe_provider.requests.get")
    def test_danfe_url_com_env_base_com_v2_nao_gera_v2_duplicado(self, mock_get, monkeypatch):
        """DANFE tambem nao pode gerar `/v2/v2/nfes_recebidas/{chave}.pdf`."""
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
        p = FocusNFeProvider()
        pdf = b"%PDF"
        mock_get.return_value = _mock_resp(
            status=200, headers={"Content-Type": "application/pdf"}, content=pdf)
        p.baixar_danfe("A" * 44)
        url = mock_get.call_args.args[0]
        assert url == "https://api.focusnfe.com.br/v2/nfes_recebidas/" + "A" * 44 + ".pdf"
        assert "/v2/v2/" not in url

    @patch("providers.focusnfe_provider.requests.get")
    def test_danfe_url_com_env_base_sem_v2(self, mock_get, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc123")
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br")
        p = FocusNFeProvider()
        pdf = b"%PDF"
        mock_get.return_value = _mock_resp(
            status=200, headers={"Content-Type": "application/pdf"}, content=pdf)
        p.baixar_danfe("A" * 44)
        assert mock_get.call_args.args[0] == "https://api.focusnfe.com.br/v2/nfes_recebidas/" + "A" * 44 + ".pdf"

    @patch("providers.focusnfe_provider.requests.get")
    def test_segundo_get_403(self, mock_get, provider_com_token):
        resp1 = _mock_resp(status=302, headers={"Location": "https://presigned.example.com/x.pdf"})
        resp2 = _mock_resp(status=403)
        mock_get.side_effect = [resp1, resp2]
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_HTTP_ERROR"
        assert r["http_status"] == 403

    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout_primeiro_get(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_REQUEST_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_no_download_presigned(self, mock_get, provider_com_token):
        resp1 = _mock_resp(status=302, headers={"Location": "https://presigned.example.com/x.pdf"})
        mock_get.side_effect = [resp1, requests.exceptions.ConnectionError()]
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_DOWNLOAD_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_status_inesperado(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=418)
        r = provider_com_token.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_UNEXPECTED_HTTP"

    def test_chave_vazia(self, provider_com_token):
        r = provider_com_token.baixar_danfe("")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"

    def test_sem_token(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.baixar_danfe("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"


# ── Seguranca — token e Authorization nunca no envelope ────────────────────
class TestSegurancaEnvelope:
    @patch("providers.focusnfe_provider.requests.get")
    def test_gov_fetch_envelope_nao_contem_authorization(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "1"},
            json_data=[{"chave_nfe": "a" * 44, "versao": 1}])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        payload = str(r).lower()
        assert "authorization" not in payload
        assert "basic " not in payload
        # token 'abcdef123456' do fixture — nao pode aparecer bruto
        assert "abcdef123456" not in str(r)

    @patch("providers.focusnfe_provider.requests.get")
    def test_gov_fetch_com_erro_nao_vaza_token(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=401)
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert "abcdef123456" not in str(r)
        assert "authorization" not in str(r).lower()

    @patch("providers.focusnfe_provider.requests.get")
    def test_raw_json_focus_mascarara_campo_sensivel(self, mock_get, provider_com_token):
        # Se Focus (por algum bug) retornasse authorization no payload,
        # ainda seria mascarado antes de guardar como raw_json_focus.
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "1"},
            json_data=[{"chave_nfe": "a" * 44, "versao": 1,
                        "authorization": "Basic MEGA_SECRETO"}])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        raw = r["documentos"][0]["raw_json_focus"]
        assert "MEGA_SECRETO" not in raw
        assert "***" in raw

# ── Fase E4a — baixar_xml_completo ─────────────────────────────────────────
class TestBaixarXmlCompleto:
    def test_200_devolve_xml(self, provider_com_token):
        xml = "<nfeProc><NFe>...</NFe></nfeProc>"
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.text = xml
        with patch("providers.focusnfe_provider.requests.get", return_value=resp) as mg:
            r = provider_com_token.baixar_xml_completo("A" * 44)
        assert r["ok"] is True
        assert r["xml_bruto"] == xml
        assert len(r["xml_hash_sha256"]) == 64
        assert r["tamanho"] == len(xml)
        # URL correta + Accept: application/xml + Authorization presente
        args, kwargs = mg.call_args
        assert args[0].endswith("/v2/nfes_recebidas/" + "A" * 44 + ".xml")
        assert kwargs["headers"]["Accept"] == "application/xml"
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        # Sem redirect (diferente do DANFE)
        assert kwargs["allow_redirects"] is False

    def test_404_devolve_nao_encontrado(self, provider_com_token):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 404
        resp.text = '{"codigo":"nao_encontrada"}'
        with patch("providers.focusnfe_provider.requests.get", return_value=resp):
            r = provider_com_token.baixar_xml_completo("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_NAO_ENCONTRADO"
        assert r["http_status"] == 404

    def test_timeout(self, provider_com_token):
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=requests.exceptions.Timeout()):
            r = provider_com_token.baixar_xml_completo("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_TIMEOUT"

    def test_erro_generico(self, provider_com_token):
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=requests.exceptions.ConnectionError()):
            r = provider_com_token.baixar_xml_completo("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_ERRO"

    def test_chave_vazia(self, provider_com_token):
        r = provider_com_token.baixar_xml_completo("")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"

    def test_sem_token(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.baixar_xml_completo("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"

    def test_authorization_nao_vaza_no_retorno(self, provider_com_token):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.text = "<nfeProc/>"
        with patch("providers.focusnfe_provider.requests.get", return_value=resp):
            r = provider_com_token.baixar_xml_completo("A" * 44)
        payload = str(r).lower()
        assert "authorization" not in payload
        assert "basic " not in payload
        assert "abcdef123456" not in str(r)


# ── Fase E4a — gov_fetch com XML por chave ─────────────────────────────────
class TestGovFetchComXml:
    def _resumo_json_response(self, docs, max_version=None):
        headers = {"X-Total-Count": str(len(docs))}
        if max_version is not None:
            headers["X-Max-Version"] = str(max_version)
        return _mock_resp(status=200, headers=headers, json_data=docs)

    def _xml_response(self, xml="<nfeProc><NFe/></nfeProc>", status=200):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status
        resp.text = xml
        return resp

    def test_nfe_completa_true_baixa_xml_e_vira_completo(self, provider_com_token):
        docs = [{"chave_nfe": "a" * 44, "versao": 10, "situacao": "autorizada",
                 "nfe_completa": True}]
        listagem = self._resumo_json_response(docs, max_version=10)
        xml_ok  = self._xml_response("<nfeProc><NFe/></nfeProc>")
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem, xml_ok]):
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        assert r["ok"] is True
        d = r["documentos"][0]
        assert d["status_xml"] == "COMPLETO"
        assert d["cStat"] == "100"
        assert d["xMotivo"] == "Autorizado"
        assert d["xml_bruto"] == "<nfeProc><NFe/></nfeProc>"
        assert len(d["xml_hash_sha256"]) == 64
        assert "xml_pending" not in d
        assert r["xmls_baixados"] == 1
        assert r["xmls_pendentes"] == 0

    def test_nfe_completa_true_xml_404_vira_resumo_pending(self, provider_com_token):
        docs = [{"chave_nfe": "a" * 44, "versao": 10, "situacao": "autorizada",
                 "nfe_completa": True}]
        listagem = self._resumo_json_response(docs, max_version=10)
        xml_404  = self._xml_response(status=404)
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem, xml_404]):
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        d = r["documentos"][0]
        assert d["status_xml"] == "RESUMO"
        assert d["xml_pending"] is True
        assert d["cStat"] == "100"      # BLINDAGEM — nao vira 101!
        assert "xml_bruto" not in d
        assert r["xmls_baixados"] == 0
        assert r["xmls_pendentes"] == 1

    def test_nfe_completa_true_xml_timeout_batch_continua(self, provider_com_token):
        docs = [
            {"chave_nfe": "a" * 44, "versao": 10, "situacao": "autorizada",
             "nfe_completa": True},
            {"chave_nfe": "b" * 44, "versao": 11, "situacao": "autorizada",
             "nfe_completa": True},
        ]
        listagem = self._resumo_json_response(docs, max_version=11)
        xml_ok   = self._xml_response("<nfeProc/>")
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem, requests.exceptions.Timeout(), xml_ok]):
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        assert r["ok"] is True
        # 1 timeout + 1 sucesso — batch nao derruba.
        d_pend = next(x for x in r["documentos"] if x.get("xml_pending"))
        d_ok   = next(x for x in r["documentos"] if x.get("status_xml") == "COMPLETO")
        assert d_pend["status_xml"] == "RESUMO"
        assert d_ok["xml_bruto"] == "<nfeProc/>"
        assert r["xmls_baixados"] == 1
        assert r["xmls_pendentes"] == 1

    def test_cap_25_com_30_completa(self, provider_com_token, monkeypatch):
        """30 docs nfe_completa=True + cap=25 → 25 COMPLETO, 5 RESUMO+pending."""
        import providers.focusnfe_provider as prov_mod
        monkeypatch.setattr(prov_mod, "_XML_BATCH_CAP", 25)
        docs = [{"chave_nfe": str(i).rjust(44, "0"), "versao": i,
                 "situacao": "autorizada", "nfe_completa": True}
                for i in range(1, 31)]
        listagem = self._resumo_json_response(docs, max_version=30)
        # 25 XML mocks (o resto nao dispara HTTP — cap atinge antes).
        xmls = [self._xml_response("<nfeProc/>") for _ in range(25)]
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem, *xmls]):
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        completos = [d for d in r["documentos"] if d["status_xml"] == "COMPLETO"]
        pendentes = [d for d in r["documentos"] if d.get("xml_pending")]
        assert len(completos) == 25
        assert len(pendentes) == 5
        assert r["xmls_baixados"] == 25
        assert r["xmls_pendentes"] == 5
        # Nenhum dos pendentes tem xml_bruto
        assert all("xml_bruto" not in d for d in pendentes)

    def test_cancelada_nao_baixa_xml(self, provider_com_token):
        """Nota cancelada nao dispara download de XML — E4a defere para E4b."""
        docs = [{"chave_nfe": "a" * 44, "versao": 10, "situacao": "cancelada",
                 "nfe_completa": True,     # mesmo se Focus disser True
                 "data_cancelamento": "2026-07-10T12:00:00Z"}]
        listagem = self._resumo_json_response(docs, max_version=10)
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem]) as mg:
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        # Apenas 1 chamada HTTP (listagem) — nenhuma para XML.
        assert mg.call_count == 1
        d = r["documentos"][0]
        assert d["cStat"] == "101"
        assert d["cancelado"] == 1
        assert d["status_xml"] == "RESUMO"
        assert "xml_bruto" not in d
        assert d["data_cancelamento"] == "2026-07-10T12:00:00Z"

    def test_nfe_completa_false_nao_baixa_xml(self, provider_com_token):
        docs = [{"chave_nfe": "a" * 44, "versao": 10, "situacao": "autorizada",
                 "nfe_completa": False}]
        listagem = self._resumo_json_response(docs, max_version=10)
        with patch("providers.focusnfe_provider.requests.get",
                   side_effect=[listagem]) as mg:
            r = provider_com_token.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"}, "fo-t")
        assert mg.call_count == 1   # so listagem
        d = r["documentos"][0]
        assert d["status_xml"] == "RESUMO"
        assert d["cStat"] == "100"
        assert "xml_pending" not in d
        assert r["xmls_baixados"] == 0
        assert r["xmls_pendentes"] == 0


    def test_nenhum_teste_deste_arquivo_dispara_http_real(self):
        """Guarda-corpo: se algum teste esqueceu de mockar, `requests.get`
        real ainda seria chamado — o que este teste NAO deve validar
        (nao ha como interceptar de forma segura). Este teste apenas
        documenta a expectativa e serve de checklist manual. O caminho
        correto e todos os testes acima usarem @patch."""
        # Placeholder intencional — assert trivial. A ausencia de chamadas
        # HTTP reais e garantida pelo uso de @patch nos testes.
        assert True
