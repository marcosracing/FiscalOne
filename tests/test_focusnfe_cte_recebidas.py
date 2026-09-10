"""FocusNFe · lote de CT-e recebidas (gate CTE-RECEBIDOS, 2026-09-10).

Contrato oficial consultado em 2026-09-10
(`doc.focusnfe.com.br/reference/consultar_ctes_recebidas`):

  GET /v2/ctes_recebidas?cnpj=<14 digitos>&versao=<cursor>
  Headers de paginacao: X-Total-Count, X-Max-Version. Ate 100 por resposta.
  Item: nome_emitente, documento_emitente, cnpj_destinatario, chave_cte,
        valor_total, data_emissao, situacao, tipo_cte, versao, digest_value,
        carta_correcao, data_carta_correcao, data_cancelamento,
        justificativa_cancelamento.
  A listagem NAO traz XML — ele vem de /v2/ctes_recebidas/{chave}.xml.

Zero HTTP real, zero token no retorno.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider, _mapear_cte_focus

CHAVE = "35260307219398000109570010000423361000864018"
CHAVE_2 = "35260307219398000109570010000423371000864040"
XML_CTE = "<cteProc><CTe><infCte Id='CTe%s'/></CTe></cteProc>" % CHAVE


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    monkeypatch.delenv("FOCUSNFE_XML_BATCH_CAP", raising=False)
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


def _item(chave=CHAVE, versao=60, situacao="autorizada"):
    """Item exatamente com os nomes de campo do contrato oficial."""
    return {
        "nome_emitente": "TRANSPORTADORA EXEMPLO LTDA",
        "documento_emitente": "12345678000123",
        "cnpj_destinatario": "07219398000109",
        "chave_cte": chave,
        "valor_total": "2741.74",
        "data_emissao": "2026-09-01T10:00:00-03:00",
        "situacao": situacao,
        "tipo_cte": "normal",
        "versao": versao,
        "digest_value": "abc123",
    }


# ── mapper ───────────────────────────────────────────────────────────


class TestMapperCte:
    def test_mapeia_os_campos_oficiais(self):
        doc = _mapear_cte_focus(_item(), "t-1")
        assert doc["chCTe"] == CHAVE
        assert doc["chave"] == CHAVE, "alias generico p/ o normalizador do MapOne"
        assert doc["CNPJ_emit"] == "12345678000123"
        assert doc["CNPJ_dest"] == "07219398000109"
        assert doc["emit_nome"] == "TRANSPORTADORA EXEMPLO LTDA"
        assert doc["vCTe"] == "2741.74" and doc["valor_total"] == "2741.74"
        assert doc["dh_emi"].startswith("2026-09-01")
        assert doc["versao"] == 60
        assert doc["tipo_cte"] == "normal"
        assert doc["cStat"] == "100" and doc["cancelado"] == 0
        assert doc["status_xml"] == "RESUMO"
        assert doc["import_origin"] == "fiscalone_focusnfe"

    def test_situacao_dirige_cstat(self):
        assert _mapear_cte_focus(_item(situacao="cancelada"), "t")["cStat"] == "101"
        assert _mapear_cte_focus(_item(situacao="cancelada"), "t")["cancelado"] == 1
        assert _mapear_cte_focus(_item(situacao="denegada"), "t")["cStat"] == "110"

    def test_chave_ausente_e_erro(self):
        item = _item(); item.pop("chave_cte")
        with pytest.raises(ValueError):
            _mapear_cte_focus(item, "t")

    def test_campos_opcionais_so_quando_vem(self):
        doc = _mapear_cte_focus(_item(), "t")
        assert "data_cancelamento" not in doc and "carta_correcao" not in doc
        item = _item(situacao="cancelada")
        item["data_cancelamento"] = "2026-09-02"
        item["justificativa_cancelamento"] = "erro de digitacao do tomador"
        doc = _mapear_cte_focus(item, "t")
        assert doc["data_cancelamento"] == "2026-09-02"
        assert doc["justificativa_cancelamento"].startswith("erro")

    def test_nunca_vaza_token(self):
        item = _item(); item["authorization"] = "Basic c2VncmVkbw=="
        doc = _mapear_cte_focus(item, "t")
        assert "c2VncmVkbw==" not in doc["raw_json_focus"]


# ── lote ─────────────────────────────────────────────────────────────


class TestLoteCte:
    def _fetch(self, provider, itens, xml_ok=True, headers=None):
        lista = _mock_resp(200, json_data=itens,
                           headers=headers or {"X-Total-Count": str(len(itens)),
                                               "X-Max-Version": str(max(i["versao"] for i in itens))})
        xml = _mock_resp(200 if xml_ok else 404, text=XML_CTE if xml_ok else "",
                         headers={"Content-Type": "application/xml"})
        chamadas = []

        def _get(url, **kw):
            chamadas.append((url, kw.get("params")))
            return xml if url.endswith(".xml") else lista

        with patch.object(requests, "get", side_effect=_get):
            env = provider.gov_fetch(
                {"cnpj": "07219398000109", "tipo": "cte",
                 "ambiente": "producao", "ultimo_nsu": "0"}, "trace-cte")
        return env, chamadas

    def test_usa_o_endpoint_oficial_com_cnpj_e_versao(self, provider):
        env, chamadas = self._fetch(provider, [_item()])
        url, params = chamadas[0]
        assert url == "https://api.focusnfe.com.br/v2/ctes_recebidas"
        assert params == {"cnpj": "07219398000109", "versao": "0"}
        assert "completa" not in (params or {}), "completa é só da NFS-e"
        assert env["ok"] is True
        assert env["cursor_tipo"] == "versao"

    def test_homologacao_usa_o_host_de_homologacao(self, provider):
        lista = _mock_resp(200, json_data=[], headers={"X-Total-Count": "0"})
        with patch.object(requests, "get", return_value=lista) as g:
            provider.gov_fetch({"cnpj": "07219398000109", "tipo": "cte",
                                "ambiente": "homologacao", "ultimo_nsu": "0"}, "t")
        assert g.call_args[0][0] == "https://homologacao.focusnfe.com.br/v2/ctes_recebidas"

    def test_baixa_o_xml_de_cada_item_autorizado(self, provider):
        env, chamadas = self._fetch(provider, [_item(), _item(CHAVE_2, versao=61)])
        docs = env["documentos"]
        assert len(docs) == 2
        assert all(d["status_xml"] == "COMPLETO" for d in docs)
        assert all(d["xml_bruto"].startswith("<cteProc>") for d in docs)
        assert all(d.get("xml_hash_sha256") for d in docs)
        xmls = [u for u, _ in chamadas if u.endswith(".xml")]
        assert xmls == [f"https://api.focusnfe.com.br/v2/ctes_recebidas/{CHAVE}.xml",
                        f"https://api.focusnfe.com.br/v2/ctes_recebidas/{CHAVE_2}.xml"]

    def test_cancelada_nao_baixa_xml(self, provider):
        env, chamadas = self._fetch(provider, [_item(situacao="cancelada")])
        assert env["documentos"][0]["cancelado"] == 1
        assert [u for u, _ in chamadas if u.endswith(".xml")] == []

    def test_xml_indisponivel_marca_pendencia_e_segura_o_cursor(self, provider):
        env, _ = self._fetch(provider, [_item(versao=60)], xml_ok=False)
        doc = env["documentos"][0]
        assert doc["xml_pending"] is True
        assert doc["status_xml"] == "RESUMO"
        # Cursor seguro nunca ultrapassa a menor versão pendente.
        assert int(str(env["ultimo_nsu"])) < 60

    def test_mdfe_continua_recusado(self, provider):
        with patch.object(requests, "get") as g:
            env = provider.gov_fetch({"cnpj": "07219398000109", "tipo": "mdfe",
                                      "ambiente": "producao", "ultimo_nsu": "0"}, "t")
        assert env["ok"] is False
        assert env["codigo"] == "FOCUS_TIPO_NAO_SUPORTADO"
        g.assert_not_called()

    def test_lote_vazio_nao_quebra(self, provider):
        lista = _mock_resp(200, json_data=[], headers={"X-Total-Count": "0"})
        with patch.object(requests, "get", return_value=lista):
            env = provider.gov_fetch({"cnpj": "07219398000109", "tipo": "cte",
                                      "ambiente": "producao", "ultimo_nsu": "377"}, "t")
        assert env["ok"] is True
        assert env["documentos"] == []
        assert str(env["ultimo_nsu"]) == "377", "cursor não regride no lote vazio"
