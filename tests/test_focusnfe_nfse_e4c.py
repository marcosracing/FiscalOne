"""FocusNFe · Fase E4c — NFSe Nacional recebidas via FocusNFe.

Cobre:
  - `gov_fetch(tipo="nfse")` monta URL `/v2/nfsens_recebidas` e envia
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
    _mapear_nfe_focus,
    _mapear_nfse_focus,
    _normalizar_iss_retido_nfse,
    _normalizar_servicos_nfse,
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
    def test_url_nfsens_recebidas_com_completa_1(self, mock_get, provider_com_token):
        mock_get.return_value = _mock_resp(
            status=200, headers={"X-Max-Version": "42", "X-Total-Count": "1"},
            json_data=[_item_nfse()])
        r = provider_com_token.gov_fetch(
            {"cnpj": "07219398000109", "tipo": "nfse", "ultimo_nsu": "0"},
            "fo-e4c")
        assert r["ok"] is True
        args, kwargs = mock_get.call_args
        assert args[0].endswith("/v2/nfsens_recebidas")
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


# ── Fix 2026-07-18 — servicos como list/dict/None + iss_retido normalizado ──
def _item_nfse_servicos(servicos_val,
                        chave="ABC123456789012345678901234567890123456789012"):
    """Item NfseRecebida com `servicos` customizado (dict/list/None/etc.)."""
    return {
        "chave":       chave,
        "versao":      99,
        "status":      1,
        "numero":      "2001",
        "serie":       "1",
        "data_emissao": "2026-07-18T10:00:00-03:00",
        "prestador":   {"cnpj": "12345678000199",
                        "razao_social": "Prestador LTDA"},
        "tomador":     {"cnpj": "07219398000109",
                        "razao_social": "Tomador SA"},
        "servicos":    servicos_val,
    }


class TestServicosListaOuDict:
    def test_t1_servicos_lista_soma_decimal(self):
        """T1 — lista com 2 itens: soma valores, iss_retido=True, discriminacao
        concatenada, item_lista_servico/codigo_cnae preenchidos."""
        servicos_list = [
            {"valor_servicos": "1000.00", "valor_iss": "50.00",
             "valor_liquido": "950.00",  "iss_retido": True,
             "discriminacao": "Servico A",
             "item_lista_servico": "01.05", "codigo_cnae": "6201501"},
            {"valor_servicos": "1000.00", "valor_iss": "50.00",
             "valor_liquido": "950.00",  "iss_retido": True,
             "discriminacao": "Servico B",
             "item_lista_servico": "01.06", "codigo_cnae": "6202300"},
        ]
        d = _mapear_nfse_focus(_item_nfse_servicos(servicos_list), "fix")
        assert d["valor_total"]   == "2000.00"
        assert d["valor_iss"]     == "100.00"
        assert d["valor_liquido"] == "1900.00"
        assert d["iss_retido"] is True
        assert "Servico A" in d["discriminacao"]
        assert "Servico B" in d["discriminacao"]
        assert " | " in d["discriminacao"]
        assert d["item_lista_servico"] == "01.05"
        assert d["codigo_cnae"]        == "6201501"

    def test_t2_servicos_dict_legado_preservado(self):
        """T2 — dict legado: mesmos campos, sem regressao."""
        servicos_dict = {"valor_servicos": "1500.00",
                         "valor_iss":      "75.00",
                         "valor_liquido":  "1425.00",
                         "iss_retido":     True,
                         "discriminacao":  "Servico legado",
                         "item_lista_servico": "01.07",
                         "codigo_cnae":    "6203100"}
        original = dict(servicos_dict)  # copia para checar nao-mutacao
        d = _mapear_nfse_focus(_item_nfse_servicos(servicos_dict), "fix")
        assert d["valor_total"]   == "1500.00"
        assert d["valor_iss"]     == "75.00"
        assert d["valor_liquido"] == "1425.00"
        assert d["iss_retido"] is True
        assert d["discriminacao"] == "Servico legado"
        assert d["item_lista_servico"] == "01.07"
        assert d["codigo_cnae"]        == "6203100"
        # nao mutou o dict original recebido
        assert servicos_dict == original

    def test_t3_servicos_lista_vazia(self):
        """T3 — lista vazia: campos vazios/zero, sem excecao."""
        d = _mapear_nfse_focus(_item_nfse_servicos([]), "fix")
        assert d["valor_total"]   == ""
        assert d["valor_iss"]     == ""
        assert d["valor_liquido"] == ""
        assert d["iss_retido"] is False
        assert d["discriminacao"] == ""
        assert d["item_lista_servico"] == ""
        assert d["codigo_cnae"]        == ""

    def test_t4_servicos_none(self):
        """T4 — None: campos vazios/zero, sem excecao."""
        d = _mapear_nfse_focus(_item_nfse_servicos(None), "fix")
        assert d["valor_total"]   == ""
        assert d["valor_iss"]     == ""
        assert d["valor_liquido"] == ""
        assert d["iss_retido"] is False
        assert d["discriminacao"] == ""
        assert d["item_lista_servico"] == ""
        assert d["codigo_cnae"]        == ""


class TestIssRetidoNormalizacao:
    def test_t5_true(self):
        assert _normalizar_iss_retido_nfse(True) is True

    def test_t6_string_numerica_positiva(self):
        assert _normalizar_iss_retido_nfse("75.00") is True

    def test_t7_zero_int(self):
        assert _normalizar_iss_retido_nfse(0) is False

    def test_t8_false(self):
        assert _normalizar_iss_retido_nfse(False) is False

    def test_variantes_string_truthy(self):
        for s in ("true", "TRUE", "1", "sim", "SIM", "S", "s"):
            assert _normalizar_iss_retido_nfse(s) is True

    def test_variantes_falsy(self):
        for v in (None, "", "0", "0.00", "false", "nao", "n", "-1"):
            assert _normalizar_iss_retido_nfse(v) is False

    def test_string_numero_negativo_falso(self):
        assert _normalizar_iss_retido_nfse("-10.5") is False

    def test_float_positivo(self):
        assert _normalizar_iss_retido_nfse(0.01) is True


class TestIssRetidoAgregadoLista:
    def test_t9_primeiro_false_segundo_true_agrega_true(self):
        """T9 — lista com item1 iss_retido=False e item2 True → True."""
        servicos_list = [
            {"valor_servicos": "500.00", "iss_retido": False},
            {"valor_servicos": "500.00", "iss_retido": True},
        ]
        d = _mapear_nfse_focus(_item_nfse_servicos(servicos_list), "fix")
        assert d["iss_retido"] is True

    def test_todos_false_agrega_false(self):
        servicos_list = [
            {"valor_servicos": "500.00", "iss_retido": False},
            {"valor_servicos": "500.00", "iss_retido": False},
        ]
        d = _mapear_nfse_focus(_item_nfse_servicos(servicos_list), "fix")
        assert d["iss_retido"] is False


class TestMapperCompletoPayloadRealista:
    def test_t10_payload_realista_lista_servicos(self):
        """T10 — mapper completo com payload NfseRecebida realista + servicos
        como lista. Contrato NFSe preservado (sem cStat SEFAZ)."""
        payload = {
            "chave":              "NFSE20260718000000000000000000000000000000AA",
            "versao":             123,
            "status":             1,
            "numero":             "5001",
            "serie":              "A",
            "codigo_verificacao": "XYZ9",
            "data_emissao":       "2026-07-18T09:30:00-03:00",
            "competencia":        "2026-07",
            "prestador": {"cnpj": "11222333000181",
                          "razao_social": "Alpha Servicos LTDA",
                          "inscricao_municipal": "99999"},
            "tomador":   {"cnpj": "07219398000109",
                          "razao_social": "Racing Logistica"},
            "servicos": [
                {"valor_servicos": "800.00",  "valor_iss": "40.00",
                 "valor_liquido": "760.00",   "iss_retido": True,
                 "discriminacao": "Consultoria mensal",
                 "item_lista_servico": "17.05", "codigo_cnae": "7020400"},
                {"valor_servicos": "1200.00", "valor_iss": "60.00",
                 "valor_liquido": "1140.00",  "iss_retido": True,
                 "discriminacao": "Analise tecnica"},
            ],
            "url_xml": "https://exemplo/nfse/xml/1.xml",
        }
        d = _mapear_nfse_focus(payload, "fix-t10")
        # Prestador → emit_*
        assert d["emit_cnpj"]     == "11222333000181"
        assert d["emit_doc_tipo"] == "cnpj"
        assert d["emit_nome"]     == "Alpha Servicos LTDA"
        assert d["emit_ie"]       == "99999"
        # Tomador → dest_*
        assert d["dest_cnpj"]     == "07219398000109"
        assert d["dest_nome"]     == "Racing Logistica"
        # Valores agregados
        assert d["valor_total"]   == "2000.00"
        assert d["valor_iss"]     == "100.00"
        assert d["valor_liquido"] == "1900.00"
        assert d["iss_retido"] is True
        assert "Consultoria mensal" in d["discriminacao"]
        assert "Analise tecnica"    in d["discriminacao"]
        assert d["item_lista_servico"] == "17.05"
        assert d["codigo_cnae"]        == "7020400"
        # Contrato NFSe (sem cStat SEFAZ)
        assert d["type"] == "nfse"
        assert d["doc_type"] == "nfse"
        assert d["import_origin"] == "fiscalone_focusnfe_nfse"
        assert d["status_sefaz"]  == "focusnfe"
        assert "cStat" not in d
        assert "xMotivo" not in d


class TestRegressaoNfe:
    def test_t11_mapper_nfe_intocado(self):
        """T11 — `_mapear_nfe_focus` mantem contrato E4a: cStat=100 para
        autorizada, CNPJ_emit vindo de `documento_emitente`."""
        item_nfe = {
            "chave_nfe":         "35240711222333000181550010000012341234567890",
            "situacao":          "autorizada",
            "documento_emitente": "11222333000181",
            "cnpj_destinatario": "07219398000109",
            "valor_total":       "999.99",
            "valor_icms":        "180.00",
            "numero":            "1234",
            "serie":             "1",
            "nome_emitente":     "Fornecedor XPTO",
            "data_emissao":      "2026-07-15T10:00:00-03:00",
            "protocolo":         "135260000001234",
            "versao":            77,
            "nfe_completa":      False,
            "tipo_nfe":          "entrada",
        }
        d = _mapear_nfe_focus(item_nfe, "regressao")
        assert d["chNFe"]         == "35240711222333000181550010000012341234567890"
        assert d["cStat"]         == "100"
        assert d["xMotivo"]       == "Resumo FocusNFe"
        assert d["status_xml"]    == "RESUMO"
        assert d["CNPJ_emit"]     == "11222333000181"
        assert d["CNPJ_dest"]     == "07219398000109"
        assert d["import_origin"] == "fiscalone_focusnfe"
        assert d["parser_version"] == "focus_v2"
        assert d["nfe_completa"]  is False
        assert d["cancelado"]     == 0


class TestNormalizadorHelperIsolado:
    def test_dict_retorna_copia(self):
        d = {"valor_servicos": "1", "iss_retido": True}
        out = _normalizar_servicos_nfse(d)
        assert out == d
        assert out is not d  # copia

    def test_lista_vazia_retorna_dict_vazio(self):
        assert _normalizar_servicos_nfse([]) == {}

    def test_lista_apenas_itens_invalidos_retorna_dict_vazio(self):
        assert _normalizar_servicos_nfse([1, "x", None]) == {}

    def test_none_retorna_dict_vazio(self):
        assert _normalizar_servicos_nfse(None) == {}

    def test_tipo_estranho_retorna_dict_vazio(self):
        assert _normalizar_servicos_nfse(123) == {}
        assert _normalizar_servicos_nfse("foo") == {}


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
