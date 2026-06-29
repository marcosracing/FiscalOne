"""
xml_parser.py — FiscalOne (ADR-0034)
Parser de documentos fiscais: NF-e, CT-e, NFS-e.
Responsabilidades:
  - Parseia campos fiscais (chave, emitente, valores, NCMs, UFs)
  - Retorna xinf BRUTO — não extrai placa/odômetro (domínio MapOne)
  - Retorna ncm_itens — não classifica conta contábil (domínio MapOne/CtrlOne)
  - NFS-e municipal não reconhecida → PARSE_UNSUPPORTED (não erro fatal)
  - import_origin segue domínio ADR-0029 (fiscalone_upload | fiscalone_sefaz | fiscalone_email)
"""
import re
import uuid
from xml.etree import ElementTree as ET

_NS = {
    "nfe":  "http://www.portalfiscal.inf.br/nfe",
    "cte":  "http://www.portalfiscal.inf.br/cte",
    "mdfe": "http://www.portalfiscal.inf.br/mdfe",
}

def _t(ns, local):
    return f"{{{_NS[ns]}}}{local}"

def _find(el, *path, ns="nfe"):
    cur = el
    for p in path:
        cur = cur.find(_t(ns, p))
        if cur is None:
            return None
    return cur

def _text(el, *path, ns="nfe", default=""):
    found = _find(el, *path, ns=ns)
    return (found.text or "").strip() if found is not None else default

def _float(el, *path, ns="nfe"):
    try:
        return float(_text(el, *path, ns=ns) or 0)
    except (ValueError, TypeError):
        return 0.0

def _detectar_tipo(root):
    for el in root.iter():
        tag = el.tag
        if "portalfiscal.inf.br/nfe" in tag:
            return "nfe"
        if "portalfiscal.inf.br/cte" in tag:
            return "cte"
        if "portalfiscal.inf.br/mdfe" in tag:
            return "mdfe"
    return None

def _extrair_inf(root, tipo):
    tag_map = {"nfe": "infNFe", "cte": "infCte", "mdfe": "infMDFe"}
    tag = tag_map.get(tipo, "infNFe")
    for el in root.iter():
        if el.tag.endswith(tag):
            return el
    return root

def _chave_do_id(inf):
    id_attr = inf.get("Id", "")
    if id_attr:
        return re.sub(r"\D", "", id_attr)[-44:]
    return ""

def parse_xml(xml_bytes: bytes, filename: str = "",
              import_origin: str = "fiscalone_upload",
              trace_id: str = "") -> dict:
    """
    Parseia XML de NF-e, CT-e ou NFS-e.
    Retorna dict normalizado ou {"ok": False, "codigo": "PARSE_ERROR", ...}
    """
    if not trace_id:
        trace_id = f"fo-{uuid.uuid4().hex}"

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return {
            "ok": False, "file": filename, "trace_id": trace_id,
            "codigo": "PARSE_ERROR", "erro": f"XML inválido: {e}",
        }

    tipo = _detectar_tipo(root)
    if tipo is None:
        return {
            "ok": False, "file": filename, "trace_id": trace_id,
            "codigo": "PARSE_UNSUPPORTED",
            "erro": "Tipo de documento não reconhecido (NFS-e municipal ou formato desconhecido)",
            "confianca": "baixa",
        }

    inf = _extrair_inf(root, tipo)
    ns  = tipo

    if tipo == "nfe":
        return _parse_nfe(inf, filename, import_origin, trace_id, ns)
    elif tipo == "cte":
        return _parse_cte(inf, filename, import_origin, trace_id, ns)
    else:
        return _parse_mdfe(inf, filename, import_origin, trace_id, ns)

def _parse_nfe(inf, filename, import_origin, trace_id, ns):
    chave = _chave_do_id(inf)
    ide   = _find(inf, "ide",  ns=ns)
    emit  = _find(inf, "emit", ns=ns)
    dest  = _find(inf, "dest", ns=ns)
    tot   = _find(inf, "total", ns=ns)
    icms  = _find(tot, "ICMSTot", ns=ns) if tot is not None else None

    ncms = []
    for det in inf.findall(_t(ns, "det")):
        prod = det.find(_t(ns, "prod"))
        if prod is not None:
            ncm = _text(prod, "NCM", ns=ns)
            if ncm:
                ncms.append(ncm)

    inf_adic = _find(inf, "infAdic", ns=ns)
    xinf = _text(inf_adic, "xInfCpl", ns=ns) if inf_adic is not None else ""

    dh_emi = (_text(ide, "dhEmi", ns=ns) or _text(ide, "dEmi", ns=ns)) if ide is not None else ""

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfe",
        "chave":         chave,
        "numero":        _text(ide, "nNF", ns=ns) if ide is not None else "",
        "serie":         _text(ide, "serie", ns=ns) if ide is not None else "",
        "emit_cnpj":     re.sub(r"\D", "", _text(emit, "CNPJ", ns=ns) if emit is not None else ""),
        "emit_nome":     _text(emit, "xNome", ns=ns) if emit is not None else "",
        "dest_cnpj":     re.sub(r"\D", "", _text(dest, "CNPJ", ns=ns) if dest is not None else ""),
        "dest_nome":     _text(dest, "xNome", ns=ns) if dest is not None else "",
        "dh_emi_utc":    dh_emi[:19] if dh_emi else "",
        "valor_total":   _float(icms, "vNF", ns=ns) if icms is not None else 0.0,
        "valor_icms":    _float(icms, "vICMS", ns=ns) if icms is not None else 0.0,
        "valor_iss":     0.0,
        "ncm_itens":     ncms,
        "uf_ini":        None,
        "uf_fim":        None,
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "xinf":          xinf,
    }

