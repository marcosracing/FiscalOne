"""Fase D — provider e token FocusNFe por requisicao.

Cobre:
- `get_provider(provider_name, token)` — resolucao por parametro + fallback env.
- `/fiscal/gov/fetch` — extracao de `provider` e `focusnfe_token` do payload,
  allowlist com 400, blindagem contra certificado A1 quando provider=focusnfe.
- `FocusNFeProvider(token=...)` — precedencia injetado > env.
- `baixar_danfe()` com token injetado — sem exigir env, sem HTTP real.
- Seguranca — token/Authorization/cert nunca em envelope/log/resposta.

Todo teste HTTP usa `unittest.mock.patch("requests.get")` — zero HTTP real.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider
from providers.sefaz_provider import SefazProvider


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def app_focusnfe_env(monkeypatch):
    """Instancia app com FISCAL_PROVIDER=focusnfe (para testar override por payload)."""
    monkeypatch.setenv("FISCAL_PROVIDER", "focusnfe")
    monkeypatch.setenv("FOCUSNFE_TOKEN", "token-do-env-nao-usar")
    import app
    importlib.reload(app)
    return app


@pytest.fixture
def app_sefaz_env(monkeypatch):
    """Instancia app com FISCAL_PROVIDER=sefaz (default)."""
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
    import app
    importlib.reload(app)
    return app


def _mock_focus_200(json_data=None, headers=None):
    """Mock de resposta 200 da FocusNFe (lista JSON de NF-e)."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.headers = headers or {}
    resp.json.return_value = json_data if json_data is not None else []
    return resp


# ── get_provider ────────────────────────────────────────────────────────────
class TestGetProvider:
    def test_sem_args_mantem_fallback_env(self, app_sefaz_env):
        p = app_sefaz_env.get_provider()
        assert isinstance(p, SefazProvider)

    def test_sem_args_fallback_env_focusnfe(self, app_focusnfe_env):
        p = app_focusnfe_env.get_provider()
        assert isinstance(p, FocusNFeProvider)

    def test_provider_sefaz_explicito(self, app_focusnfe_env):
        # Mesmo com FISCAL_PROVIDER=focusnfe, parametro forca SEFAZ
        p = app_focusnfe_env.get_provider("sefaz")
        assert isinstance(p, SefazProvider)

    def test_provider_focusnfe_com_token_injetado(self, app_sefaz_env, monkeypatch):
        # Env FOCUSNFE_TOKEN esta ausente; token injetado deve ganhar
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = app_sefaz_env.get_provider("focusnfe", token="token-abc-123")
        assert isinstance(p, FocusNFeProvider)
        assert p._token == "token-abc-123"

    def test_provider_focusnfe_token_injetado_ganha_de_env(self, app_focusnfe_env):
        # env=token-do-env-nao-usar; injetado=novo-token-injetado
        p = app_focusnfe_env.get_provider("focusnfe", token="novo-token-injetado")
        assert isinstance(p, FocusNFeProvider)
        assert p._token == "novo-token-injetado"
        assert "token-do-env-nao-usar" not in p._token

    def test_provider_focusnfe_sem_token_cai_no_env(self, app_focusnfe_env):
        # Sem token injetado → cai em FOCUSNFE_TOKEN env
        p = app_focusnfe_env.get_provider("focusnfe")
        assert p._token == "token-do-env-nao-usar"

    def test_provider_case_insensitive(self, app_sefaz_env):
        p = app_sefaz_env.get_provider("FOCUSNFE", token="x")
        assert isinstance(p, FocusNFeProvider)
        p = app_sefaz_env.get_provider("  Sefaz  ")
        assert isinstance(p, SefazProvider)

    def test_provider_invalido_levanta(self, app_sefaz_env):
        # Ao passar provider explicito invalido, levanta (handler valida antes
        # de chamar, mas o helper tambem se defende).
        with pytest.raises(ValueError, match="provider nao suportado"):
            app_sefaz_env.get_provider("hackear_isso")

    def test_provider_vazio_cai_no_env(self, app_sefaz_env):
        p = app_sefaz_env.get_provider("")
        assert isinstance(p, SefazProvider)  # env=sefaz
        p = app_sefaz_env.get_provider(None)
        assert isinstance(p, SefazProvider)


