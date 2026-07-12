"""Parsers por tipo — status_xml sempre presente, doc_type explicito."""
import xml_parser as p


XML_NFE = b'''<?xml version="1.0"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
<NFe><infNFe Id="NFe35260607219398000109550010000001231000001231">
<ide><nNF>123</nNF><serie>1</serie><dhEmi>2026-07-05T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>07219398000109</CNPJ><xNome>RACING</xNome></emit>
<dest><CNPJ>11222333000155</CNPJ></dest>
<total><ICMSTot><vNF>1234.56</vNF></ICMSTot></total>
</infNFe></NFe></nfeProc>'''

XML_CTE = b'''<?xml version="1.0"?>
<cteProc xmlns="http://www.portalfiscal.inf.br/cte">
<CTe><infCte Id="CTe35260607219398000109570010000001231000001231">
<ide><nCT>555</nCT><serie>1</serie><dhEmi>2026-07-05T10:00:00-03:00</dhEmi>
      <UFIni>SP</UFIni><UFFim>RJ</UFFim></ide>
<emit><CNPJ>07219398000109</CNPJ><xNome>TRANSPORTADORA</xNome></emit>
<dest><CNPJ>11222333000155</CNPJ></dest>
<vPrest><vTPrest>500.00</vTPrest></vPrest>
</infCte></CTe></cteProc>'''

XML_NFSE = b'''<?xml version="1.0"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">
  <infNFSe Id="NFSe12345678901234567890123456789012345678901234567890">
    <emit><CNPJ>07219398000109</CNPJ><xNome>RACING</xNome></emit>
    <dhEmi>2026-07-05T09:00:00-03:00</dhEmi>
    <valores><vNFSe>500.00</vNFSe></valores>
    <nDPS>777</nDPS>
  </infNFSe>
</NFSe>'''

XML_RES_NFE = b'''<?xml version="1.0"?>
<resNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
  <chNFe>35260607219398000109550010000009999900000000000</chNFe>
  <CNPJ>07219398000109</CNPJ><xNome>RACING</xNome>
  <dhEmi>2026-07-05T10:00:00-03:00</dhEmi>
  <tpNF>1</tpNF><vNF>100</vNF><cSitNFe>1</cSitNFe>
</resNFe>'''


class TestParseNfe:
    def test_completo(self):
        r = p.parse_nfe(XML_NFE)
        assert r["ok"] is True
        assert r["status_xml"] == "COMPLETO"
        assert r["doc_type"] == "nfe"
        assert r["chave"] == "35260607219398000109550010000001231000001231"
        assert len(r["chave"]) == 44
        assert "parser_version" in r
        assert r["import_origin"] == "fiscalone_gov_fetch"

    def test_resumo_via_parse_xml(self):
        r = p.parse_xml(XML_RES_NFE)
        assert r["ok"] is False
        assert r["status_xml"] == "RESUMO"
        assert r["codigo"] == "RESUMO_DFE_RECEBIDO"
        assert "parser_version" in r

    def test_xml_invalido_falha_processamento(self):
        r = p.parse_nfe(b"nao e xml")
        assert r["ok"] is False
        assert r["status_xml"] == "FALHA_PROCESSAMENTO"
        assert r["codigo"] == "PARSE_ERROR"
        assert "parser_version" in r
        assert r["doc_type"] == "nfe"

    def test_doc_type_divergente(self):
        r = p.parse_nfe(XML_CTE)   # CT-e enviado como NF-e
        assert r["ok"] is False
        assert r["codigo"] == "DOC_TYPE_DIVERGENTE"
        assert r["status_xml"] == "FALHA_PROCESSAMENTO"


class TestParseCte:
    def test_completo(self):
        r = p.parse_cte(XML_CTE)
        assert r["ok"] is True
        assert r["status_xml"] == "COMPLETO"
        assert r["doc_type"] == "cte"
        assert r["chave"] == "35260607219398000109570010000001231000001231"
        assert len(r["chave"]) == 44
        assert r["uf_ini"] == "SP" and r["uf_fim"] == "RJ"

    def test_xml_invalido(self):
        r = p.parse_cte(b"<lixo/>")
        assert r["ok"] is False
        assert r["status_xml"] == "FALHA_PROCESSAMENTO"


class TestParseNfse:
    def test_completo(self):
        r = p.parse_nfse(XML_NFSE)
        assert r["ok"] is True
        assert r["status_xml"] == "COMPLETO"
        assert r["doc_type"] == "nfse"

    def test_nsu_adn_preservado(self):
        """NSU do ADN nao passa por zfill — regra do provider ADN, testada
        aqui via nfse_provider.normalizar_nsu_adn."""
        from providers.nfse_provider import normalizar_nsu_adn
        assert normalizar_nsu_adn("555") == "555"
        assert normalizar_nsu_adn("125643") == "125643"


class TestStatusXmlSempre:
    """Todo retorno de parse_xml/parse_document deve ter status_xml."""

    def test_ok_true_tem_status_xml(self):
        r = p.parse_xml(XML_NFE)
        assert "status_xml" in r
        assert "parser_version" in r

    def test_ok_false_tem_status_xml(self):
        r = p.parse_xml(b"")
        assert "status_xml" in r
        assert r["status_xml"] == "FALHA_PROCESSAMENTO"
        assert "parser_version" in r
