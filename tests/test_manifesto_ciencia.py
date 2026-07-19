"""E4b-1A — Manifestacao de Ciencia (evento SEFAZ 210210) via FocusNFe.

Todos os testes usam mock. Zero POST real ao FocusNFe.

Cobertura:
- Trava tipo (so aceita "ciencia" — bloqueia confirmacao/desconhecimento/nao_realizada).
- Trava chave (44 digitos numericos).
- Trava token (FOCUS_TOKEN_AUSENTE sem POST).
- Mapeamento de status HTTP (200/201/202/400/401/403/404/409/422/429/5xx/exception).
- Rota /fiscal/nfe/recebida/manifesto sanitiza campos sensiveis.
- Rota rejeita provider != focusnfe.
- Envelope e log nunca vazam token/Authorization/XML/certificado.
- Emissao NF-e/CT-e/NFS-e/MDF-e permanece bloqueada.
- Rota nao chama nenhum metodo de emissao.
"""
import importlib
import io
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider


CHAVE_44 = "35260607219398000109550010000001231000001231"
CHAVE_43 = CHAVE_44[:-1]                       # 43 digitos
CHAVE_LETRAS = "3" * 40 + "ABCD"               # 44 chars, com letras


# ── Helpers ─────────────────────────────────────────────────────────────────
def _mock_resp(status=200, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


@pytest.fixture
def provider_com_token(monkeypatch):
    """FocusNFeProvider com token — nao dispara HTTP real."""
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


@pytest.fixture
def provider_sem_token(monkeypatch):
    """FocusNFeProvider sem token (fail-fast local)."""
    monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider(token=None)


@pytest.fixture
def cli(monkeypatch):
    """Client Flask com producao liberada (para nao mascarar codigo)."""
    monkeypatch.setenv("FISCALONE_AMBIENTE", "producao")
    monkeypatch.setenv("FISCALONE_ENABLE_PRODUCAO", "1")
    monkeypatch.setenv("MAPONE_FISCAL_PRODUCAO_READY", "1")
    monkeypatch.setenv("FISCALONE_DFE_RECEBIDO_ONLY", "1")
    monkeypatch.setenv("FISCAL_PROVIDER", "focusnfe")
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
    import app
    importlib.reload(app)
    return app.app.test_client()


# ── Travas de tipo ──────────────────────────────────────────────────────────
class TestTravaTipo:
    @patch("providers.focusnfe_provider.requests.post")
    def test_confirmacao_bloqueada_sem_post(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="confirmacao", trace_id="fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_desconhecimento_bloqueada_sem_post(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="desconhecimento", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_nao_realizada_bloqueada_sem_post(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="nao_realizada", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_tipo_vazio_bloqueado(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO"
        mock_post.assert_not_called()


# ── Trava de chave ──────────────────────────────────────────────────────────
class TestTravaChave:
    @patch("providers.focusnfe_provider.requests.post")
    def test_chave_43_digitos(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_43, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_CHAVE_INVALIDA"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_chave_com_letras(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_LETRAS, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_CHAVE_INVALIDA"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_chave_vazia(self, mock_post, provider_com_token):
        r = provider_com_token.manifestar_nfe_recebida(
            "", tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_CHAVE_INVALIDA"
        mock_post.assert_not_called()


# ── Trava de token ──────────────────────────────────────────────────────────
class TestTravaToken:
    @patch("providers.focusnfe_provider.requests.post")
    def test_token_ausente_nao_dispara_post(self, mock_post, provider_sem_token):
        r = provider_sem_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"
        mock_post.assert_not_called()


# ── Sucesso HTTP ────────────────────────────────────────────────────────────
class TestSucessoHttp:
    @patch("providers.focusnfe_provider.requests.post")
    def test_200_retorna_manifesto_ok(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(
            200,
            json_data={
                "cstat": "135",
                "xmotivo": "Evento registrado e vinculado a NF-e",
                "protocolo": "135260000123456",
            },
        )
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", ambiente="homologacao", trace_id="fo-t")
        assert r["ok"] is True
        assert r["codigo"] == "MANIFESTO_OK"
        assert r["evento"] == "210210"
        assert r["tipo"] == "ciencia"
        assert r["chave"] == CHAVE_44
        assert r["cstat"] == "135"
        assert r["protocolo"] == "135260000123456"
        assert r["http_status"] == 200
        # URL correta
        args, kwargs = mock_post.call_args
        assert args[0].endswith(f"/v2/nfes_recebidas/{CHAVE_44}/manifesto")
        assert kwargs["json"] == {"tipo": "ciencia"}
        # Nao segue redirect
        assert kwargs["allow_redirects"] is False

    @patch("providers.focusnfe_provider.requests.post")
    def test_201_retorna_manifesto_ok(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(201, json_data={})
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["ok"] is True
        assert r["codigo"] == "MANIFESTO_OK"
        assert r["http_status"] == 201

    @patch("providers.focusnfe_provider.requests.post")
    def test_202_retorna_manifesto_ok(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(202, json_data={})
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["ok"] is True
        assert r["codigo"] == "MANIFESTO_OK"
        assert r["http_status"] == 202


# ── Mapeamento de erros HTTP ────────────────────────────────────────────────
class TestErrosHttp:
    @patch("providers.focusnfe_provider.requests.post")
    def test_400_invalido(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(400, json_data={"erro": "x"})
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_MANIFESTO_INVALIDO"
        assert r["http_status"] == 400

    @patch("providers.focusnfe_provider.requests.post")
    def test_401_auth(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(401)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_AUTH_ERROR"

    @patch("providers.focusnfe_provider.requests.post")
    def test_403_forbidden(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(403)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_FORBIDDEN"

    @patch("providers.focusnfe_provider.requests.post")
    def test_404_nao_encontrado(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(404)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_NAO_ENCONTRADO"

    @patch("providers.focusnfe_provider.requests.post")
    def test_409_conflito(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(409)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_CONFLITO"

    @patch("providers.focusnfe_provider.requests.post")
    def test_422_conflito(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(422)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_CONFLITO"

    @patch("providers.focusnfe_provider.requests.post")
    def test_429_rate_limit(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(429)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_RATE_LIMIT"

    @patch("providers.focusnfe_provider.requests.post")
    def test_500_http_error(self, mock_post, provider_com_token):
        mock_post.return_value = _mock_resp(500)
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["codigo"] == "FOCUS_MANIFESTO_HTTP_ERROR"

    @patch("providers.focusnfe_provider.requests.post")
    def test_request_exception(self, mock_post, provider_com_token):
        mock_post.side_effect = requests.exceptions.ConnectionError("boom")
        r = provider_com_token.manifestar_nfe_recebida(
            CHAVE_44, tipo="ciencia", trace_id="fo-t")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_MANIFESTO_HTTP_ERROR"


# ── Rota /fiscal/nfe/recebida/manifesto ─────────────────────────────────────
class TestRotaManifesto:
    @patch("providers.focusnfe_provider.requests.post")
    def test_ciencia_ok(self, mock_post, cli):
        mock_post.return_value = _mock_resp(
            200,
            json_data={"cstat": "135", "xmotivo": "OK", "protocolo": "p1"},
        )
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={
                "chave": CHAVE_44,
                "tipo": "ciencia",
                "ambiente": "homologacao",
                "focusnfe_token": "SEGREDO-abcdef",
            },
        )
        assert r.status_code == 200
        j = r.get_json()
        assert j["ok"] is True
        assert j["codigo"] == "MANIFESTO_OK"
        assert j["evento"] == "210210"
        assert j["chave"] == CHAVE_44
        # Envelope nunca contem token/authorization/xml
        raw = json.dumps(j)
        assert "SEGREDO-abcdef" not in raw
        assert "Authorization" not in raw
        assert "Bearer" not in raw

    def test_provider_invalido_400(self, cli):
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={
                "chave": CHAVE_44,
                "tipo": "ciencia",
                "focusnfe_token": "abc",
                "provider": "sefaz",
            },
        )
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "FOCUS_MANIFESTO_PROVIDER_INVALIDO"

    def test_payload_vazio_400(self, cli):
        r = cli.post("/fiscal/nfe/recebida/manifesto", json={})
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "PAYLOAD_INVALIDO"

    @patch("providers.focusnfe_provider.requests.post")
    def test_tipo_confirmacao_bloqueado_400(self, mock_post, cli):
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_44, "tipo": "confirmacao",
                  "focusnfe_token": "abc"},
        )
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_chave_invalida_400(self, mock_post, cli):
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_43, "tipo": "ciencia",
                  "focusnfe_token": "abc"},
        )
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "FOCUS_MANIFESTO_CHAVE_INVALIDA"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_token_ausente_400(self, mock_post, cli):
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_44, "tipo": "ciencia"},
        )
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "FOCUS_TOKEN_AUSENTE"
        mock_post.assert_not_called()

    @patch("providers.focusnfe_provider.requests.post")
    def test_rota_sanitiza_cert_pfx_e_password(self, mock_post, cli):
        """Rota deve REMOVER cert_pfx_base64 e cert_password ANTES do provider.

        Verificamos que o payload JSON enviado ao Focus nao contem certificado
        e que o envelope de retorno nao vaza esses campos.
        """
        mock_post.return_value = _mock_resp(
            200, json_data={"cstat": "135", "xmotivo": "OK"})
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={
                "chave": CHAVE_44,
                "tipo": "ciencia",
                "focusnfe_token": "abc",
                "cert_pfx_base64": "AAAABBBB-SEGREDO-CERT",
                "cert_password": "SENHA-CERT-XYZ",
            },
        )
        assert r.status_code == 200
        j = r.get_json()
        # Nada de certificado no envelope
        raw = json.dumps(j)
        assert "AAAABBBB-SEGREDO-CERT" not in raw
        assert "SENHA-CERT-XYZ" not in raw
        assert "cert_pfx_base64" not in raw
        assert "cert_password" not in raw
        # Body enviado ao Focus e apenas {"tipo":"ciencia"}
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"tipo": "ciencia"}

    @patch("providers.focusnfe_provider.requests.post")
    def test_409_conflito_retorna_409(self, mock_post, cli):
        mock_post.return_value = _mock_resp(409)
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_44, "tipo": "ciencia",
                  "focusnfe_token": "abc"},
        )
        assert r.status_code == 409
        j = r.get_json()
        assert j["codigo"] == "FOCUS_MANIFESTO_CONFLITO"

    @patch("providers.focusnfe_provider.requests.post")
    def test_404_retorna_404(self, mock_post, cli):
        mock_post.return_value = _mock_resp(404)
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_44, "tipo": "ciencia",
                  "focusnfe_token": "abc"},
        )
        assert r.status_code == 404
        j = r.get_json()
        assert j["codigo"] == "FOCUS_MANIFESTO_NAO_ENCONTRADO"