# ── /fiscal/gov/fetch — provider por requisicao ─────────────────────────────
class TestFiscalGovFetchProviderPayload:
    @patch("providers.focusnfe_provider.requests.get")
    def test_payload_sem_provider_usa_env(self, mock_get, app_focusnfe_env):
        """Sem `provider` no payload, fallback ao env FISCAL_PROVIDER=focusnfe."""
        cli = app_focusnfe_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "1"})
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "ultimo_nsu": "0",
        })
        assert r.status_code == 200
        assert r.get_json()["provider"] == "focusnfe"

    @patch("providers.focusnfe_provider.requests.get")
    def test_payload_provider_focusnfe_override_env_sefaz(self, mock_get, app_sefaz_env, monkeypatch):
        """provider=focusnfe no payload OVERRIDE env FISCAL_PROVIDER=sefaz."""
        monkeypatch.setenv("FOCUSNFE_TOKEN", "token-env")
        cli = app_sefaz_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "5"})
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "ultimo_nsu": "0",
        })
        assert r.status_code == 200
        assert r.get_json()["provider"] == "focusnfe"
        # HTTP disparou (mock), provando que FocusNFeProvider foi usado
        assert mock_get.called

    def test_payload_provider_sefaz_explicito(self, app_focusnfe_env):
        """provider=sefaz no payload OVERRIDE env FISCAL_PROVIDER=focusnfe.
        Nao mockamos requests aqui pq SefazProvider vai tentar SOAP e falhar —
        so validamos que o codigo devolvido nao e PROVIDER_INVALIDO nem
        PROVIDER_NAO_IMPLEMENTADO (comportamento tipico do FocusNFe)."""
        cli = app_focusnfe_env.app.test_client()
        # Sem certificado, SefazProvider vai devolver CERT_NAO_CONFIGURADO ou
        # similar (400) — o importante e nao cair no branch focusnfe.
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "sefaz",
            "ultimo_nsu": "0",
        })
        j = r.get_json()
        # NAO pode ser codigo Focus (validacao dupla: nem PROVIDER_NAO_IMPLEMENTADO,
        # nem qualquer FOCUS_*).
        codigo = (j or {}).get("codigo", "")
        assert codigo != "PROVIDER_NAO_IMPLEMENTADO"
        assert not codigo.startswith("FOCUS_")

    def test_payload_provider_invalido_400(self, app_sefaz_env):
        cli = app_sefaz_env.app.test_client()
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "provider_hackeado_v2",
            "ultimo_nsu": "0",
        })
        assert r.status_code == 400
        j = r.get_json()
        assert j["codigo"] == "PROVIDER_INVALIDO"
        # NAO ecoa o valor invalido no envelope de resposta
        assert "provider_hackeado_v2" not in str(j).lower()


# ── Token FocusNFe via payload — nao vaza ────────────────────────────────
class TestFocusnfeTokenPayload:
    @patch("providers.focusnfe_provider.requests.get")
    def test_focusnfe_token_no_payload_usado_e_nao_vazado(self, mock_get, app_sefaz_env, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        cli = app_sefaz_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "10"})
        token_secreto = "TOKEN-SECRETO-DE-TESTE-XYZ789"
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "focusnfe_token": token_secreto,
            "ultimo_nsu": "0",
        })
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # Token nao pode aparecer no body de nenhuma forma
        assert token_secreto not in body
        assert "focusnfe_token" not in body.lower()
        assert "authorization" not in body.lower()
        assert "basic " not in body.lower()
        # Mas a chamada HTTP DEVE ter usado o token (via Authorization header)
        assert mock_get.called
        headers_enviados = mock_get.call_args.kwargs["headers"]
        assert "Authorization" in headers_enviados
        # O header contem o token codificado em base64 — nao aparece raw no body
        assert token_secreto not in body

    @patch("providers.focusnfe_provider.requests.get")
    def test_focusnfe_token_ausente_e_env_ausente_erro_controlado(self, mock_get, app_sefaz_env, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        cli = app_sefaz_env.app.test_client()
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "ultimo_nsu": "0",
        })
        assert r.status_code == 400
        assert r.get_json()["codigo"] == "FOCUS_TOKEN_AUSENTE"
        # HTTP nao pode ter sido disparado
        assert not mock_get.called

    @patch("providers.focusnfe_provider.requests.get")
    def test_focusnfe_token_no_payload_ganha_de_env(self, mock_get, app_sefaz_env, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "TOKEN-DO-ENV-VELHO")
        cli = app_sefaz_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "1"})
        cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "focusnfe_token": "TOKEN-NOVO-INJETADO",
            "ultimo_nsu": "0",
        })
        # A chamada HTTP usa o token injetado, nao o do env
        headers_enviados = mock_get.call_args.kwargs["headers"]
        import base64
        esperado = base64.b64encode(b"TOKEN-NOVO-INJETADO:").decode()
        assert headers_enviados["Authorization"] == f"Basic {esperado}"


# ── Blindagem: cert A1 em provider=focusnfe ─────────────────────────────────
class TestBlindagemCertFocusNFe:
    @patch("providers.focusnfe_provider.requests.get")
    def test_provider_focusnfe_ignora_cert_pfx_base64(self, mock_get, app_sefaz_env, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "token-x")
        cli = app_sefaz_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "1"})
        # MapOne pode enviar cert por engano (bug ou legado) — FiscalOne
        # precisa ignorar defensivamente quando provider=focusnfe.
        r = cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "ultimo_nsu": "0",
            "cert_pfx_base64": "BASE64_DO_CERT_QUE_VEIO_POR_ENGANO",
            "cert_password": "SENHA_DO_CERT_QUE_VEIO_POR_ENGANO",
            "cert_cnpj": "07219398000109",
            "cert_valid_until": "2027-01-01",
        })
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # NENHUM dos campos de cert deve aparecer no body
        assert "BASE64_DO_CERT" not in body
        assert "SENHA_DO_CERT" not in body
        assert "cert_pfx_base64" not in body.lower()
        assert "cert_password" not in body.lower()
        # HTTP disparou (Focus foi chamado, nao SEFAZ)
        assert mock_get.called
        # Nenhum cert usado (Focus nao pede cert; SEFAZ pediria)
        assert "Authorization" in mock_get.call_args.kwargs["headers"]


