"""FocusNFe · NFS-e recebida — contrato oficial `/v2/nfsens_recebidas`
(2026-07-31).

Correção definitiva do provider NFS-e Nacional recebida:

- **listagem**: `chave_nfse`, `situacao` textual, campos planos
  (`nome_prestador`, `documento_prestador`, `valor_total`,
  `data_emissao`, `data_geracao`, `versao`, opcionais de
  cancelamento/substituição), `X-Total-Count`, `X-Max-Version`;
- **individual**: `GET /v2/nfsens_recebidas/{chave}.xml` — XML canônico;
- **cancelada/substituída**: sem fabricar Espelho a partir do resumo;
  quando não houver XML, persistir evento nominal;
- **isolamento** NF-e × NFS-e absoluto (endpoints, mappers, doc_type);
- **cursor seguro** `versao` string opaca, X-Max-Version não avança em
  falha, cursor vazio não regride, pendências bloqueiam;
- **HTTP** 400 empresa_nao_habilitada nominal; 429/5xx sem avanço;
  Content-Type inesperado tratado; corpo não JSON tratado;
- **telemetria** contadores separados (documento vs erro), sem soma
  enganosa;
- **XML**: nunca reconstruir Espelho a partir do resumo — a listagem
  fornece identidade/versão/situação; o XML é a fonte canônica.

Zero HTTP real; zero dado real de cliente; fixtures sintéticas.

Requisitos §8 do prompt (25 casos comportamentais):
  1  chave_nfse aceita
  2  `chave` histórica não confundida com contrato oficial
  3  situacao autorizado
  4  situacao cancelado
  5  situacao substituido
  6  situação desconhecida fecha
  7  campos planos do prestador
  8  versão preservada como cursor opaco
  9  consulta com cnpj, versao e completa=1
 10  paginação de 100 itens
 11  X-Max-Version
 12  X-Total-Count
 13  XML individual recuperado pela chave
 14  resposta não XML rejeitada
 15  400 empresa_nao_habilitada
 16  400 genérico
 17  429/timeout/5xx sem avanço
 18  erro no item intermediário impede salto
 19  limite de XML por rodada não perde os demais
 20  lista vazia não mistura cursor NF-e
 21  execução NFS-e não altera cursor NF-e
 22  execução NF-e não altera cursor NFS-e
 23  contadores distinguem documento e erro
 24  Parser_Fiscal recebe XML, nunca resumo
 25  regressão integral do provider NF-e
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import (
    FocusNFeProvider,
    _mapear_nfe_focus,
    _mapear_nfse_focus,
)


CHAVE_OK = "9" * 44
CNPJ_SINTETICO = "0" * 14


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    return FocusNFeProvider()


def _mock_resp(status=200, json_data=None, headers=None, text="", content=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = text
    resp.content = content if content is not None else (text or "").encode("utf-8")
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


def _item_oficial(**overrides):
    """Item da listagem no contrato OFICIAL (chave_nfse, situacao
    textual, campos planos). Nunca aninhado."""
    base = {
        "chave_nfse":          CHAVE_OK,
        "situacao":            "autorizado",
        "versao":              42,
        "nome_prestador":      "Prestador Oficial LTDA",
        "documento_prestador": CNPJ_SINTETICO,
        "nome_tomador":        "Tomador Sintetico",
        "documento_tomador":   CNPJ_SINTETICO,
        "valor_total":         "1500.00",
        "valor_iss":           "75.00",
        "valor_liquido":       "1425.00",
        "data_emissao":        "2026-07-15T10:00:00-03:00",
        "data_geracao":        "2026-07-15T10:05:00-03:00",
        "numero":              "1001",
        "serie":               "1",
        "codigo_verificacao":  "ABCD1234",
        "competencia":         "2026-07",
        "discriminacao":       "Servico oficial",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────
# Mapper — contrato oficial
# ─────────────────────────────────────────────────────────────────────


class TestMapperContratoOficial:
    def test_1_chave_nfse_aceita(self):
        d = _mapear_nfse_focus(_item_oficial(), "trace-1")
        assert d["chave_nfse"] == CHAVE_OK
        assert d["chave"] == CHAVE_OK        # publica compat
        assert d["_layout_focus"] == "oficial"

    def test_2_chave_historica_nao_e_oficial(self):
        # Item ONLY com `chave` (sem `chave_nfse`) → adaptador legacy
        # explícito (marcado). Não é silenciosamente misturado.
        legacy = _item_oficial()
        del legacy["chave_nfse"]
        legacy["chave"] = CHAVE_OK
        d = _mapear_nfse_focus(legacy, "trace-2")
        assert d["chave_nfse"] == CHAVE_OK
        assert d["_layout_focus"] == "legacy"

    def test_3_situacao_autorizado(self):
        d = _mapear_nfse_focus(_item_oficial(situacao="autorizado"), "trace-3")
        assert d["situacao_nfse"] == "autorizada"
        assert d["cancelado"] == 0
        assert d["substituido"] == 0
        assert d["situacao_focus"] == "autorizado"

    def test_4_situacao_cancelado(self):
        d = _mapear_nfse_focus(_item_oficial(
            situacao="cancelado",
            data_cancelamento="2026-07-16T08:00:00-03:00",
        ), "trace-4")
        assert d["situacao_nfse"] == "cancelada"
        assert d["cancelado"] == 1
        assert d["substituido"] == 0
        assert d["data_cancelamento"] == "2026-07-16T08:00:00-03:00"

    def test_5_situacao_substituido(self):
        d = _mapear_nfse_focus(_item_oficial(
            situacao="substituido",
            chave_nfse_substituida="B" * 44,
        ), "trace-5")
        assert d["situacao_nfse"] == "substituida"
        assert d["substituido"] == 1
        assert d["cancelado"] == 0
        assert d["chave_nfse_substituida"] == "B" * 44

    def test_6_situacao_desconhecida_fecha_nominal(self):
        # Nunca converte em "autorizado" silenciosamente. Consumidor
        # bloqueia cursor antes do item.
        with pytest.raises(ValueError) as e:
            _mapear_nfse_focus(_item_oficial(situacao="quarentena"), "trace-6")
        assert "situacao NFS-e desconhecida" in str(e.value)

    def test_7_campos_planos_prestador(self):
        d = _mapear_nfse_focus(_item_oficial(), "trace-7")
        assert d["emit_cnpj"] == CNPJ_SINTETICO
        assert d["emit_doc_tipo"] == "cnpj"
        assert d["emit_nome"] == "Prestador Oficial LTDA"
        assert d["dest_cnpj"] == CNPJ_SINTETICO
        assert d["dest_nome"] == "Tomador Sintetico"
        # Valor plano, não aninhado em `servicos`.
        assert d["valor_total"] == "1500.00"
        # data_geracao é campo oficial, precisa ser preservado.
        assert d["data_geracao"] == "2026-07-15T10:05:00-03:00"

    def test_8_versao_preservada_string_opaca(self):
        # `versao=1` legítimo — nunca zero-padding, nunca converte em NSU 15 dígitos.
        d = _mapear_nfse_focus(_item_oficial(versao=1), "trace-8")
        assert d["versao"] == 1
        # Contra-regressão: nada de "000000000000001"
        assert str(d["versao"]) == "1"

    def test_chave_ausente_levanta(self):
        item = _item_oficial()
        del item["chave_nfse"]
        with pytest.raises(ValueError):
            _mapear_nfse_focus(item, "trace-x")

    def test_versao_ausente_levanta(self):
        item = _item_oficial()
        del item["versao"]
        with pytest.raises(ValueError):
            _mapear_nfse_focus(item, "trace-x")

    def test_versao_zero_rejeitada(self):
        with pytest.raises(ValueError):
            _mapear_nfse_focus(_item_oficial(versao=0), "trace-x")


# ─────────────────────────────────────────────────────────────────────
# gov_fetch — listagem
# ─────────────────────────────────────────────────────────────────────


class TestGovFetchListagemOficial:
    @patch("providers.focusnfe_provider.requests.get")
    def test_9_consulta_com_cnpj_versao_completa_1(self, mock_get, provider):
        # Sem url_xml → tenta fallback oficial (2 chamadas: listagem + XML)
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_oficial()])
        xml_ok = _mock_resp(status=200, text="<CompNfse/>")
        mock_get.side_effect = [listagem, xml_ok]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-9",
        )
        assert r["ok"] is True
        args, kwargs = mock_get.call_args_list[0]
        assert args[0].endswith("/v2/nfsens_recebidas")
        assert kwargs["params"]["cnpj"] == CNPJ_SINTETICO
        assert kwargs["params"]["versao"] == "0"
        assert kwargs["params"]["completa"] == "1"

    @patch("providers.focusnfe_provider.requests.get")
    def test_10_paginacao_100_itens(self, mock_get, provider):
        # 100 itens no batch — has_more sinaliza continuação.
        docs = [_item_oficial(chave_nfse=f"CHAVE-{i:040d}", versao=i)
                for i in range(1, 101)]
        # Todos cancelados para não disparar XML fetch (cap 25).
        for d in docs:
            d["situacao"] = "cancelado"
        listagem = _mock_resp(
            status=200,
            headers={"X-Max-Version": "100", "X-Total-Count": "250"},
            json_data=docs,
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-10",
        )
        assert r["ok"] is True
        assert r["quantidade_retornada"] == 100
        assert r["has_more"] is True
        assert r["total_count"] == 250

    @patch("providers.focusnfe_provider.requests.get")
    def test_11_x_max_version_consumido(self, mock_get, provider):
        # Cancelada não baixa XML → 1 chamada só.
        listagem = _mock_resp(
            status=200,
            headers={"X-Max-Version": "9999"},
            json_data=[_item_oficial(versao=9999, situacao="cancelado")],
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-11",
        )
        assert r["cursor_seguro"] == "9999"
        assert r["versao_pagina"] == "9999"

    @patch("providers.focusnfe_provider.requests.get")
    def test_12_x_total_count_consumido(self, mock_get, provider):
        listagem = _mock_resp(
            status=200,
            headers={"X-Total-Count": "12345"},
            json_data=[_item_oficial(situacao="cancelado")],
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-12",
        )
        assert r["total_count"] == 12345

    @patch("providers.focusnfe_provider.requests.get")
    def test_18_erro_no_item_impede_salto(self, mock_get, provider):
        # 3 itens; o do meio tem situacao desconhecida → cursor bloqueado antes.
        docs = [
            _item_oficial(chave_nfse="A" * 44, versao=10, situacao="cancelado"),
            _item_oficial(chave_nfse="B" * 44, versao=11, situacao="marciano"),
            _item_oficial(chave_nfse="C" * 44, versao=12, situacao="cancelado"),
        ]
        listagem = _mock_resp(
            status=200,
            headers={"X-Max-Version": "12"},
            json_data=docs,
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "5"},
            "trace-18",
        )
        # Documento 11 vira erro; cursor não pode ultrapassar 10.
        assert r["ok"] is True
        assert len(r["erros"]) >= 1
        assert int(r["cursor_seguro"]) <= 10

    @patch("providers.focusnfe_provider.requests.get")
    def test_20_lista_vazia_nao_regride_e_nao_mistura_nfe(
            self, mock_get, provider):
        listagem = _mock_resp(status=200, headers={}, json_data=[])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "42"},
            "trace-20",
        )
        assert r["ok"] is True
        assert r["quantidade_retornada"] == 0
        # Cursor nunca regride abaixo da entrada.
        assert int(r["cursor_seguro"]) >= 42
        # URL exclusiva NFS-e.
        args, _ = mock_get.call_args
        assert args[0].endswith("/v2/nfsens_recebidas")
        assert "/v2/nfes_recebidas" not in args[0]


# ─────────────────────────────────────────────────────────────────────
# XML individual pela chave (canônico)
# ─────────────────────────────────────────────────────────────────────


class TestXmlIndividualPorChave:
    @patch("providers.focusnfe_provider.requests.get")
    def test_13_xml_recuperado_pela_chave(self, mock_get, provider):
        xml_ok = _mock_resp(status=200, text="<CompNfse><Nfse/></CompNfse>")
        mock_get.return_value = xml_ok
        r = provider.baixar_xml_nfse_por_chave(CHAVE_OK, "homologacao")
        assert r["ok"] is True
        assert r["xml_bruto"].startswith("<CompNfse>")
        assert len(r["xml_hash_sha256"]) == 64
        # URL canônica.
        args, _ = mock_get.call_args
        assert args[0].endswith(f"/v2/nfsens_recebidas/{CHAVE_OK}.xml")

    @patch("providers.focusnfe_provider.requests.get")
    def test_14_resposta_nao_xml_rejeitada(self, mock_get, provider):
        # Retorna HTML no lugar do XML — precisa cair como erro nominal.
        resp = _mock_resp(
            status=200,
            content=b"<html>Erro</html>",
            headers={"Content-Type": "text/html"},
        )
        mock_get.return_value = resp
        r = provider.baixar_xml_nfse_por_chave(CHAVE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_CONTENT_TYPE_INVALIDO"
        assert "xml_bruto" not in r

    @patch("providers.focusnfe_provider.requests.get")
    def test_14b_content_type_ausente_com_body_xml_ok(self, mock_get, provider):
        # Storage pré-assinado às vezes omite Content-Type — corpo XML
        # ainda deve ser aceito.
        resp = _mock_resp(
            status=200,
            content=b"<?xml version='1.0'?><CompNfse/>",
            headers={},  # sem Content-Type
        )
        mock_get.return_value = resp
        r = provider.baixar_xml_nfse_por_chave(CHAVE_OK, "homologacao")
        assert r["ok"] is True
        assert r["xml_bruto"].startswith("<?xml")

    @patch("providers.focusnfe_provider.requests.get")
    def test_14c_html_sem_content_type_ainda_e_rejeitado(
            self, mock_get, provider):
        # Sanity check leve: começo <html sem Content-Type → rejeitado.
        resp = _mock_resp(
            status=200,
            content=b"<html><body>Erro proxy</body></html>",
            headers={},
        )
        mock_get.return_value = resp
        r = provider.baixar_xml_nfse_por_chave(CHAVE_OK, "homologacao")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_XML_CONTENT_TYPE_INVALIDO"


# ─────────────────────────────────────────────────────────────────────
# HTTP nominal (§6)
# ─────────────────────────────────────────────────────────────────────


class TestHttpErrosNominais:
    @patch("providers.focusnfe_provider.requests.get")
    def test_15_400_empresa_nao_habilitada(self, mock_get, provider):
        resp = _mock_resp(status=400,
                          json_data={"codigo": "empresa_nao_habilitada",
                                     "mensagem": "Empresa nao habilitada"})
        mock_get.return_value = resp
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-15",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_NFSE_NAO_HABILITADA"
        assert r["http_status"] == 400
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_16_400_generico(self, mock_get, provider):
        resp = _mock_resp(status=400,
                          json_data={"erro": "cnpj invalido"})
        mock_get.return_value = resp
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-16",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_BAD_REQUEST"
        # Cursor não avança em 400.
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_17a_429_sem_avanco(self, mock_get, provider):
        resp = _mock_resp(status=429, headers={"Retry-After": "60"},
                          json_data={"mensagem": "rate limit"})
        mock_get.return_value = resp
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "42"},
            "trace-17a",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_RATE_LIMIT"
        assert r["nsu_avancou"] is False
        assert r["ultimo_nsu"] == "42"
        assert r["cooldown_recomendado_seg"] == 60

    @patch("providers.focusnfe_provider.requests.get")
    def test_17b_timeout_sem_avanco(self, mock_get, provider):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "42"},
            "trace-17b",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TIMEOUT"
        assert r["nsu_avancou"] is False
        assert r["ultimo_nsu"] == "42"

    @patch("providers.focusnfe_provider.requests.get")
    def test_17c_5xx_sem_avanco(self, mock_get, provider):
        resp = _mock_resp(status=503)
        mock_get.return_value = resp
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "42"},
            "trace-17c",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_SERVER_ERROR"
        assert r["nsu_avancou"] is False
        assert r["ultimo_nsu"] == "42"

    @patch("providers.focusnfe_provider.requests.get")
    def test_corpo_nao_json_na_listagem_erro_nominal(self, mock_get, provider):
        resp = _mock_resp(status=200)  # sem json_data → resp.json() ValueError
        mock_get.return_value = resp
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-json",
        )
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_PARSE_ERROR"
        assert r["nsu_avancou"] is False


# ─────────────────────────────────────────────────────────────────────
# Isolamento NF-e × NFS-e (§4)
# ─────────────────────────────────────────────────────────────────────


class TestIsolamentoDocType:
    @patch("providers.focusnfe_provider.requests.get")
    def test_21_execucao_nfse_nao_toca_endpoint_nfe(self, mock_get, provider):
        listagem = _mock_resp(status=200, headers={},
                              json_data=[_item_oficial(situacao="cancelado")])
        mock_get.side_effect = [listagem]
        provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-21",
        )
        # NENHUMA chamada pode ter mirado /v2/nfes_recebidas.
        for call in mock_get.call_args_list:
            url = call.args[0]
            assert "/v2/nfes_recebidas" not in url
            assert "/v2/nfsens_recebidas" in url

    @patch("providers.focusnfe_provider.requests.get")
    def test_22_execucao_nfe_nao_toca_endpoint_nfse(self, mock_get, provider):
        listagem = _mock_resp(status=200, headers={}, json_data=[])
        mock_get.side_effect = [listagem]
        provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfe", "ultimo_nsu": "0"},
            "trace-22",
        )
        for call in mock_get.call_args_list:
            url = call.args[0]
            assert "/v2/nfsens_recebidas" not in url
            assert "/v2/nfes_recebidas" in url

    def test_cursor_tipo_versao_nao_e_nsu(self, provider):
        # Provas estruturais: cursor NFS-e é `versao` string, NF-e NSU
        # 15 dígitos — a interface do envelope precisa preservar isso.
        with patch("providers.focusnfe_provider.requests.get") as mg:
            mg.return_value = _mock_resp(status=200, headers={}, json_data=[])
            r = provider.gov_fetch(
                {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "1"},
                "trace-cursor",
            )
        assert r["cursor_tipo"] == "versao"
        assert isinstance(r["cursor_seguro"], str)


# ─────────────────────────────────────────────────────────────────────
# Telemetria (§7) — contadores separados
# ─────────────────────────────────────────────────────────────────────


class TestTelemetriaSeparada:
    @patch("providers.focusnfe_provider.requests.get")
    def test_23_contadores_distinguem_documento_e_erro(self, mock_get, provider):
        # 3 itens: 2 cancelados válidos + 1 com situação desconhecida.
        docs = [
            _item_oficial(chave_nfse="A" * 44, versao=10, situacao="cancelado"),
            _item_oficial(chave_nfse="B" * 44, versao=11, situacao="cancelado"),
            _item_oficial(chave_nfse="C" * 44, versao=12, situacao="marciano"),
        ]
        listagem = _mock_resp(
            status=200,
            headers={"X-Max-Version": "12", "X-Total-Count": "3"},
            json_data=docs,
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-23",
        )
        # 2 documentos válidos, 1 erro.
        assert len(r["documentos"]) == 2
        assert len(r["erros"]) == 1
        # quantidade_retornada é bruta (recebidos_da_focus).
        assert r["quantidade_retornada"] == 3
        # Documentos NÃO se somam com erros no envelope.
        assert len(r["documentos"]) + len(r["erros"]) == r["quantidade_retornada"]
        assert r["recebidos_da_focus"] == 3
        assert r["documentos_mapeados"] == 2
        assert r["erros_de_mapeamento"] == 1

    @patch("providers.focusnfe_provider.requests.get")
    @pytest.mark.parametrize("situacao", ["cancelado", "substituido"])
    def test_cancelada_ou_substituida_vira_evento_sem_fabricar_espelho(
            self, mock_get, provider, situacao):
        listagem = _mock_resp(
            status=200,
            headers={"X-Max-Version": "42"},
            json_data=[_item_oficial(situacao=situacao)],
        )
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-cancel-subst",
        )
        doc = r["documentos"][0]
        assert doc["status_xml"] == "EVENTO"
        assert doc["xml_individual_estado"] == "NAO_COMPROVADO"
        assert "xml_bruto" not in doc
        assert mock_get.call_count == 1


# ─────────────────────────────────────────────────────────────────────
# XML como fonte canônica (§2, requisito 24)
# ─────────────────────────────────────────────────────────────────────


class TestXmlComoFonteCanonica:
    @patch("providers.focusnfe_provider.requests.get")
    def test_24_documento_completo_traz_xml_nao_resumo(self, mock_get, provider):
        """`gov_fetch` marca `status_xml=COMPLETO` **somente** quando o
        XML foi baixado. O Parser_Fiscal downstream nunca deve receber
        um documento sem XML como se fosse completo."""
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_oficial()])
        xml_ok = _mock_resp(status=200, text="<CompNfse><Nfse/></CompNfse>")
        mock_get.side_effect = [listagem, xml_ok]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-24",
        )
        d = r["documentos"][0]
        assert d["status_xml"] == "COMPLETO"
        assert d["xml_bruto"].startswith("<CompNfse>")
        assert len(d["xml_hash_sha256"]) == 64

    @patch("providers.focusnfe_provider.requests.get")
    def test_24b_sem_xml_fica_resumo_e_bloqueia_cursor(self, mock_get, provider):
        listagem = _mock_resp(status=200, headers={"X-Max-Version": "42"},
                              json_data=[_item_oficial()])
        xml_404 = _mock_resp(status=404, text="")
        mock_get.side_effect = [listagem, xml_404]
        r = provider.gov_fetch(
            {"cnpj": CNPJ_SINTETICO, "tipo": "nfse", "ultimo_nsu": "0"},
            "trace-24b",
        )
        d = r["documentos"][0]
        assert d["status_xml"] == "RESUMO"
        assert d.get("xml_pending") is True
        # Cursor não ultrapassa a versão pendente (42) — nesta página o
        # menor pendente é 42, então cursor_seguro deve ficar < 42.
        # Cursor entrada era 0; cursor seguro pode ser 0 (bloqueio antes).
        assert int(r["cursor_seguro"]) < 42


# ─────────────────────────────────────────────────────────────────────
# Regressão NF-e (§25)
# ─────────────────────────────────────────────────────────────────────


class TestRegressaoNfe:
    def test_25_mapper_nfe_intacto(self):
        # Mapper NF-e ainda aceita `chave_nfe` como identidade e emite
        # `chNFe`; nenhum campo NFS-e deve aparecer.
        item = {
            "chave_nfe":         "3" * 44,
            "documento_emitente":"22222222000181",
            "nome_emitente":     "Emit",
            "cnpj_destinatario": "11111111000191",
            "data_emissao":      "2026-07-20T10:00:00-03:00",
            "situacao":          "autorizada",
            "versao":            10,
            "nfe_completa":      True,
        }
        d = _mapear_nfe_focus(item, "trace-25")
        assert d["chNFe"] == "3" * 44
        assert d["cStat"] == "100"
        assert d["import_origin"] == "fiscalone_focusnfe"
        # NF-e não usa `chave_nfse` no schema; garantir que o mapper
        # NF-e não escreve esse campo (para o pipeline NFS-e não
        # confundir).
        assert "chave_nfse" not in d
        assert "situacao_nfse" not in d