# ── Log nao vaza dados sensiveis ────────────────────────────────────────────
class TestLogNaoVaza:
    @patch("providers.focusnfe_provider.requests.post")
    def test_log_provider_nao_contem_token_authorization_xml(
            self, mock_post, provider_com_token, caplog):
        mock_post.return_value = _mock_resp(
            200, json_data={"cstat": "135", "xmotivo": "OK"})
        with caplog.at_level(logging.INFO):
            provider_com_token.manifestar_nfe_recebida(
                CHAVE_44, tipo="ciencia", trace_id="fo-t")
        blob = " ".join(rec.getMessage() for rec in caplog.records)
        assert "abcdef123456" not in blob      # token do fixture
        assert "Authorization" not in blob
        assert "Bearer" not in blob
        assert "Basic " not in blob
        assert "<xml" not in blob.lower()
        # A chave deve aparecer mascarada — nao inteira
        assert CHAVE_44 not in blob


# ── Regressao: rotas de emissao continuam bloqueadas ────────────────────────
_ROTAS_EMISSAO_BLOQ = [
    ("POST",   "/fiscal/nfe"),
    ("POST",   "/fiscal/cte"),
    ("POST",   "/fiscal/mdfe"),
    ("DELETE", "/fiscal/nfe/" + CHAVE_44),
    ("DELETE", "/fiscal/cte/" + CHAVE_44),
    ("POST",   "/fiscal/nfe/" + CHAVE_44 + "/inutilizar"),
    ("POST",   "/fiscal/nfe/" + CHAVE_44 + "/cce"),
    ("POST",   "/fiscal/mdfe/" + CHAVE_44 + "/encerrar"),
    ("POST",   "/fiscal/mdfe/" + CHAVE_44 + "/condutor"),
]


