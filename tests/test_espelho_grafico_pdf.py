"""Espelho gráfico em PDF — DANFE (NF-e) e DACTE (CT-e).

Gate ESPELHO-GRAFICO (2026-09-10). Contratos oficiais consultados na mesma
data: `GET /v2/nfes_recebidas/{chave}.pdf` e `GET /v2/ctes_recebidas/{chave}.pdf`,
ambos com 302 para o storage. O segundo GET nunca leva Authorization; o
conteúdo é provado por MIME **e** pelos bytes mágicos.

Zero HTTP real, zero token em resposta ou log.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider

CHAVE_NFE = "35260755459671000107550050000379221500064103"
CHAVE_CTE = "35260307219398000109570010000423361000864018"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
    return FocusNFeProvider()


def _resp(status=200, headers=None, content=b""):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.content = content
    return r


def _fluxo_302(provider, metodo, chave, ambiente="producao",
               location="https://storage.focusnfe.com.br/x.pdf",
               content=PDF, mime="application/pdf"):
    chamadas = []

    def _get(url, **kw):
        chamadas.append((url, kw.get("headers") or {}))
        if len(chamadas) == 1:
            return _resp(302, {"Location": location})
        return _resp(200, {"Content-Type": mime}, content)

    with patch.object(requests, "get", side_effect=_get):
        r = getattr(provider, metodo)(chave, ambiente=ambiente)
    return r, chamadas


class TestDanfeDacte:
    def test_danfe_usa_o_endpoint_oficial(self, provider):
        r, chamadas = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE)
        assert r["ok"] is True
        assert chamadas[0][0] == f"https://api.focusnfe.com.br/v2/nfes_recebidas/{CHAVE_NFE}.pdf"
        assert r["bytes"] == PDF and r["mime"] == "application/pdf"
        assert r["sha256"] and r["tamanho"] == len(PDF)

    def test_dacte_usa_o_endpoint_oficial(self, provider):
        r, chamadas = _fluxo_302(provider, "baixar_dacte", CHAVE_CTE)
        assert r["ok"] is True
        assert chamadas[0][0] == f"https://api.focusnfe.com.br/v2/ctes_recebidas/{CHAVE_CTE}.pdf"

    def test_homologacao_troca_o_host(self, provider):
        _r, chamadas = _fluxo_302(provider, "baixar_dacte", CHAVE_CTE, ambiente="homologacao")
        assert chamadas[0][0].startswith("https://homologacao.focusnfe.com.br/")

    def test_segundo_get_nunca_leva_authorization(self, provider):
        _r, chamadas = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE)
        assert "Authorization" in chamadas[0][1]
        assert "Authorization" not in chamadas[1][1]

    def test_conteudo_precisa_ser_pdf_de_verdade(self, provider):
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE, content=b"<html>erro</html>")
        assert r["ok"] is False
        assert r["codigo"] == "DANFE_CONTEUDO_INVALIDO"

    def test_mime_inesperado_recusado(self, provider):
        r, _ = _fluxo_302(provider, "baixar_dacte", CHAVE_CTE, mime="text/html")
        assert r["codigo"] == "DACTE_MIME_INESPERADO"

    def test_redirect_http_simples_recusado(self, provider):
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE,
                          location="http://storage.exemplo.com/x.pdf")
        assert r["codigo"] == "DANFE_HOST_PROIBIDO"

    def test_redirect_com_credencial_embutida_recusado(self, provider):
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE,
                          location="https://user:senha@storage.exemplo.com/x.pdf")
        assert r["codigo"] == "DANFE_HOST_PROIBIDO"

    def test_allowlist_por_env_restringe_o_host(self, provider, monkeypatch):
        monkeypatch.setenv("FISCALONE_XML_REDIRECT_HOSTS", "storage.focusnfe.com.br")
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE,
                          location="https://outro.exemplo.com/x.pdf")
        assert r["codigo"] == "DANFE_HOST_PROIBIDO"
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE)
        assert r["ok"] is True

    def test_404_e_401_nominais(self, provider):
        with patch.object(requests, "get", return_value=_resp(404)):
            r = provider.baixar_dacte(CHAVE_CTE)
        assert r["codigo"] == "DACTE_NAO_ENCONTRADO" and r["http_status"] == 404
        with patch.object(requests, "get", return_value=_resp(401)):
            r = provider.baixar_danfe(CHAVE_NFE)
        assert r["codigo"] == "DANFE_NAO_AUTORIZADO"

    def test_chave_invalida_nao_chega_a_chamar(self, provider):
        with patch.object(requests, "get") as g:
            r = provider.baixar_danfe("../../etc/passwd")
        assert r["codigo"] == "FOCUS_BAD_REQUEST"
        g.assert_not_called()

    def test_resposta_nunca_expoe_token(self, provider):
        r, _ = _fluxo_302(provider, "baixar_danfe", CHAVE_NFE)
        assert "abcdef123456" not in repr({k: v for k, v in r.items() if k != "bytes"})


class TestRotas:
    @pytest.fixture(autouse=True)
    def _app(self, monkeypatch):
        monkeypatch.setenv("FISCALONE_M2M_TOKEN", "m2m-de-teste")
        import app as _app
        _app.app.config["TESTING"] = True
        self.client = _app.app.test_client()

    def _post(self, rota, **payload):
        corpo = {"chave": CHAVE_NFE, "provider": "focusnfe",
                 "ambiente": "producao", "focusnfe_token": "tok"}
        corpo.update(payload)
        return self.client.post(rota, json=corpo,
                                headers={"X-RLogix-Service-Token": "m2m-de-teste",
                                         "X-Source-System": "mapone"})

    def test_danfe_devolve_pdf_cru(self):
        with patch("providers.focusnfe_provider.FocusNFeProvider.baixar_danfe",
                   return_value={"ok": True, "bytes": PDF, "sha256": "abc",
                                 "mime": "application/pdf", "tamanho": len(PDF)}):
            r = self._post("/fiscal/nfe/recebida/danfe")
        assert r.status_code == 200
        assert r.mimetype == "application/pdf"
        assert r.get_data() == PDF
        assert r.headers["X-RLogix-Content-SHA256"] == "abc"
        assert r.headers["Cache-Control"] == "private, no-store"

    def test_dacte_devolve_pdf_cru(self):
        with patch("providers.focusnfe_provider.FocusNFeProvider.baixar_dacte",
                   return_value={"ok": True, "bytes": PDF, "sha256": "d",
                                 "mime": "application/pdf", "tamanho": len(PDF)}):
            r = self._post("/fiscal/cte/recebida/dacte", chave=CHAVE_CTE)
        assert r.status_code == 200 and r.get_data() == PDF

    def test_erro_vira_envelope_json_com_status_proprio(self):
        with patch("providers.focusnfe_provider.FocusNFeProvider.baixar_dacte",
                   return_value={"ok": False, "codigo": "DACTE_NAO_ENCONTRADO",
                                 "erro": "Documento não encontrado na FocusNFe.",
                                 "http_status": 404}):
            r = self._post("/fiscal/cte/recebida/dacte", chave=CHAVE_CTE)
        assert r.status_code == 404
        d = r.get_json()
        assert d["ok"] is False and d["codigo"] == "DACTE_NAO_ENCONTRADO"
        assert d["http_status_upstream"] == 404
        assert b"tok" not in r.get_data()

    def test_sem_m2m_recusa(self):
        r = self.client.post("/fiscal/nfe/recebida/danfe", json={"chave": CHAVE_NFE})
        assert r.status_code in (401, 403, 503)

    def test_provider_e_ambiente_validados(self):
        assert self._post("/fiscal/nfe/recebida/danfe", provider="sefaz").status_code == 400
        assert self._post("/fiscal/nfe/recebida/danfe", ambiente="sandbox").status_code == 400
        assert self._post("/fiscal/nfe/recebida/danfe", focusnfe_token="").status_code == 400


class TestMensagemDoUpstream:
    """Marcos (10/09) viu `DANFE_NAO_AUTORIZADO` sem saber a causa: o 401 da
    FocusNFe podia ser credencial inválida **ou** permissão negada para o
    recurso, e o nosso código descartava a mensagem que separa as duas."""

    def _resp_json(self, status, corpo):
        r = MagicMock(spec=requests.Response)
        r.status_code = status
        r.headers = {"Content-Type": "application/json"}
        r.content = b"{}"
        r.json.return_value = corpo
        return r

    def test_401_carrega_codigo_e_mensagem_da_focus(self, provider):
        resp = self._resp_json(401, {"codigo": "permissao_negada",
                                     "mensagem": "Token sem acesso ao recurso"})
        with patch.object(requests, "get", return_value=resp):
            r = provider.baixar_danfe(CHAVE_NFE)
        assert r["codigo"] == "DANFE_NAO_AUTORIZADO"
        assert "permissao_negada" in r["erro"]
        assert "Token sem acesso ao recurso" in r["erro"]

    def test_403_cai_no_mesmo_tratamento(self, provider):
        resp = self._resp_json(403, {"codigo": "acesso_negado", "mensagem": "x"})
        with patch.object(requests, "get", return_value=resp):
            r = provider.baixar_dacte(CHAVE_CTE)
        assert r["codigo"] == "DACTE_NAO_AUTORIZADO"
        assert r["http_status"] == 403

    def test_corpo_nao_json_mantem_a_mensagem_padrao(self, provider):
        r0 = MagicMock(spec=requests.Response)
        r0.status_code = 401
        r0.headers = {"Content-Type": "text/html"}
        r0.content = b"<html>"
        r0.json.side_effect = ValueError("nao e json")
        with patch.object(requests, "get", return_value=r0):
            r = provider.baixar_danfe(CHAVE_NFE)
        assert r["erro"] == "Credencial rejeitada pela FocusNFe."

    def test_nada_alem_de_codigo_e_mensagem_atravessa(self, provider):
        """O corpo de erro não pode virar um vazamento: só dois campos."""
        resp = self._resp_json(404, {"codigo": "nao_encontrado",
                                     "mensagem": "Documento fiscal nao encontrado",
                                     "chave": CHAVE_NFE,
                                     "cnpj": "07219398000109"})
        with patch.object(requests, "get", return_value=resp):
            r = provider.baixar_danfe(CHAVE_NFE)
        assert CHAVE_NFE not in r["erro"]
        assert "07219398000109" not in r["erro"]