# ── FocusNFeProvider(token=...) — precedencia ───────────────────────────────
class TestFocusNFeProviderInit:
    def test_token_injetado_ganha_de_env(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "env-token-antigo")
        p = FocusNFeProvider(token="injetado-novo")
        assert p._token == "injetado-novo"

    def test_token_none_usa_env(self, monkeypatch):
        monkeypatch.setenv("FOCUSNFE_TOKEN", "env-token")
        p = FocusNFeProvider(token=None)
        assert p._token == "env-token"

    def test_token_vazio_string_usa_env(self, monkeypatch):
        # "" e "  " sao equivalentes a ausencia — cai no env
        monkeypatch.setenv("FOCUSNFE_TOKEN", "env-token")
        p = FocusNFeProvider(token="")
        assert p._token == "env-token"
        p = FocusNFeProvider(token="   ")
        assert p._token == "env-token"

    def test_sem_token_sem_env_vazio(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        assert p._token == ""

    def test_sem_token_sem_env_require_levanta(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        with pytest.raises(RuntimeError, match="FOCUSNFE_TOKEN"):
            p._require_token()

    def test_backward_compat_sem_arg(self, monkeypatch):
        # Chamada legada FocusNFeProvider() sem args continua funcionando
        monkeypatch.setenv("FOCUSNFE_TOKEN", "abc")
        p = FocusNFeProvider()
        assert p._token == "abc"


# ── gov_fetch com token injetado ────────────────────────────────────────────
class TestGovFetchTokenInjetado:
    @patch("providers.focusnfe_provider.requests.get")
    def test_gov_fetch_com_token_injetado(self, mock_get, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider(token="token-injetado-abc")
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "1"})
        r = p.gov_fetch({"cnpj": "07219398000109", "tipo": "nfe"}, "fo-t")
        assert r["ok"] is True
        # Chamada HTTP usou o token injetado
        import base64
        esperado = base64.b64encode(b"token-injetado-abc:").decode()
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == f"Basic {esperado}"


# ── baixar_danfe com token injetado ─────────────────────────────────────────
class TestBaixarDanfeTokenInjetado:
    @patch("providers.focusnfe_provider.requests.get")
    def test_baixar_danfe_com_token_injetado_sem_env(self, mock_get, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider(token="token-abc")
        # 200 direto com PDF
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/pdf"}
        resp.content = b"%PDF-1.4\nmock"
        mock_get.return_value = resp
        r = p.baixar_danfe("A" * 44)
        assert r["ok"] is True
        assert r["bytes"] == b"%PDF-1.4\nmock"
        # 1o GET tem Authorization
        assert "Authorization" in mock_get.call_args.kwargs["headers"]

    @patch("providers.focusnfe_provider.requests.get")
    def test_baixar_danfe_302_2o_get_sem_authorization(self, mock_get, monkeypatch):
        """Preservacao da regra critica: 2o GET (URL presigned) sem Authorization."""
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider(token="token-abc")
        resp1 = MagicMock(spec=requests.Response)
        resp1.status_code = 302
        resp1.headers = {"Location": "https://presigned.example.com/x.pdf"}
        resp2 = MagicMock(spec=requests.Response)
        resp2.status_code = 200
        resp2.headers = {"Content-Type": "application/pdf"}
        resp2.content = b"%PDF"
        mock_get.side_effect = [resp1, resp2]
        r = p.baixar_danfe("A" * 44)
        assert r["ok"] is True
        primeiro = mock_get.call_args_list[0]
        segundo = mock_get.call_args_list[1]
        assert "Authorization" in primeiro.kwargs["headers"]
        assert "Authorization" not in segundo.kwargs["headers"]


# ── Seguranca — payload/token/cert nao aparecem em log ─────────────────────
class TestSegurancaFaseD:
    @patch("providers.focusnfe_provider.requests.get")
    def test_log_stdout_nao_contem_token(self, mock_get, app_sefaz_env, capsys, monkeypatch):
        """Executar gov_fetch com token injetado e verificar que nada no stdout
        (usado por _log_stdout) contem o token."""
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        cli = app_sefaz_env.app.test_client()
        mock_get.return_value = _mock_focus_200(headers={"X-Max-Version": "1"})
        token = "TOKEN-DE-VAZAMENTO-XYZ"
        cli.post("/fiscal/gov/fetch", json={
            "cnpj_tenant": "07219398000109",
            "ambiente": "homologacao",
            "tipo": "nfe",
            "provider": "focusnfe",
            "focusnfe_token": token,
            "ultimo_nsu": "0",
        })
        captured = capsys.readouterr()
        # stdout inclui _log_stdout — nunca pode conter o token
        assert token not in captured.out
        assert token not in captured.err
        assert "authorization" not in captured.out.lower()
        assert "focusnfe_token" not in captured.out.lower()