@pytest.mark.parametrize("metodo,rota", _ROTAS_EMISSAO_BLOQ)
def test_emissao_bloqueada_regressao(cli, metodo, rota):
    r = cli.open(rota, method=metodo, json={})
    assert r.status_code == 403
    assert r.get_json()["codigo"] == "EMISSAO_BLOQUEADA"


def test_nfse_nao_tem_endpoint_de_emissao(cli):
    """NFS-e emissao — nao existe endpoint (defesa por ausencia). POST em
    rota inexistente devolve 404 (nao 200/201) — confirma que nada foi
    liberado nesta fase."""
    r = cli.post("/fiscal/nfse", json={})
    assert r.status_code == 404


def test_rota_manifesto_nao_chama_metodos_de_emissao(cli):
    """A rota nao deve invocar qualquer emitir_* do provider. Espionamos
    o provider e garantimos que emitir_cte/emitir_mdfe nao sao chamados."""
    with patch("providers.focusnfe_provider.FocusNFeProvider.emitir_cte") as m_cte, \
         patch("providers.focusnfe_provider.FocusNFeProvider.emitir_mdfe") as m_mdf, \
         patch("providers.focusnfe_provider.requests.post") as m_post:
        m_post.return_value = _mock_resp(
            200, json_data={"cstat": "135"})
        r = cli.post(
            "/fiscal/nfe/recebida/manifesto",
            json={"chave": CHAVE_44, "tipo": "ciencia",
                  "focusnfe_token": "abc"},
        )
        assert r.status_code == 200
        m_cte.assert_not_called()
        m_mdf.assert_not_called()
