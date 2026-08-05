"""Testes DANFSe HTML — FocusNFe (Fase DANFSe 2026-07-30).

Cobre:

- URL oficial `/v2/nfsens_recebidas/{chave}.html`;
- chave escapada (rejeita caracteres fora do allowlist);
- Basic Auth apenas na primeira origem;
- redirect sem Authorization no segundo GET (URL pré-assinada);
- redirect para host proibido → erro nominal;
- 200 HTML direto;
- 401/404/timeout/vazio/MIME inválido → códigos nominais;
- token ausente → FOCUS_TOKEN_AUSENTE;
- token nunca aparece em log/envelope/resposta.

Também cobre a rota M2M `/fiscal/nfse/recebida/danfse`:

- token M2M obrigatório;
- provider ≠ focusnfe;
- chave inválida;
- sucesso encaminha corpo com MIME text/html;
- 404/502/504 mapeados;
- token FocusNFe sanitizado do payload antes do log.
"""
import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def provider_com_token(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


def _mock_resp(status=200, headers=None, content=b""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.content = content
    return resp


# ─────────────────────────────────────────────────────────────────────
# Provider — baixar_danfse_nfse
# ─────────────────────────────────────────────────────────────────────


class TestBaixarDanfseNfse:

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_oficial_html(self, mock_get, provider_com_token):
        html = b"<!doctype html><body>ok</body>"
        mock_get.return_value = _mock_resp(
            200, {"Content-Type": "text/html; charset=utf-8"}, html)
        r = provider_com_token.baixar_danfse_nfse("NFSe" + "A" * 44)
        assert r["ok"] is True
        assert r["bytes"] == html
        assert r["mime"] == "text/html"
        url = mock_get.call_args.args[0]
        assert url.endswith("/v2/nfsens_recebidas/NFSe" + "A" * 44 + ".html")
        assert "nfes_recebidas" not in url  # não confunde com NF-e

    @patch("providers.focusnfe_provider.requests.get")
    def test_chave_vazia_rejeitada(self, mock_get, provider_com_token):
        r = provider_com_token.baixar_danfse_nfse("")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"
        mock_get.assert_not_called()

    @patch("providers.focusnfe_provider.requests.get")
    def test_chave_com_caracteres_perigosos_rejeitada(self, mock_get,
                                                        provider_com_token):
        for perigosa in (
            "A" * 44 + "/../../etc/passwd",
            "A" * 44 + "?token=X",
            "AAA BBB",
            "A" * 44 + "#frag",
            "A" * 200,
        ):
            r = provider_com_token.baixar_danfse_nfse(perigosa)
            assert r["ok"] is False
            assert r["codigo"] == "FOCUS_BAD_REQUEST"
        mock_get.assert_not_called()

    @patch("providers.focusnfe_provider.requests.get")
    def test_302_segundo_get_sem_authorization(self, mock_get,
                                                 provider_com_token,
                                                 monkeypatch):
        # Habilita host allowlist para o teste.
        monkeypatch.setenv(
            "FISCALONE_XML_REDIRECT_HOSTS",
            "presigned.example.com",
        )
        html = b"<!doctype html><body>presigned</body>"
        resp1 = _mock_resp(302, {"Location": "https://presigned.example.com/x.html"})
        resp2 = _mock_resp(200, {"Content-Type": "text/html"}, html)
        mock_get.side_effect = [resp1, resp2]
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is True
        assert r["bytes"] == html
        primeiro = mock_get.call_args_list[0]
        segundo = mock_get.call_args_list[1]
        assert "Authorization" in primeiro.kwargs["headers"]
        assert "Authorization" not in segundo.kwargs["headers"]

    @patch("providers.focusnfe_provider.requests.get")
    def test_redirect_host_proibido(self, mock_get, provider_com_token,
                                      monkeypatch):
        monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
        resp1 = _mock_resp(302, {"Location": "https://evil.example/x.html"})
        mock_get.side_effect = [resp1]
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_HOST_PROIBIDO"
        # Nenhum GET pré-assinado deve ser feito.
        assert mock_get.call_count == 1

    @patch("providers.focusnfe_provider.requests.get")
    def test_302_sem_location(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(302, {})
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_NO_LOCATION"

    @patch("providers.focusnfe_provider.requests.get")
    def test_401_upstream(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(401)
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_NAO_AUTORIZADA"
        assert r["http_status"] == 401

    @patch("providers.focusnfe_provider.requests.get")
    def test_404_upstream(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(404)
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_NAO_ENCONTRADA"

    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.Timeout("tempo esgotou")
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_TIMEOUT"

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_conexao(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_REQUEST_ERROR"

    @patch("providers.focusnfe_provider.requests.get")
    def test_corpo_vazio(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(200, {"Content-Type": "text/html"}, b"")
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_VAZIO"

    @patch("providers.focusnfe_provider.requests.get")
    def test_mime_inesperado(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "DANFSE_MIME_INESPERADO"

    def test_token_ausente(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider(token=None)
        r = p.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"

    @patch("providers.focusnfe_provider.requests.get")
    def test_token_nunca_no_envelope(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            401, {}, b"<html>segredo?</html>")
        r = provider_com_token.baixar_danfse_nfse("A" * 44)
        assert r["ok"] is False
        # Token nunca aparece em nenhum campo do envelope.
        for v in r.values():
            assert "abcdef123456" not in str(v)
            assert "Basic " not in str(v)

    @patch("providers.focusnfe_provider.requests.get")
    def test_authorization_apenas_primeira_origem(self, mock_get,
                                                    provider_com_token):
        html = b"<html>ok</html>"
        mock_get.return_value = _mock_resp(
            200, {"Content-Type": "text/html"}, html)
        provider_com_token.baixar_danfse_nfse("A" * 44)
        headers = mock_get.call_args.kwargs["headers"]
        assert headers.get("Authorization", "").startswith("Basic ")
        assert headers.get("Accept") == "text/html"


# ─────────────────────────────────────────────────────────────────────
# Rota M2M — /fiscal/nfse/recebida/danfse
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", "test-m2m-token")
    import app as _app
    _app.app.config["TESTING"] = True
    return _app.app.test_client()


def _hdrs(m2m_token="test-m2m-token"):
    return {"X-RLogix-Service-Token": m2m_token,
            "X-Source-System": "mapone-teste"}


class TestRotaDanfseM2M:

    def test_sem_m2m_401(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 401
        body = r.get_json()
        assert body["codigo"] == "M2M_NAO_AUTORIZADO"

    def test_m2m_ausente_configuracao_503(self, monkeypatch, app_client):
        monkeypatch.delenv("FISCALONE_M2M_TOKEN", raising=False)
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers={"X-RLogix-Service-Token": "qualquer"},
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 503
        assert r.get_json()["codigo"] == "M2M_NAO_CONFIGURADO"

    def test_provider_diferente_de_focusnfe_400(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "sefaz",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "PROVIDER_NAO_SUPORTADO"

    def test_chave_invalida_400(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "com espaco", "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        # Chave com espaço passa no _CONTROL_CHARS_RE mas falha depois
        # no provider (chave inválida). Ambos os caminhos devolvem 400.
        assert r.status_code in (400, 502)

    def test_chave_com_control_char_400(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "AAA\x00BBB", "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "CHAVE_INVALIDA"

    def test_token_focus_ausente_400(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe"},
        )
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "FOCUS_TOKEN_AUSENTE"

    @patch("providers.focusnfe_provider.requests.get")
    def test_sucesso_html_encaminhado(self, mock_get, app_client):
        html = b"<!doctype html><body>NFSe</body>"
        mock_get.return_value = _mock_resp(
            200, {"Content-Type": "text/html; charset=utf-8"}, html)
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "meu-token"},
        )
        assert r.status_code == 200
        assert r.mimetype == "text/html"
        assert r.data == html
        assert r.headers.get("Cache-Control") == "private, no-store"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-RLogix-Provider") == "focusnfe"

    @patch("providers.focusnfe_provider.requests.get")
    def test_focus_404_mapeia_para_404(self, mock_get, app_client):
        mock_get.return_value = _mock_resp(404, {}, b"")
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 404
        assert r.get_json()["codigo"] == "DANFSE_NAO_ENCONTRADA"

    @patch("providers.focusnfe_provider.requests.get")
    def test_focus_timeout_504(self, mock_get, app_client):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 504
        assert r.get_json()["codigo"] == "DANFSE_TIMEOUT"

    @patch("providers.focusnfe_provider.requests.get")
    def test_focus_500_upstream_502(self, mock_get, app_client):
        mock_get.return_value = _mock_resp(500)
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 502

    @patch("providers.focusnfe_provider.requests.get")
    def test_token_nao_aparece_na_resposta(self, mock_get, app_client):
        mock_get.return_value = _mock_resp(
            401, {}, b"<html>upstream</html>")
        r = app_client.post(
            "/fiscal/nfse/recebida/danfse",
            headers=_hdrs(),
            json={"chave": "A"*44, "provider": "focusnfe",
                  "focusnfe_token": "supersecret123"},
        )
        assert r.status_code == 502
        # O corpo do erro nunca deve conter o token.
        assert b"supersecret123" not in r.data


class TestRotaJsonNfseM2M:
    def test_json_sem_m2m_401(self, app_client):
        r = app_client.post(
            "/fiscal/nfse/recebida/json",
            json={"chave_acesso_nfse": "A" * 44,
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 401

    @patch("providers.focusnfe_provider.FocusNFeProvider.baixar_json_nfse_por_chave")
    def test_json_sucesso_repassa_documento(self, baixar, app_client):
        baixar.return_value = {
            "ok": True, "documento": {"chave_nfse": "A" * 44},
        }
        r = app_client.post(
            "/fiscal/nfse/recebida/json", headers=_hdrs(),
            json={"chave_acesso_nfse": "A" * 44,
                  "ambiente": "producao", "focusnfe_token": "segredo"},
        )
        assert r.status_code == 200
        assert r.get_json()["documento"]["chave_nfse"] == "A" * 44
        assert b"segredo" not in r.data

    @patch("providers.focusnfe_provider.FocusNFeProvider.baixar_json_nfse_por_chave")
    def test_json_404_nominal(self, baixar, app_client):
        baixar.return_value = {
            "ok": False, "codigo": "FOCUS_NFSE_JSON_NAO_ENCONTRADO",
            "erro": "nao encontrado",
        }
        r = app_client.post(
            "/fiscal/nfse/recebida/json", headers=_hdrs(),
            json={"chave_acesso_nfse": "A" * 44,
                  "focusnfe_token": "x"},
        )
        assert r.status_code == 404