def _parse_cte(inf, filename, import_origin, trace_id, ns):
    chave  = _chave_do_id(inf)
    ide    = _find(inf, "ide",    ns=ns)
    emit   = _find(inf, "emit",   ns=ns)
    vprest = _find(inf, "vPrest", ns=ns)

    dh_emi = (_text(ide, "dhEmi", ns=ns) or _text(ide, "dEmi", ns=ns)) if ide is not None else ""

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "cte",
        "chave":         chave,
        "numero":        _text(ide, "nCT", ns=ns) if ide is not None else "",
        "serie":         _text(ide, "serie", ns=ns) if ide is not None else "",
        "emit_cnpj":     re.sub(r"\D", "", _text(emit, "CNPJ", ns=ns) if emit is not None else ""),
        "emit_nome":     _text(emit, "xNome", ns=ns) if emit is not None else "",
        "dest_cnpj":     "",
        "dest_nome":     "",
        "dh_emi_utc":    dh_emi[:19] if dh_emi else "",
        "valor_total":   _float(vprest, "vTPrest", ns=ns) if vprest is not None else 0.0,
        "valor_icms":    0.0,
        "valor_iss":     0.0,
        "ncm_itens":     [],
        "uf_ini":        _text(ide, "UFIni", ns=ns) if ide is not None else None,
        "uf_fim":        _text(ide, "UFFim", ns=ns) if ide is not None else None,
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "xinf":          "",
    }

def _parse_mdfe(inf, filename, import_origin, trace_id, ns):
    chave = _chave_do_id(inf)
    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "mdfe",
        "chave":         chave,
        "numero":        "",
        "emit_cnpj":     "",
        "emit_nome":     "",
        "dh_emi_utc":    "",
        "valor_total":   0.0,
        "valor_icms":    0.0,
        "valor_iss":     0.0,
        "ncm_itens":     [],
        "uf_ini":        None,
        "uf_fim":        None,
        "confianca":     "media",
        "import_origin": import_origin,
        "xinf":          "",
    }

if __name__ == "__main__":
    import sys, json
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
        print(json.dumps(parse_xml(data, sys.argv[1]), indent=2, ensure_ascii=False))
    else:
        # Autoteste mínimo
        xml = b'''<?xml version="1.0"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
<NFe><infNFe Id="NFe35260607219398000109550010000001231000001231">
<ide><nNF>123</nNF><serie>1</serie><dhEmi>2026-06-27T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>07219398000109</CNPJ><xNome>Auto Posto Teste</xNome></emit>
<dest><CNPJ>07219398000109</CNPJ><xNome>Racing Logistica</xNome></dest>
<det nItem="1"><prod><NCM>27101921</NCM></prod></det>
<total><ICMSTot><vNF>759.60</vNF><vICMS>0</vICMS></ICMSTot></total>
<infAdic><xInfCpl>PLACA EFO9I83 KM 712181 MOTORISTA DIMAS</xInfCpl></infAdic>
</infNFe></NFe></nfeProc>'''
        r = parse_xml(xml, "teste.xml")
        assert r["ok"] is True, f"FALHA: {r}"
        assert r["type"] == "nfe", f"FALHA tipo: {r['type']}"
        assert r["chave"] == "35260607219398000109550010000001231000001231", f"FALHA chave: {r['chave']}"
        assert r["emit_cnpj"] == "07219398000109", f"FALHA emit: {r['emit_cnpj']}"
        assert r["valor_total"] == 759.60, f"FALHA valor: {r['valor_total']}"
        assert r["ncm_itens"] == ["27101921"], f"FALHA ncm: {r['ncm_itens']}"
        assert r["xinf"] == "PLACA EFO9I83 KM 712181 MOTORISTA DIMAS", f"FALHA xinf: {r['xinf']}"
        assert r["import_origin"] == "fiscalone_upload", f"FALHA origin: {r['import_origin']}"
        assert r["trace_id"].startswith("fo-"), f"FALHA trace_id: {r['trace_id']}"
        print("OK autoteste NF-e — todos os campos corretos")

        # Teste PARSE_UNSUPPORTED
        r2 = parse_xml(b"<nfse><nota>qualquer</nota></nfse>", "nfse_municipal.xml")
        assert r2["ok"] is False
        assert r2["codigo"] == "PARSE_UNSUPPORTED"
        print("OK autoteste PARSE_UNSUPPORTED")

        print("TODOS OS AUTOTESTES PASSARAM")
