"""G0.2a — Recuperacao individual segura de XML por chave (ADR-0049).

Zero HTTP real. Zero banco. Zero disco. Todo teste mockado.

Categorias:
- REGRESSIVO      — falha contra d8d832f7 (rota/metodo/campo inexistente).
- NOVA_CAPACIDADE — comportamento novo introduzido por G0.2a.
- PRESERVACAO     — invariantes de fase 1 (emissao bloqueada, sem eventos etc.)

Cada regressivo/nova-capacidade DEVE falhar contra o baseline `d8d832f7`.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import app as fiscalone_app
from providers.focusnfe_provider import FocusNFeProvider


# ── Utilitarios ──────────────────────────────────────────────────────────────

def _mock_resp(status=200, headers=None, content=b"", text=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.content = content
    resp.text = text if text is not None else (
        content.decode("utf-8", errors="replace") if isinstance(content, (bytes, bytearray)) else ""
    )
    resp.json.side_effect = ValueError("not json")
    return resp


def _dv(digitos_43: str) -> str:
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(ch) * pesos[i % 8] for i, ch in enumerate(reversed(digitos_43)))
    resto = soma % 11
    dv = 11 - resto
    return "0" if dv in (10, 11) else str(dv)


def _chave_valida(prefixo_43: str = "35240107219398000109550010000000011000000001") -> str:
    prefixo_43 = prefixo_43[:43].ljust(43, "0")
    return prefixo_43 + _dv(prefixo_43)


CHAVE_NFE_OK = _chave_valida("35240107219398000109550010000000011000000001")
CHAVE_CTE_OK = _chave_valida("35240107219398000109570010000000011000000001")
IDENT_NFSE   = "NFSE-2026-000012345"
M2M_TOKEN    = "srvtok-abcdef1234567890"


@pytest.fixture
def m2m_env(monkeypatch):
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M_TOKEN)
    monkeypatch.setenv("FOCUSNFE_TOKEN", "focus-abc-123")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)


@pytest.fixture
def client(m2m_env):
    fiscalone_app.app.config["TESTING"] = True
    return fiscalone_app.app.test_client()


def _post_por_chave(client, body=None, headers=None, extra_headers=None):
    hdr = {
        "X-RLogix-Service-Token": M2M_TOKEN,
        "X-Source-System":        "mapone-test",
        "Content-Type":           "application/json",
    }
    if extra_headers:
        hdr.update(extra_headers)
    if headers is not None:
        hdr = headers
    return client.post("/fiscal/xml/por-chave",
                       data=json.dumps(body or {}), headers=hdr)


def _body_focus(doc_type, ident, provider="focusnfe", ambiente="homologacao",
                focusnfe_token="focus-abc-123"):
    return {
        "doc_type":      doc_type,
        "identificador": ident,
        "provider":      provider,
        "ambiente":      ambiente,
        "focusnfe_token": focusnfe_token,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER — 11 testes (helper + metodos por tipo)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "focus-abc-123")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


class TestProviderBytesPorChave:
    """NOVA_CAPACIDADE — baixar_xml_bytes_por_chave/CT-e nao existem no baseline."""

    # 1. NF-e — path plural + bytes identicos
    @patch("providers.focusnfe_provider.requests.get")
    def test_nfe_usa_path_plural_e_bytes_identicos(self, mock_get, provider):
        bytes_up = b"<nfeProc><NFe>abc</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=bytes_up)
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is True
        assert r["xml_bytes"] == bytes_up
        url = mock_get.call_args.args[0]
        assert f"/v2/nfes_recebidas/{CHAVE_NFE_OK}.xml" in url

    # 2. NFSe — path plural + identificador escapado (rota escapa antes; aqui verificamos que o path e plural)
    @patch("providers.focusnfe_provider.requests.get")
    def test_nfse_usa_path_plural_e_identificador_escapado(self, mock_get, provider):
        bytes_up = b"<CompNfse><Nfse/></CompNfse>"
        mock_get.return_value = _mock_resp(status=200, content=bytes_up)
        # Rota fornece ident ja escapado; aqui simulamos com quote inline
        import urllib.parse as _u
        ident_esc = _u.quote(IDENT_NFSE, safe="")
        r = provider.baixar_xml_bytes_por_chave("nfse", ident_esc, "homologacao")
        assert r["ok"] is True
        assert r["xml_bytes"] == bytes_up
        url = mock_get.call_args.args[0]
        assert f"/v2/nfsens_recebidas/{ident_esc}.xml" in url

    # 3. CT-e — path plural + bytes identicos
    @patch("providers.focusnfe_provider.requests.get")
    def test_cte_usa_path_plural_e_bytes_identicos(self, mock_get, provider):
        bytes_up = b"<cteProc><CTe/></cteProc>"
        mock_get.return_value = _mock_resp(status=200, content=bytes_up)
        r = provider.baixar_xml_cte_por_chave(CHAVE_CTE_OK, "producao")
        assert r["ok"] is True
        assert r["xml_bytes"] == bytes_up
        url = mock_get.call_args.args[0]
        assert f"/v2/ctes_recebidas/{CHAVE_CTE_OK}.xml" in url

    # 4. SHA-256 sobre bytes upstream (nao sobre str reencodada)
    @patch("providers.focusnfe_provider.requests.get")
    def test_sha256_calculado_sobre_bytes_upstream(self, mock_get, provider):
        import hashlib
        bytes_up = b"\xff\xfe<nfeProc><NFe>\xc3\xa1\xc3\xa9</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=bytes_up)
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is True
        assert r["xml_hash_sha256"] == hashlib.sha256(bytes_up).hexdigest()

    # 5. Bytes nao-UTF-8 permanecem byte-identicos
    @patch("providers.focusnfe_provider.requests.get")
    def test_bytes_nao_utf8_permanecem_identicos(self, mock_get, provider):
        # ISO-8859-1 encoded content with non-ASCII bytes.
        bytes_up = "<nfeProc>ção</nfeProc>".encode("latin-1")
        mock_get.return_value = _mock_resp(status=200, content=bytes_up)
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is True
        assert r["xml_bytes"] == bytes_up
        assert r["tamanho"] == len(bytes_up)

    # 6. Upstream 401/403 → FOCUS_XML_AUTH_ERROR (sem token no erro)
    @patch("providers.focusnfe_provider.requests.get")
    def test_401_403_upstream_traduz_auth_error(self, mock_get, provider):
        for status in (401, 403):
            mock_get.return_value = _mock_resp(status=status, content=b"denied")
            r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
            assert r["ok"] is False
            assert r["codigo"] == "FOCUS_XML_AUTH_ERROR"
            assert "focus-abc-123" not in json.dumps(r)
            assert "Authorization" not in json.dumps(r)

    # 7. 404 upstream → FOCUS_XML_NAO_ENCONTRADO
    @patch("providers.focusnfe_provider.requests.get")
    def test_404_upstream(self, mock_get, provider):
        mock_get.return_value = _mock_resp(status=404, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_NAO_ENCONTRADO"

    # 8. Timeout upstream
    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout_upstream(self, mock_get, provider):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_TIMEOUT"

    # 9. 429 upstream → rate limit
    @patch("providers.focusnfe_provider.requests.get")
    def test_429_rate_limit(self, mock_get, provider):
        mock_get.return_value = _mock_resp(status=429, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_RATE_LIMIT"

    # 10. 5xx upstream → indisponibilidade
    @patch("providers.focusnfe_provider.requests.get")
    def test_5xx_upstream_unavailable(self, mock_get, provider):
        for status in (500, 502, 503, 504):
            mock_get.return_value = _mock_resp(status=status, content=b"")
            r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
            assert r["ok"] is False
            assert r["codigo"] == "FOCUS_XML_UPSTREAM_UNAVAILABLE"
            assert r["http_status"] == status

    # 11. Helper com redirect: segundo GET SEM Authorization
    @patch("providers.focusnfe_provider.requests.get")
    def test_redirect_nunca_envia_auth_no_segundo_get(self, mock_get, provider,
                                                       monkeypatch):
        monkeypatch.setenv("FISCALONE_XML_REDIRECT_HOSTS", "presigned")
        resp1 = _mock_resp(status=302, headers={"Location": "https://presigned/x.xml"})
        resp2 = _mock_resp(status=200, content=b"<CompNfse/>")
        mock_get.side_effect = [resp1, resp2]
        r = provider._http_get_xml_bytes_upstream(
            "https://focusnfe/.../teste.xml", permitir_redirect=True)
        assert r["ok"] is True
        assert r["xml_bytes"] == b"<CompNfse/>"
        # 1o request COM Auth; 2o SEM Auth
        primeiro, segundo = mock_get.call_args_list[0], mock_get.call_args_list[1]
        assert "Authorization" in primeiro.kwargs["headers"]
        assert "Authorization" not in segundo.kwargs["headers"]

    @pytest.mark.parametrize("location", [
        "http://storage.example/x.xml",
        "https://usuario:senha@storage.example/x.xml",
        "https://storage.example:8443/x.xml",
        "https://storage.example/x.xml#fragmento",
        "https://nao-autorizado.example/x.xml",
        "/x.xml",
    ])
    @patch("providers.focusnfe_provider.requests.get")
    def test_redirect_inseguro_ou_nao_autorizado_falha_fechado(
            self, mock_get, location, provider):
        mock_get.return_value = _mock_resp(
            status=302, headers={"Location": location})
        r = provider._http_get_xml_bytes_upstream(
            "https://api.focusnfe.com.br/v2/teste.xml",
            permitir_redirect=True,
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_REDIRECT_NAO_PERMITIDO"
        assert mock_get.call_count == 1

    @patch("providers.focusnfe_provider.requests.get")
    def test_redirect_mesmo_host_https_e_permitido_sem_allowlist(
            self, mock_get, provider, monkeypatch):
        monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
        mock_get.side_effect = [
            _mock_resp(
                status=302,
                headers={
                    "Location":
                    "https://api.focusnfe.com.br/storage/x.xml?assinatura=abc"
                },
            ),
            _mock_resp(status=200, content=b"<xml/>"),
        ]
        r = provider._http_get_xml_bytes_upstream(
            "https://api.focusnfe.com.br/v2/teste.xml",
            permitir_redirect=True,
        )
        assert r["ok"] is True
        assert mock_get.call_count == 2
        assert "Authorization" not in mock_get.call_args_list[1].kwargs["headers"]


# ══════════════════════════════════════════════════════════════════════════════
# ROTA — 15 testes (M2M + validacao + execucao + seguranca)
# ══════════════════════════════════════════════════════════════════════════════

class TestRotaXmlPorChave:
    """NOVA_CAPACIDADE — a rota /fiscal/xml/por-chave nao existe no baseline."""

    # 12. M2M ausente/incorreto → 401
    def test_m2m_ausente_401(self, client):
        r = client.post("/fiscal/xml/por-chave",
                        data=json.dumps(_body_focus("nfe", CHAVE_NFE_OK)),
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 401
        j = r.get_json()
        assert j["codigo"] == "M2M_NAO_AUTORIZADO"

    def test_m2m_divergente_401(self, client):
        r = client.post(
            "/fiscal/xml/por-chave",
            data=json.dumps(_body_focus("nfe", CHAVE_NFE_OK)),
            headers={"Content-Type": "application/json",
                     "X-RLogix-Service-Token": "errado-xyz-9999999999999999"},
        )
        assert r.status_code == 401
        assert r.get_json()["codigo"] == "M2M_NAO_AUTORIZADO"

    # 13. M2M nao configurado no servidor → 503
    def test_m2m_nao_configurado_503(self, monkeypatch):
        monkeypatch.delenv("FISCALONE_M2M_TOKEN", raising=False)
        monkeypatch.setenv("FOCUSNFE_TOKEN", "focus-abc-123")
        client = fiscalone_app.app.test_client()
        r = client.post(
            "/fiscal/xml/por-chave",
            data=json.dumps(_body_focus("nfe", CHAVE_NFE_OK)),
            headers={"Content-Type": "application/json",
                     "X-RLogix-Service-Token": "qualquer-coisa-1234567890"},
        )
        assert r.status_code == 503
        assert r.get_json()["codigo"] == "M2M_NAO_CONFIGURADO"

    # 14. M2M correto → processa (NF-e valida ok)
    @patch("providers.focusnfe_provider.requests.get")
    def test_m2m_correto_permite_processamento_nfe(self, mock_get, client):
        xml = b"<nfeProc><NFe>ok</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        assert r.status_code == 200
        assert r.data == xml
        assert r.mimetype == "application/xml"

    # 15. NF-e valida → application/xml + body identico + headers seguros
    @patch("providers.focusnfe_provider.requests.get")
    def test_nfe_valida_body_identico_e_headers(self, mock_get, client):
        import hashlib
        xml = b"<nfeProc><NFe>alfa</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        assert r.status_code == 200
        assert r.data == xml
        assert r.mimetype == "application/xml"
        assert r.headers["X-Trace-Id"]
        assert r.headers["X-RLogix-Provider"] == "focusnfe"
        assert r.headers["X-RLogix-Ambiente"] == "homologacao"
        assert r.headers["X-RLogix-Upstream-Status"] == "200"
        assert r.headers["X-RLogix-Content-SHA256"] == hashlib.sha256(xml).hexdigest()
        assert r.headers["Content-Length"] == str(len(xml))

    @pytest.mark.parametrize("ambiente", ["", "staging", "PROD", "teste"])
    def test_ambiente_ausente_ou_invalido_e_rejeitado(
            self, client, ambiente):
        body = _body_focus("nfe", CHAVE_NFE_OK, ambiente=ambiente)
        with patch("app.get_provider") as get_provider:
            r = _post_por_chave(client, body)
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "AMBIENTE_INVALIDO"
        get_provider.assert_not_called()

    # 16. CT-e valida → body identico
    @patch("providers.focusnfe_provider.requests.get")
    def test_cte_valida_body_identico(self, mock_get, client):
        xml = b"<cteProc><CTe/></cteProc>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("cte", CHAVE_CTE_OK))
        assert r.status_code == 200
        assert r.data == xml
        # URL upstream usa path plural /v2/ctes_recebidas
        url = mock_get.call_args.args[0]
        assert "/v2/ctes_recebidas/" in url

    # 17. NFSe opaca valida → body identico
    @patch("providers.focusnfe_provider.requests.get")
    def test_nfse_opaca_valida_body_identico(self, mock_get, client):
        xml = b"<CompNfse><Nfse/></CompNfse>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfse", IDENT_NFSE))
        assert r.status_code == 200
        assert r.data == xml
        url = mock_get.call_args.args[0]
        assert "/v2/nfsens_recebidas/" in url
        # Identificador foi escapado (nao esta cru na URL sem escape)
        # Caracteres seguros permanecem, mas o path deve ser byte-safe.

    def test_nfse_ident_com_barra_e_escapado(self, client):
        # Nao mocka HTTP — a rota so processa se HTTP for chamado. Vamos mockar.
        xml = b"<CompNfse/>"
        with patch("providers.focusnfe_provider.requests.get",
                   return_value=_mock_resp(status=200, content=xml)) as mg:
            r = _post_por_chave(client, _body_focus("nfse", "NFSE/2026?foo=bar"))
        assert r.status_code == 200
        url = mg.call_args.args[0]
        # '/' e '?' devem estar percent-encoded (%2F e %3F)
        assert "%2F" in url
        assert "%3F" in url
        assert "NFSE/2026?foo=bar" not in url  # nao pode aparecer cru

    def test_nfse_ident_com_control_chars_400(self, client):
        r = _post_por_chave(client, _body_focus("nfse", "abc\x00def"))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "IDENTIFICADOR_INVALIDO"

    def test_nfse_ident_excede_max_400(self, client):
        r = _post_por_chave(client, _body_focus("nfse", "A" * 500))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "IDENTIFICADOR_INVALIDO"

    # 18. doc_type invalido → 400
    def test_doc_type_invalido_400(self, client):
        r = _post_por_chave(client, _body_focus("mdfe", CHAVE_NFE_OK))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "TIPO_NAO_SUPORTADO"

    # 19. Chave NF-e/CT-e invalida (DV)
    def test_chave_nfe_com_dv_errado_400(self, client):
        chave_ruim = CHAVE_NFE_OK[:43] + str((int(CHAVE_NFE_OK[43]) + 1) % 10)
        r = _post_por_chave(client, _body_focus("nfe", chave_ruim))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "CHAVE_DFE_INVALIDA"

    def test_chave_nfe_nao_44_digitos_400(self, client):
        r = _post_por_chave(client, _body_focus("nfe", "123"))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "CHAVE_DFE_INVALIDA"

    def test_chave_cte_com_letra_400(self, client):
        r = _post_por_chave(client, _body_focus("cte", "A" * 44))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "CHAVE_DFE_INVALIDA"

    # 20. provider SEFAZ → 501, SEM chamada ao provider
    def test_provider_sefaz_fail_closed_501(self, client):
        with patch("providers.focusnfe_provider.requests.get") as mg_focus, \
             patch("providers.sefaz_provider.SefazProvider") as SP:
            r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK,
                                                    provider="sefaz"))
        assert r.status_code == 501
        assert r.get_json()["codigo"] == "RECUPERACAO_INDIVIDUAL_SEFAZ_NAO_IMPLEMENTADA"
        # nenhum provider foi instanciado
        assert SP.call_count == 0
        assert mg_focus.call_count == 0

    # 21. provider desconhecido → 400
    def test_provider_desconhecido_400(self, client):
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK,
                                                provider="acme"))
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "PROVIDER_NAO_SUPORTADO"

    # 22. Upstream 404 distinto de credencial
    @patch("providers.focusnfe_provider.requests.get")
    def test_upstream_404_distinto_de_credencial(self, mock_get, client):
        mock_get.return_value = _mock_resp(status=404, content=b"")
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        j = r.get_json()
        assert r.status_code == 404
        assert j["codigo"] == "FOCUS_XML_NAO_ENCONTRADO"
        assert j["codigo"] != "M2M_NAO_AUTORIZADO"
        assert j["codigo"] != "FOCUS_XML_AUTH_ERROR"

    # 23. Segredo ausente em log, exceção, headers e body
    @patch("providers.focusnfe_provider.requests.get")
    def test_segredo_ausente_em_todo_output(self, mock_get, client, capsys):
        xml = b"<nfeProc><NFe>doc</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK,
                                                focusnfe_token="segredo-nao-vaza-9999"))
        assert r.status_code == 200
        # Nenhum header retorna o token
        for k, v in r.headers.items():
            assert "segredo-nao-vaza-9999" not in v
            assert M2M_TOKEN not in v
        # Nenhum segredo em stdout (log estruturado)
        out = capsys.readouterr().out
        assert "segredo-nao-vaza-9999" not in out
        assert M2M_TOKEN not in out
        # Body e o XML upstream, nao o token
        assert b"segredo-nao-vaza-9999" not in r.data
        assert M2M_TOKEN.encode() not in r.data

    # 24. Nenhuma escrita em banco/disco (rota nao importa nada de banco)
    def test_rota_nao_importa_banco(self):
        import inspect
        src = inspect.getsource(fiscalone_app.xml_por_chave)
        assert "sqlite" not in src.lower()
        assert "psycopg" not in src.lower()
        assert "cx_oracle" not in src.lower()
        assert "sqlalchemy" not in src.lower()
        assert "open(" not in src
        assert "write(" not in src

    # 25. Metadados seguros presentes nos headers de sucesso
    @patch("providers.focusnfe_provider.requests.get")
    def test_metadados_headers_seguros(self, mock_get, client):
        xml = b"<nfeProc/>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK,
                                                ambiente="producao"))
        assert r.status_code == 200
        # Nenhum header vaza chave completa, CNPJ, token ou XML.
        for k, v in r.headers.items():
            assert CHAVE_NFE_OK not in v
            assert "focus-abc-123" not in v
        # Metadados seguros presentes:
        assert r.headers["X-RLogix-Provider"] == "focusnfe"
        assert r.headers["X-RLogix-Ambiente"] == "producao"
        assert "X-RLogix-Upstream-Status" in r.headers
        assert "X-RLogix-Content-SHA256" in r.headers
        assert "Content-Length" in r.headers

    # 26. Nenhum conteudo fiscal integral em log
    @patch("providers.focusnfe_provider.requests.get")
    def test_conteudo_fiscal_nao_vai_para_log(self, mock_get, client, capsys):
        xml = b"<nfeProc><NFe>MARCADOR-CONFIDENCIAL-Z9</NFe></nfeProc>"
        mock_get.return_value = _mock_resp(status=200, content=xml)
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        assert r.status_code == 200
        out = capsys.readouterr().out
        assert "MARCADOR-CONFIDENCIAL-Z9" not in out


# ══════════════════════════════════════════════════════════════════════════════
# TRAVAS DE EMISSAO — PRESERVACAO das nove rotas bloqueadas
# ══════════════════════════════════════════════════════════════════════════════

_NOVE_ROTAS_EMISSAO = [
    ("POST",   "/fiscal/nfe"),
    ("DELETE", "/fiscal/nfe/" + CHAVE_NFE_OK),
    ("POST",   "/fiscal/nfe/" + CHAVE_NFE_OK + "/inutilizar"),
    ("POST",   "/fiscal/nfe/" + CHAVE_NFE_OK + "/cce"),
    ("POST",   "/fiscal/cte"),
    ("DELETE", "/fiscal/cte/" + CHAVE_CTE_OK),
    ("POST",   "/fiscal/mdfe"),
    ("POST",   "/fiscal/mdfe/" + CHAVE_CTE_OK + "/encerrar"),
    ("POST",   "/fiscal/mdfe/" + CHAVE_CTE_OK + "/condutor"),
]


class TestPreservacaoEmissao:
    """PRESERVACAO — G0.2a nao pode reabrir as nove rotas de emissao/evento."""

    @pytest.mark.parametrize("metodo,rota", _NOVE_ROTAS_EMISSAO)
    def test_nove_rotas_permanecem_bloqueadas(self, client, metodo, rota):
        r = client.open(rota, method=metodo, data="{}",
                        content_type="application/json")
        # Todas devem responder 403 com codigo EMISSAO_BLOQUEADA.
        assert r.status_code == 403
        j = r.get_json()
        assert j["codigo"] == "EMISSAO_BLOQUEADA"

    def test_rota_nova_nao_aceita_acao_evento(self, client):
        # Payload contem "acao": "manifestar" — rota deve ignorar
        # (chega em CHAVE_DFE_INVALIDA porque payload nao tem chave;
        # nenhum helper de emissao/manifestacao alcancado).
        body = _body_focus("nfe", "123")  # chave curta invalida
        body["acao"] = "manifestar"
        body["tipo"] = "ciencia"
        with patch("providers.focusnfe_provider.FocusNFeProvider.manifestar_nfe_recebida") as m_manif, \
             patch("providers.focusnfe_provider.requests.get") as mg:
            r = _post_por_chave(client, body)
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "CHAVE_DFE_INVALIDA"
        # Nenhum helper de manifestacao/emissao foi chamado.
        assert m_manif.call_count == 0
        assert mg.call_count == 0

    def test_rota_nova_nao_alcanca_manifesto(self, client):
        # doc_type valido mas mesmo com "acao" no body, a rota nao pode invocar
        # nfe_recebida_manifesto (rota distinta e provider distinto).
        body = _body_focus("nfe", CHAVE_NFE_OK)
        body["acao"] = "manifestar"
        with patch("providers.focusnfe_provider.FocusNFeProvider.manifestar_nfe_recebida") as m_manif, \
             patch("providers.focusnfe_provider.requests.get",
                   return_value=_mock_resp(status=200, content=b"<nfeProc/>")):
            r = _post_por_chave(client, body)
        assert r.status_code == 200
        # manifestacao NUNCA foi chamada
        assert m_manif.call_count == 0

    def test_rota_de_manifesto_permanece_com_contrato_proprio(self, client):
        # A rota /fiscal/nfe/recebida/manifesto continua existente e separada.
        # POST vazio → 400 payload invalido (contrato antigo preservado).
        r = client.post("/fiscal/nfe/recebida/manifesto",
                        data="{}", content_type="application/json")
        # Nao deve ser 404 (rota removida) nem 200 (aceite sem chave).
        assert r.status_code in (400, 403)  # 403 se producao bloqueada; 400 payload invalido


# ══════════════════════════════════════════════════════════════════════════════
# REGRESSIVOS — cada teste deve falhar contra d8d832f7
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressivosContraBaseline:
    """REGRESSIVO — provas que a superficie NOVA existe.

    Cada asserção depende de artefato criado em G0.2a. Contra `d8d832f7`,
    todos falham (rota inexistente, metodos inexistentes, campos inexistentes).
    """

    def test_regressivo_rota_xml_por_chave_registrada(self):
        rules = {r.rule for r in fiscalone_app.app.url_map.iter_rules()}
        assert "/fiscal/xml/por-chave" in rules

    def test_regressivo_metodo_provider_baixar_xml_bytes_por_chave(self, provider):
        assert callable(getattr(provider, "baixar_xml_bytes_por_chave", None))

    def test_regressivo_metodo_provider_baixar_xml_cte_por_chave(self, provider):
        assert callable(getattr(provider, "baixar_xml_cte_por_chave", None))

    def test_regressivo_helper_interno_bytes(self, provider):
        assert callable(getattr(provider, "_http_get_xml_bytes_upstream", None))

    def test_regressivo_constante_m2m_env(self):
        assert fiscalone_app._M2M_TOKEN_ENV == "FISCALONE_M2M_TOKEN"

    def test_regressivo_helper_m2m_check(self):
        assert callable(getattr(fiscalone_app, "_m2m_check", None))

    def test_regressivo_dv_chave_dfe(self):
        assert callable(getattr(fiscalone_app, "_chave_dfe_valida", None))
        assert fiscalone_app._chave_dfe_valida(CHAVE_NFE_OK) is True
        # DV errado
        bad = CHAVE_NFE_OK[:43] + str((int(CHAVE_NFE_OK[43]) + 1) % 10)
        assert fiscalone_app._chave_dfe_valida(bad) is False


# ══════════════════════════════════════════════════════════════════════════════
# G0.2a-R2 — Retry-After normalizado (NOVA_CAPACIDADE)
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryAfterR2:
    """G0.2a-R2 — Retry-After propagado como retry_after_seg apenas quando
    inteiro positivo. Header cru NUNCA repassado."""

    def test_parse_retry_after_int_helper(self):
        from providers.focusnfe_provider import _parse_retry_after_int
        assert _parse_retry_after_int(None) is None
        assert _parse_retry_after_int("") is None
        assert _parse_retry_after_int("abc") is None
        assert _parse_retry_after_int("0") is None
        assert _parse_retry_after_int("-5") is None
        # Data HTTP (RFC 7231) nao interpretada nesta fase
        assert _parse_retry_after_int("Fri, 31 Dec 2026 23:59:59 GMT") is None
        assert _parse_retry_after_int("120") == 120
        assert _parse_retry_after_int(" 45 ") == 45
        assert _parse_retry_after_int(30) == 30

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_com_retry_after_valido_propaga_no_provider(self, mock_get, provider):
        mock_get.return_value = _mock_resp(
            status=429, headers={"Retry-After": "180"}, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_RATE_LIMIT"
        assert r["retry_after_seg"] == 180

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_sem_retry_after_nao_inventa(self, mock_get, provider):
        mock_get.return_value = _mock_resp(status=429, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_RATE_LIMIT"
        assert "retry_after_seg" not in r

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_com_retry_after_invalido_ignora(self, mock_get, provider):
        mock_get.return_value = _mock_resp(
            status=429, headers={"Retry-After": "abc"}, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert "retry_after_seg" not in r

    @patch("providers.focusnfe_provider.requests.get")
    def test_503_com_retry_after_propaga(self, mock_get, provider):
        mock_get.return_value = _mock_resp(
            status=503, headers={"Retry-After": "45"}, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        assert r["codigo"] == "FOCUS_XML_UPSTREAM_UNAVAILABLE"
        assert r["retry_after_seg"] == 45

    @patch("providers.focusnfe_provider.requests.get")
    def test_500_nao_carrega_retry_after(self, mock_get, provider):
        mock_get.return_value = _mock_resp(
            status=500, headers={"Retry-After": "30"}, content=b"")
        r = provider.baixar_xml_bytes_por_chave("nfe", CHAVE_NFE_OK, "homologacao")
        # 500 (nao 503) nao le Retry-After — mantem semantica conservadora.
        assert r["codigo"] == "FOCUS_XML_UPSTREAM_UNAVAILABLE"
        assert "retry_after_seg" not in r

    @patch("providers.focusnfe_provider.requests.get")
    def test_rota_propaga_retry_after_no_envelope_de_erro(self, mock_get, client):
        mock_get.return_value = _mock_resp(
            status=429, headers={"Retry-After": "120"}, content=b"")
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        assert r.status_code == 429
        j = r.get_json()
        assert j["codigo"] == "FOCUS_XML_RATE_LIMIT"
        assert j["retry_after_seg"] == 120

    @patch("providers.focusnfe_provider.requests.get")
    def test_rota_nao_repassa_header_cru_retry_after(self, mock_get, client):
        mock_get.return_value = _mock_resp(
            status=429, headers={"Retry-After": "60"}, content=b"")
        r = _post_por_chave(client, _body_focus("nfe", CHAVE_NFE_OK))
        # Header cru "Retry-After" nunca deve estar no response da rota nova.
        assert "Retry-After" not in r.headers
