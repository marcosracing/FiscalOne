"""FocusNFe · Fase E4c — NFSe Nacional recebidas via FocusNFe.

Cobre:
  - `gov_fetch(tipo="nfse")` monta URL `/v2/nfses_recebidas` e envia
    `params={cnpj, versao, completa="1"}`.
  - Mapper `_mapear_nfse_focus` converte prestador/tomador/servicos.
  - status=1/2/3 → cancelado/substituido corretos.
  - `url_xml` sucesso → `xml_bruto`+`COMPLETO`.
  - `url_xml` falha (404/timeout) → RESUMO+`xml_pending`.
  - `empresa_nao_habilitada` (403 body) → `FOCUS_NFSE_NAO_HABILITADA`.
  - `tipo=cte` continua bloqueado (`FOCUS_TIPO_NAO_SUPORTADO`).
  - Regressao NF-e: `tipo=nfe` inalterado.

Zero HTTP real. Zero token no envelope.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import (
    FocusNFeProvider,
    _mapear_nfse_focus,
)


@pytest.fixture
def provider_com_token(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


def _mock_resp(status=200, json_data=None, headers=None, text=""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


def _item_nfse(chave="ABC123456789012345678901234567890123456789012",
               status=1,
               with_url_xml=False,
               prestador_cnpj="12345678000199",
               prestador_cpf="",
               tomador_cnpj="07219398000109"):
    """Fabrica item NfseRecebida no formato oficial FocusNFe."""
    prest = {"razao_social": "Prestador de Servicos LTDA",
             "nome_fantasia": "Prestador",
             "inscricao_municipal": "12345"}
    if prestador_cpf:
        prest["cpf"] = prestador_cpf
    else:
        prest["cnpj"] = prestador_cnpj
    return {
        "chave":              chave,
        "versao":             42,
        "status":             status,
        "numero":             "1001",
        "serie":              "1",
        "codigo_verificacao": "ABCD1234",
        "data_emissao":       "2026-07-15T10:00:00-03:00",
        "competencia":        "2026-07",
        "prestador":          prest,
        "tomador":            {"cnpj": tomador_cnpj,
                               "razao_social": "Racing Logistica"},
        "servicos":           {"valor_servicos": "1500.00",
                               "valor_iss":      "75.00",
                               "iss_retido":     False,
                               "valor_liquido":  "1425.00",
                               "discriminacao":  "Servico de manutencao."},
        "url":                "https://exemplo/nfse/123",
        "url_xml":            "https://exemplo/nfse/123.xml" if with_url_xml else "",
    }


# ── Mapper isolado ──────────────────────────────────────────────────────────
class TestMapperNfse:
    def test_status1_autorizada(self):
        d = _mapear_nfse_focus(_item_nfse(status=1), "fo-e4c")
        assert d["type"] == "nfse"
        assert d["doc_type"] == "nfse"
        assert d["cancelado"] == 0
        assert d["substituido"] == 0
        assert d["situacao_nfse"] == "autorizada"
        assert d["import_origin"] == "fiscalone_focusnfe_nfse"
        assert d["status_sefaz"] == "focusnfe"
        assert d["parser_version"] == "focus_nfse_v1"

    def test_status2_cancelada(self):
        d = _mapear_nfse_focus(_item_nfse(status=2), "fo-e4c")
        assert d["cancelado"] == 1
        assert d["substituido"] == 0
        assert d["situacao_nfse"] == "cancelada"

    def test_status3_substituida(self):
        d = _mapear_nfse_focus(_item_nfse(status=3), "fo-e4c")
        assert d["cancelado"] == 0
        assert d["substituido"] == 1
        assert d["situacao_nfse"] == "substituida"

    def test_campos_prestador_tomador_servicos(self):
        d = _mapear_nfse_focus(_item_nfse(), "fo-e4c")
        assert d["emit_cnpj"]     == "12345678000199"
        assert d["emit_doc_tipo"] == "cnpj"
        assert d["emit_nome"]     == "Prestador de Servicos LTDA"
        assert d["emit_ie"]       == "12345"
        assert d["dest_cnpj"]     == "07219398000109"
        assert d["dest_doc_tipo"] == "cnpj"
        assert d["valor_total"]   == "1500.00"
        assert d["valor_iss"]     == "75.00"
        assert d["valor_liquido"] == "1425.00"
        assert d["discriminacao"] == "Servico de manutencao."

    def test_prestador_cpf(self):
        d = _mapear_nfse_focus(_item_nfse(prestador_cnpj="", prestador_cpf="12345678909"),
                               "fo-e4c")
        assert d["emit_cnpj"]     == "12345678909"
        assert d["emit_doc_tipo"] == "cpf"

    def test_chave_ausente_levanta(self):
        with pytest.raises(ValueError):
            _mapear_nfse_focus({"status": 1}, "fo-e4c")

    def test_default_status_xml_resumo(self):
        d = _mapear_nfse_focus(_item_nfse(), "fo-e4c")
        # Mapper isolado sempre RESUMO — promocao a COMPLETO acontece
        # apenas no gov_fetch depois de baixar_xml_nfse.
        assert d["status_xml"] == "RESUMO"


# ── gov_fetch — dispatch por tipo ───────────────────────────────────────────
class TestGovFetchTipoNfse:
    @patch("providers.focusnfe_provider.requests.get")
    def test_url_nfses_recebidas_com_completa_1(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "42", "X-Total-Count": "1"},
            json_data=[_item_nfse()])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert r["ok"] is True
        args, kwargs = mock_get.call_args
        assert args[0].endswith("/v2/nfses_recebidas")
        assert kwargs["params"]["cnpj"] == "07219398000109"
        assert kwargs["params"]["versao"] == "0"
        assert kwargs["params"]["completa"] == "1"

    @patch("providers.focusnfe_provider.requests.get")
    def test_tipo_nfe_ainda_usa_nfes_recebidas(self, mock_get, provider_com_token):
        """Regressao: tipo=nfe continua com rota antiga, sem `completa`."""
        mock_get.return_value = _mock_resp(
            status=200, headers={}, json_data=[])
        provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-e4c")
        args, kwargs = mock_get.call_args
        assert args[0].endswith("/v2/nfes_recebidas")
        assert "completa" not in kwargs["params"]

    def test_tipo_cte_continua_bloqueado(self, provider_com_token):
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "cte"}, "fo-e4c")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TIPO_NAO_SUPORTADO"

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_no_item_nao_derruba_lote_nfse(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "50"},
            json_data=[_item_nfse(chave="valida"),
                       {"status": 1}])  # item sem chave — cai em erros
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert r["ok"] is True
        assert len(r["documentos"]) == 1
        assert len(r["erros"]) == 1
        assert r["erros"][0]["codigo"] == "FOCUS_ITEM_INVALIDO"


# ── XML via url_xml ─────────────────────────────────────────────────────────
class TestGovFetchNfseXmlUrl:
    @patch("providers.focusnfe_provider.requests.get")
    def test_url_xml_sucesso_vira_completo(self, mock_get, provider_com_token):
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_nfse(with_url_xml=True)])
        xml_ok = _mock_resp(status=200, text="<CompNfse><Nfse/></CompNfse>")
        mock_get.side_effect = [listagem, xml_ok]
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        d = r["documentos"][0]
        assert d["status_xml"] == "COMPLETO"
        assert d["xml_bruto"].startswith("<CompNfse>")
        assert len(d["xml_hash_sha256"]) == 64
        assert "xml_pending" not in d
        assert r["xmls_baixados"] == 1
        assert r["xmls_pendentes"] == 0

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_xml_404_vira_resumo_pending(self, mock_get, provider_com_token):
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_nfse(with_url_xml=True)])
        xml_404 = _mock_resp(status=404, text="")
        mock_get.side_effect = [listagem, xml_404]
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        d = r["documentos"][0]
        assert d["status_xml"] == "RESUMO"
        assert d["xml_pending"] is True
        assert "xml_bruto" not in d
        assert r["xmls_pendentes"] == 1

    @patch("providers.focusnfe_provider.requests.get")
    def test_url_xml_timeout_batch_continua(self, mock_get, provider_com_token):
        docs = [_item_nfse(chave="A" * 40, with_url_xml=True),
                _item_nfse(chave="B" * 40, with_url_xml=True)]
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "50"},
                              json_data=docs)
        xml_ok = _mock_resp(status=200, text="<CompNfse/>")
        mock_get.side_effect = [
            listagem, requests.exceptions.Timeout(), xml_ok,
        ]
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert r["ok"] is True
        assert r["xmls_baixados"] == 1
        assert r["xmls_pendentes"] == 1

    @patch("providers.focusnfe_provider.requests.get")
    def test_sem_url_xml_permanece_resumo(self, mock_get, provider_com_token):
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_nfse(with_url_xml=False)])
        mock_get.side_effect = [listagem]
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert mock_get.call_count == 1  # so listagem, sem GET XML
        d = r["documentos"][0]
        assert d["status_xml"] == "RESUMO"
        assert "xml_pending" not in d

    @patch("providers.focusnfe_provider.requests.get")
    def test_cancelada_e_substituida_nao_baixam_xml(self, mock_get, provider_com_token):
        docs = [_item_nfse(chave="C" * 40, status=2, with_url_xml=True),
                _item_nfse(chave="D" * 40, status=3, with_url_xml=True)]
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "50"},
                              json_data=docs)
        mock_get.side_effect = [listagem]
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert mock_get.call_count == 1
        assert all(d.get("status_xml") == "RESUMO" for d in r["documentos"])
        assert r["xmls_baixados"] == 0


# ── baixar_xml_nfse direto ──────────────────────────────────────────────────
class TestBaixarXmlNfse:
    @patch("providers.focusnfe_provider.requests.get")
    def test_200_devolve_xml(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, text="<CompNfse><Nfse/></CompNfse>")
        r = provider_com_token.baixar_xml_nfse("https://exemplo/nfse.xml")
        assert r["ok"] is True
        assert r["xml_bruto"].startswith("<CompNfse>")
        args, kwargs = mock_get.call_args
        assert kwargs["headers"]["Accept"] == "application/xml"
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        assert kwargs["allow_redirects"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_302_segundo_get_sem_authorization(self, mock_get, provider_com_token):
        resp1 = _mock_resp(status=302, headers={"Location": "https://presigned/x.xml"})
        resp2 = _mock_resp(status=200, text="<CompNfse/>")
        mock_get.side_effect = [resp1, resp2]
        r = provider_com_token.baixar_xml_nfse("https://exemplo/nfse.xml")
        assert r["ok"] is True
        primeiro = mock_get.call_args_list[0]
        segundo = mock_get.call_args_list[1]
        assert "Authorization" in primeiro.kwargs["headers"]
        assert "Authorization" not in segundo.kwargs["headers"]

    @patch("providers.focusnfe_provider.requests.get")
    def test_404_nao_encontrado(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=404, text="")
        r = provider_com_token.baixar_xml_nfse("https://exemplo/nfse.xml")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_NAO_ENCONTRADO"

    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout(self, mock_get, provider_com_token):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider_com_token.baixar_xml_nfse("https://exemplo/nfse.xml")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_TIMEOUT"

    def test_url_vazia(self, provider_com_token):
        r = provider_com_token.baixar_xml_nfse("")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"

    def test_sem_token(self, monkeypatch):
        monkeypatch.delenv("FOCUSNFE_TOKEN", raising=False)
        p = FocusNFeProvider()
        r = p.baixar_xml_nfse("https://exemplo/nfse.xml")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TOKEN_AUSENTE"

    @patch("providers.focusnfe_provider.requests.get")
    def test_authorization_nao_vaza(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(status=200, text="<xml/>")
        r = provider_com_token.baixar_xml_nfse("https://exemplo/nfse.xml")
        payload = str(r).lower()
        assert "abcdef123456" not in payload
        assert "authorization" not in payload
        assert "basic " not in payload


# ── empresa_nao_habilitada → codigo dedicado ────────────────────────────────
class TestEmpresaNaoHabilitada:
    @patch("providers.focusnfe_provider.requests.get")
    def test_403_com_codigo_dedicado(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=403,
            json_data={"codigo": "empresa_nao_habilitada",
                       "mensagem": "CNPJ 07219398000109 nao esta habilitado."})
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse"}, "fo-e4c")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_NFSE_NAO_HABILITADA"
        assert "suporte" in r["erro"].lower()

    @patch("providers.focusnfe_provider.requests.get")
    def test_403_generico_cai_em_forbidden(self, mock_get, provider_com_token):
        # Sem `codigo:empresa_nao_habilitada` → codigo generico FORBIDDEN.
        mock_get.return_value = _mock_resp(
            status=403,
            json_data={"codigo": "outro_erro", "mensagem": "x"})
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse"}, "fo-e4c")
        assert r["codigo"] == "FOCUS_FORBIDDEN"

    @patch("providers.focusnfe_provider.requests.get")
    def test_403_body_nao_json_cai_em_forbidden(self, mock_get, provider_com_token):
        # Focus devolveu 403 sem JSON parseavel — nao pode explodir.
        mock_get.return_value = _mock_resp(status=403)  # json_data=None
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse"}, "fo-e4c")
        assert r["codigo"] == "FOCUS_FORBIDDEN"


# ── Seguranca envelope NFSe ──────────────────────────────────────────────────
class TestSegurancaEnvelopeNfse:
    @patch("providers.focusnfe_provider.requests.get")
    def test_envelope_nfse_nao_vaza_token(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "42"},
            json_data=[_item_nfse()])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        s = str(r).lower()
        assert "abcdef123456" not in s
        assert "authorization" not in s
        assert "basic " not in s
