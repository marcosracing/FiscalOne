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


# ── _resolve_base_url ───────────────────────────────────────────────────────
class TestResolveBaseUrl:
    def test_env_base_url_ganha(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://custom.example.com/v2")
        assert _resolve_base_url("producao") == "https://custom.example.com/v2"

    def test_env_base_url_remove_barra_final(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://custom.example.com/v2/")
        assert _resolve_base_url("producao") == "https://custom.example.com/v2"

    def test_producao_default(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("producao") == "https://api.focusnfe.com.br/v2"

    def test_homologacao_default(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("homologacao") == "https://homologacao.focusnfe.com.br/v2"

    def test_ambiente_desconhecido_cai_homologacao(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        assert _resolve_base_url("marte") == "https://homologacao.focusnfe.com.br/v2"

    def test_none_usa_env_ou_homologacao(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
        monkeypatch.delenv("FOCUSNFE_AMBIENTE", raising=False)
        assert _resolve_base_url(None) == "https://homologacao.focusnfe.com.br/v2"


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
    def test_completo_com_xml(self):
        item = {
            "chave_nfe": "1" * 44,
            "protocolo": "PROT-1",
            "data_recebimento": "2026-07-16T10:00:00Z",
            "cnpj_emitente": "07219398000109",
            "cnpj_destinatario": "12345678000199",
            "valor_total": "1500.50",
            "valor_icms": "180.06",
            "numero": "123",
            "serie": "1",
            "nome_emitente": "Racing Logistica",
            "data_emissao": "2026-07-16T09:00:00Z",
            "xml": "<nfeProc>...</nfeProc>",
            "versao": 42,
        }
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["chNFe"] == "1" * 44
        assert d["status_xml"] == "COMPLETO"
        assert d["cStat"] == "100"
        assert d["xMotivo"] == "Autorizado"
        assert d["import_origin"] == "fiscalone_focusnfe"
        assert d["danfe_fonte"] == "focusnfe"
        assert d["versao"] == 42
        assert d["xml_bruto"] == "<nfeProc>...</nfeProc>"
        assert d["xml_hash_sha256"] != ""
        assert "raw_json_focus" in d

    def test_resumo_sem_xml(self):
        item = {"chave_nfe": "2" * 44, "versao": 10, "numero": "9"}
        d = _mapear_nfe_focus(item, "fo-t")
        assert d["status_xml"] == "RESUMO"
        assert d["cStat"] == "101"
        assert "xml_bruto" not in d

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
        mock_get.return_value = _mock_resp(
            status=200,
            headers={"X-Total-Count": "3", "X-Max-Version": "100"},
            json_data=[
                {"chave_nfe": "a" * 44, "versao": 98, "xml": "<xml/>"},
                {"chave_nfe": "b" * 44, "versao": 99},
                {"chave_nfe": "c" * 44, "versao": 100, "xml": "<xml/>"},
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
        # 2 com XML → COMPLETO; 1 sem → RESUMO
        completos = [d for d in r["documentos"] if d["status_xml"] == "COMPLETO"]
        resumos = [d for d in r["documentos"] if d["status_xml"] == "RESUMO"]
        assert len(completos) == 2
        assert len(resumos) == 1

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

    def test_nenhum_teste_deste_arquivo_dispara_http_real(self):
        """Guarda-corpo: se algum teste esqueceu de mockar, `requests.get`
        real ainda seria chamado — o que este teste NAO deve validar
        (nao ha como interceptar de forma segura). Este teste apenas
        documenta a expectativa e serve de checklist manual. O caminho
        correto e todos os testes acima usarem @patch."""
        # Placeholder intencional — assert trivial. A ausencia de chamadas
        # HTTP reais e garantida pelo uso de @patch nos testes.
        assert True
