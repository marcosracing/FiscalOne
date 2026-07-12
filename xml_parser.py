"""
xml_parser.py — FiscalOne (ADR-0034/0035)

Parser de documentos fiscais recebidos: NF-e, CT-e, MDF-e, NFS-e (XML e PDF).

Responsabilidades:
  - Parseia campos fiscais canônicos (chave, emitente, valores, NCMs, UFs).
  - Retorna xinf BRUTO — não extrai placa/odômetro (domínio MapOne).
  - Retorna ncm_itens — não classifica conta contábil (domínio MapOne/CtrlOne).
  - NFS-e XML: padrão nacional (sped.fazenda.gov.br/nfse) + ABRASF genérico.
  - NFS-e PDF: layout São Paulo (capital) + melhor esforço genérico.
  - Se não houver chave canônica, gera identificador estável
    (`nfse:{emit_cnpj}:{numero}:{dh_emi}:{valor_total}`).
  - Nunca persiste — a vertical (MapOne) grava em op_fiscal_xml.
  - import_origin segue domínio ADR-0029.

A lógica de NFS-e/PDF foi adaptada do parser maduro do rlogix/fiscal.py
(mesmo autor/projeto); não há import cruzado — a cópia é intencional para
manter o FiscalOne autocontido.
"""
import io
import json
import re
import unicodedata
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET


# ── Contrato ──────────────────────────────────────────────────────────────────
# Versao do parser exposta em todo dict retornado (contrato MapOne).
PARSER_VERSION = "fiscalone_xml_parser@2026-07-12"

# Valores canonicos de status_xml — devem casar com schemas/__init__.StatusXml.
STATUS_XML_COMPLETO             = "COMPLETO"
STATUS_XML_RESUMO               = "RESUMO"
STATUS_XML_EVENTO               = "EVENTO"
STATUS_XML_FALHA_PROCESSAMENTO  = "FALHA_PROCESSAMENTO"
STATUS_XML_RECEBIDA             = "RECEBIDA"


def _falha_processamento(codigo: str, erro: str, filename: str,
                          trace_id: str, doc_type: str | None = None) -> dict:
    """Retorno padrao para qualquer erro de parse. status_xml sempre presente."""
    out = {
        "ok":             False,
        "codigo":         codigo,
        "erro":           erro,
        "file":           filename,
        "trace_id":       trace_id,
        "status_xml":     STATUS_XML_FALHA_PROCESSAMENTO,
        "parser_version": PARSER_VERSION,
    }
    if doc_type:
        out["doc_type"] = doc_type
    return out


# ── utilidades numéricas / texto ──────────────────────────────────────────────

def to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("R$", "").replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _deaccent(s: str) -> str:
    s = (s or "").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def parse_date(s: str):
    """Retorna (iso, ano, mes) a partir de '2025-05-10T...' ou '10/05/2025'."""
    s = (s or "").strip()
    if not s:
        return "", None, None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10], int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return iso, int(m.group(3)), int(m.group(2))
    m = re.match(r"(\d{2})/(\d{2})/(\d{2})(?!\d)", s)
    if m:
        ano = 2000 + int(m.group(3))
        return f"{ano}-{m.group(2)}-{m.group(1)}", ano, int(m.group(2))
    return s, None, None


# ── navegação genérica de XML (local-name, sem namespace fixo) ────────────────

def localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def first(el, name):
    if el is None:
        return None
    target = name.lower()
    for d in el.iter():
        if localname(d.tag).lower() == target:
            return d
    return None


def first_any(el, names):
    for n in names:
        f = first(el, n)
        if f is not None and (f.text or "").strip():
            return f
    return None


def text(el, name, default=""):
    f = first(el, name)
    return f.text.strip() if (f is not None and f.text) else default


def text_any(el, names, default=""):
    f = first_any(el, names)
    return f.text.strip() if f is not None else default


def num(el, name):
    return to_float(text(el, name))


def num_any(el, names):
    return to_float(text_any(el, names))


def findall(el, name):
    target = name.lower()
    return [d for d in el.iter() if localname(d.tag).lower() == target]


# ── detecção de tipo ─────────────────────────────────────────────────────────

def _detect_type_by_localnames(root) -> str:
    names = {localname(e.tag).lower() for e in root.iter()}
    # Resumos SEFAZ Distribuicao DFe (nao sao layouts fiscais completos)
    if "resnfe" in names:
        return "resumo_nfe"
    if "rescte" in names:
        return "resumo_cte"
    if "resevento" in names:
        return "resumo_evento"
    if "tpevento" in names or "infevento" in names:
        return "evento"
    if "infcte" in names:
        return "cte"
    if "infmdfe" in names:
        return "mdfe"
    if "infnfe" in names:
        return "nfe"
    nfse_markers = {"infnfse", "nfse", "compnfse", "gerarnfseresposta", "infdps"}
    if names & nfse_markers or any("nfse" in n or "servprestado" in n for n in names):
        return "nfse"
    return None


def _load_xml_root(xml_bytes: bytes):
    """Tolera BOM/encoding declarado incorretamente (Barueri declara utf-16
    mas grava utf-8 etc.). Remove <?xml ...?> antes de parsear em texto."""
    if xml_bytes[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text_xml = xml_bytes.decode("utf-16")
    else:
        try:
            text_xml = xml_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_xml = xml_bytes.decode("latin-1", errors="replace")
    text_xml = re.sub(r"^\s*<\?xml[^>]*\?>", "", text_xml, count=1).lstrip()
    return ET.fromstring(text_xml)


# ── NF-e ─────────────────────────────────────────────────────────────────────

_FUEL_KW = ("diesel", "etanol", "alcool")
_FUEL_NCM = ("27101921", "27101929", "22071000", "22072010", "22072019", "38260000")


def _is_fuel(xprod: str, ncm: str) -> bool:
    t = _deaccent(xprod)
    if any(k in t for k in _FUEL_KW):
        return True
    n = only_digits(ncm)
    return n.startswith(_FUEL_NCM)


def _parse_nfe(root, filename, import_origin, trace_id):
    inf = first(root, "infNFe")
    chave = only_digits(inf.get("Id")) if inf is not None and inf.get("Id") else ""
    if chave and len(chave) > 44:
        chave = chave[-44:]
    ide  = first(root, "ide")
    emit = first(root, "emit")
    dest = first(root, "dest")
    tot  = first(root, "ICMSTot")
    iso, ano, mes = parse_date(text(ide, "dhEmi") or text(ide, "dEmi"))

    ncms = []
    fuel_liters = 0.0
    fuel_value = 0.0
    for d in findall(root, "det"):
        prod = first(d, "prod")
        if prod is None:
            continue
        xprod = text(prod, "xProd")
        ncm   = text(prod, "NCM")
        if ncm:
            ncms.append(only_digits(ncm))
        if _is_fuel(xprod, ncm):
            fuel_liters += num(prod, "qCom")
            fuel_value  += num(prod, "vProd")

    inf_adic = first(root, "infAdic")
    xinf = text(inf_adic, "xInfCpl") if inf_adic is not None else ""

    transp = first(root, "transp")
    transporta = first(transp, "transporta") if transp is not None else None
    transp_cnpj = only_digits(text(transporta, "CNPJ")) if transporta is not None else None
    transp_nome = text(transporta, "xNome") if transporta is not None else None

    emit_end = first(emit, "enderEmit") if emit is not None else None

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfe",
        "doc_type":      "nfe",
        "chave":         chave or None,
        "numero":        text(ide, "nNF"),
        "serie":         text(ide, "serie"),
        "natureza":      text(ide, "natOp"),
        "cfop":          text(first(root, "prod"), "CFOP") if first(root, "prod") is not None else "",
        "emit_cnpj":     only_digits(text(emit, "CNPJ") or text(emit, "CPF")),
        "emit_nome":     text(emit, "xNome"),
        "emit_cnae":        text(emit, "CNAE") or None,
        "emit_ie":          text(emit, "IE") or None,
        "emit_logradouro":  text(emit_end, "xLgr")    if emit_end is not None else None,
        "emit_numero":      text(emit_end, "nro")     if emit_end is not None else None,
        "emit_complemento": text(emit_end, "xCpl")    if emit_end is not None else None,
        "emit_bairro":      text(emit_end, "xBairro") if emit_end is not None else None,
        "emit_municipio":   text(emit_end, "xMun")    if emit_end is not None else None,
        "emit_uf":          text(emit_end, "UF")      if emit_end is not None else None,
        "emit_cep":         only_digits(text(emit_end, "CEP")) if emit_end is not None else None,
        "dest_cnpj":     only_digits(text(dest, "CNPJ") or text(dest, "CPF")),
        "dest_nome":     text(dest, "xNome"),
        "transp_cnpj":   transp_cnpj or None,
        "transp_nome":   transp_nome or None,
        "dh_emi_utc":    (text(ide, "dhEmi") or text(ide, "dEmi"))[:19],
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   num(tot, "vNF"),
        "valor_icms":    num(tot, "vICMS"),
        "valor_pis":     num(tot, "vPIS"),
        "valor_cofins":  num(tot, "vCOFINS"),
        "valor_iss":     0.0,
        "ncm_itens":     ncms,
        "uf_ini":        None,
        "uf_fim":        None,
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "xinf":          xinf,
        "extra": {
            "vProd":       num(tot, "vProd"),
            "vFrete":      num(tot, "vFrete"),
            "vDesc":       num(tot, "vDesc"),
            "fuel_liters": round(fuel_liters, 2),
            "fuel_value":  round(fuel_value, 2),
            "origem":      "xml",
        },
    }


# ── CT-e ─────────────────────────────────────────────────────────────────────

_TOMA_BLOCO = {"0": "rem", "1": "exped", "2": "receb", "3": "dest"}
_TOMA_PAPEL = {"0": "remetente", "1": "expedidor", "2": "recebedor", "3": "destinatario", "4": "outros"}


def _resolve_cte_tomador(root, ide):
    toma3 = first(ide, "toma3")
    toma4 = first(ide, "toma4") or first(root, "toma4")
    if toma3 is not None:
        ind = text(toma3, "toma").strip()
        papel = _TOMA_PAPEL.get(ind)
        bloco_tag = _TOMA_BLOCO.get(ind)
        bloco = first(root, bloco_tag) if bloco_tag else None
        if bloco is not None and papel:
            cnpj = only_digits(text(bloco, "CNPJ") or text(bloco, "CPF"))
            nome = text(bloco, "xNome")
            return cnpj or None, nome or None, papel
        return None, None, papel
    if toma4 is not None:
        cnpj = only_digits(text(toma4, "CNPJ") or text(toma4, "CPF"))
        nome = text(toma4, "xNome")
        return cnpj or None, nome or None, "outros"
    return None, None, None


def _parse_cte(root, filename, import_origin, trace_id):
    inf = first(root, "infCte")
    chave = only_digits(inf.get("Id")) if inf is not None and inf.get("Id") else ""
    if chave and len(chave) > 44:
        chave = chave[-44:]
    ide  = first(root, "ide")
    emit = first(root, "emit")
    rem  = first(root, "rem")
    dest = first(root, "dest")
    iso, ano, mes = parse_date(text(ide, "dhEmi") or text(ide, "dEmi"))
    tomador_cnpj, tomador_nome, tomador_papel = _resolve_cte_tomador(root, ide)

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "cte",
        "doc_type":      "cte",
        "chave":         chave or None,
        "numero":        text(ide, "nCT"),
        "serie":         text(ide, "serie"),
        "natureza":      text(ide, "natOp"),
        "cfop":          text(ide, "CFOP"),
        "emit_cnpj":     only_digits(text(emit, "CNPJ") or text(emit, "CPF")),
        "emit_nome":     text(emit, "xNome"),
        "dest_cnpj":     only_digits(text(dest, "CNPJ") or text(dest, "CPF")
                                      or text(rem, "CNPJ") or text(rem, "CPF")),
        "dest_nome":     text(dest, "xNome") or text(rem, "xNome"),
        "dh_emi_utc":    (text(ide, "dhEmi") or text(ide, "dEmi"))[:19],
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   num(root, "vTPrest"),
        "valor_icms":    num(first(root, "imp"), "vICMS"),
        "valor_pis":     0.0,
        "valor_cofins":  0.0,
        "valor_iss":     0.0,
        "uf_ini":        text(ide, "UFIni") or None,
        "uf_fim":        text(ide, "UFFim") or None,
        "mun_ini":       text(ide, "xMunIni"),
        "mun_fim":       text(ide, "xMunFim"),
        "ncm_itens":     [],
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "xinf":          "",
        "tomador_cnpj":  tomador_cnpj,
        "tomador_nome":  tomador_nome,
        "tomador_papel": tomador_papel,
        "extra": {
            "vCarga":   num(first(root, "infCarga"), "vCarga"),
            "vTotTrib": num(root, "vTotTrib"),
            "tpCTe":    text(ide, "tpCTe"),
            "origem":   "xml",
        },
    }


# ── MDF-e ────────────────────────────────────────────────────────────────────

def _parse_mdfe(root, filename, import_origin, trace_id):
    inf = first(root, "infMDFe")
    chave = only_digits(inf.get("Id")) if inf is not None and inf.get("Id") else ""
    if chave and len(chave) > 44:
        chave = chave[-44:]
    ide = first(root, "ide")
    emit = first(root, "emit")
    iso, ano, mes = parse_date(text(ide, "dhEmi"))
    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "mdfe",
        "doc_type":      "mdfe",
        "chave":         chave or None,
        "numero":        text(ide, "nMDF"),
        "serie":         text(ide, "serie"),
        "emit_cnpj":     only_digits(text(emit, "CNPJ") or text(emit, "CPF")),
        "emit_nome":     text(emit, "xNome"),
        "dest_cnpj":     "",
        "dest_nome":     "",
        "dh_emi_utc":    text(ide, "dhEmi")[:19],
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   0.0,
        "valor_icms":    0.0,
        "valor_iss":     0.0,
        "valor_pis":     0.0,
        "valor_cofins":  0.0,
        "ncm_itens":     [],
        "uf_ini":        text(ide, "UFIni") or None,
        "uf_fim":        text(ide, "UFFim") or None,
        "confianca":     "media" if chave else "baixa",
        "import_origin": import_origin,
        "xinf":          "",
        "extra": {"origem": "xml"},
    }


# ── NFS-e (XML padrão nacional) ──────────────────────────────────────────────

def _parse_nfse_nacional(root, filename, import_origin, trace_id):
    inf   = first(root, "infNFSe")
    chave = only_digits(inf.get("Id")) if inf is not None and inf.get("Id") else ""
    emit  = first(root, "emit")
    val   = first(root, "valores")
    dps   = first(root, "DPS")
    infdps = first(dps, "infDPS") if dps is not None else None
    toma  = first(infdps, "toma") if infdps is not None else None
    dh    = text(infdps, "dhEmi") or text(inf, "dhProc")
    iso, ano, mes = parse_date(dh)
    numero = text(inf, "nNFSe")
    emit_cnpj = only_digits(text(emit, "CNPJ") or text(emit, "CPF"))
    valor_total = num(val, "vLiq") or num(val, "vBC")

    if not chave:
        chave = _nfse_synth_key(emit_cnpj, numero, iso, valor_total)

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfse",
        "doc_type":      "nfse",
        "chave":         chave,
        "numero":        numero,
        "serie":         text(infdps, "serie"),
        "natureza":      text(inf, "xTribNac"),
        "cfop":          "",
        "emit_cnpj":     emit_cnpj,
        "emit_nome":     text(emit, "xNome"),
        "dest_cnpj":     only_digits(text(toma, "CNPJ") or text(toma, "CPF")),
        "dest_nome":     text(toma, "xNome"),
        "dh_emi_utc":    (dh or "")[:19],
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   valor_total,
        "valor_icms":    0.0,
        "valor_pis":     0.0,
        "valor_cofins":  0.0,
        "valor_iss":     num(val, "vISSQN"),
        "ncm_itens":     [],
        "uf_ini":        None,
        "uf_fim":        None,
        "mun_ini":       text(inf, "xLocEmi"),
        "mun_fim":       text(inf, "xLocPrestacao"),
        "confianca":     "alta",
        "import_origin": import_origin,
        "xinf":          "",
        "extra": {
            "cLocIncid":  text(inf, "cLocIncid"),
            "competencia": text(infdps, "dCompet"),
            "vBC":        num(val, "vBC"),
            "aliquota":   num(val, "pAliqAplic"),
            "vRet":       num(val, "vTotalRet"),
            "origem":     "nfse-nacional",
        },
    }


def _nfse_synth_key(emit_cnpj: str, numero: str, dh_emi: str, valor_total: float) -> str:
    """Chave estável quando NFS-e não tem chave canônica: usa dados fiscais."""
    parts = [only_digits(emit_cnpj), (numero or "").strip(),
             (dh_emi or "")[:10], f"{valor_total:.2f}"]
    return "nfse:" + ":".join(parts)


def _parse_nfse_abrasf(root, filename, import_origin, trace_id):
    """NFS-e XML ABRASF/municipal genérico. Confiança média (layouts variam)."""
    numero = text_any(root, ["NumeroNfe", "nNFSe", "Numero", "numero", "nNFS"])
    iso, ano, mes = parse_date(text_any(root, ["dhProc", "DataEmissao", "dhEmi", "dataEmissao"]))
    prest = (first(root, "emit") or first(root, "PrestadorServico")
             or first(root, "prest") or first(root, "Prestador"))
    tom = (first(root, "dest") or first(root, "TomadorServico")
           or first(root, "toma") or first(root, "Tomador"))
    valor = num_any(root, ["ValorServicos", "vServ", "ValorLiquidoNfse",
                          "ValorLiquidoNfe", "vLiq", "ValorTotal"])
    iss = num_any(root, ["ValorIss", "vISSQN", "vISS", "ValorIssqn"])
    chave = text_any(root, ["chNFSe", "CodigoVerificacao", "codigoVerificacao"])
    emit_cnpj = only_digits(text(prest, "CNPJ") or text(prest, "Cnpj") or text(prest, "CpfCnpj"))
    dest_cnpj = only_digits(text(tom, "CNPJ") or text(tom, "Cnpj") or text(tom, "CpfCnpj"))

    if not chave:
        chave = _nfse_synth_key(emit_cnpj, numero, iso, valor)

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfse",
        "doc_type":      "nfse",
        "chave":         chave,
        "numero":        numero,
        "serie":         text_any(root, ["SerieNfe", "serie"]),
        "natureza":      text_any(root, ["DescricaoServico", "Discriminacao", "xDiscriminacao"])[:120],
        "cfop":          text_any(root, ["CodigoServico"]),
        "emit_cnpj":     emit_cnpj,
        "emit_nome":     (text(prest, "xNome") or text(prest, "RazaoSocial")
                          or text(prest, "razaoSocial")),
        "dest_cnpj":     dest_cnpj,
        "dest_nome":     (text(tom, "xNome") or text(tom, "RazaoSocial")
                          or text(tom, "razaoSocial")),
        "dh_emi_utc":    (text_any(root, ["dhProc", "DataEmissao", "dhEmi", "dataEmissao"]) or "")[:19],
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   valor,
        "valor_icms":    0.0,
        "valor_pis":     num_any(root, ["ValorPis", "vPIS"]),
        "valor_cofins":  num_any(root, ["ValorCofins", "vCOFINS"]),
        "valor_iss":     iss,
        "ncm_itens":     [],
        "uf_ini":        None,
        "uf_fim":        None,
        "confianca":     "media",
        "import_origin": import_origin,
        "xinf":          "",
        "extra": {
            "origem":         "nfse-abrasf-xml",
            "irrf":           num_any(root, ["ValorIr", "vIR", "ValorIrrf"]),
            "csll":           num_any(root, ["ValorCsll", "vCSLL"]),
            "liquido":        num_any(root, ["ValorLiquidoNfe", "ValorLiquidoNfse", "vLiq"]),
            "iss_retido":     text_any(root, ["IssRetido"]),
            "municipio_iss":  text_any(root, ["ObservacaoLocalTributado"])[:80],
            "discriminacao":  text_any(root, ["Discriminacao"])[:300],
        },
    }


# ── Resumos DFe (SEFAZ Distribuicao) ─────────────────────────────────────────
#
# Layouts resNFe/resCTe/resEvento sao devolvidos pela SEFAZ quando o XML fiscal
# completo ainda nao esta disponivel para o interessado (destinatario/tomador).
# NAO sao layouts fiscais completos e NAO devem alimentar op_fiscal_xml como
# documento oficial. MapOne deve persistir como pendencia operacional e
# consultar chave-a-chave depois, quando o COMPLETO ficar disponivel.

_RESUMO_MSG = "DFe resumido recebido; XML completo ainda nao disponivel."


def _parse_resumo_nfe(root, filename, import_origin, trace_id):
    r = first(root, "resNFe")
    chave = only_digits(text(r, "chNFe"))
    iso, ano, mes = parse_date(text(r, "dhEmi"))
    return {
        "ok":            False,
        "codigo":        "RESUMO_DFE_RECEBIDO",
        "erro":          _RESUMO_MSG,
        "status_xml":    "RESUMO",
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "resumo_nfe",
        "doc_type":      "nfe",
        "chave":         chave or None,
        "numero":        chave[25:34] if len(chave) == 44 else None,
        "emit_cnpj":     only_digits(text(r, "CNPJ") or text(r, "CPF")),
        "emit_nome":     text(r, "xNome"),
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   num(r, "vNF"),
        "cSitNFe":       text(r, "cSitNFe"),
        "tpNF":          text(r, "tpNF"),
        "dig_val":       text(r, "digVal"),
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "extra":         {"origem": "sefaz-distribuicao-dfe-resumo"},
    }


def _parse_resumo_cte(root, filename, import_origin, trace_id):
    r = first(root, "resCTe")
    chave = only_digits(text(r, "chCTe"))
    iso, ano, mes = parse_date(text(r, "dhEmi"))
    return {
        "ok":            False,
        "codigo":        "RESUMO_DFE_RECEBIDO",
        "erro":          _RESUMO_MSG,
        "status_xml":    "RESUMO",
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "resumo_cte",
        "doc_type":      "cte",
        "chave":         chave or None,
        "numero":        chave[25:34] if len(chave) == 44 else None,
        "emit_cnpj":     only_digits(text(r, "CNPJ") or text(r, "CPF")),
        "emit_nome":     text(r, "xNome"),
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   num(r, "vTPrest"),
        "cSitCTe":       text(r, "cSitCTe"),
        "tpCTe":         text(r, "tpCTe"),
        "dig_val":       text(r, "digVal"),
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "extra":         {"origem": "sefaz-distribuicao-dfe-resumo"},
    }


def _parse_resumo_evento(root, filename, import_origin, trace_id):
    r = first(root, "resEvento")
    chave = only_digits(text(r, "chNFe") or text(r, "chCTe"))
    tp = text(r, "tpEvento")
    return {
        "ok":            False,
        "codigo":        "RESUMO_DFE_RECEBIDO",
        "erro":          _RESUMO_MSG,
        "status_xml":    "RESUMO",
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "resumo_evento",
        "doc_type":      "evento",
        "chave":         chave or None,
        "chave_ref":     chave or "",
        "tipo_evento":   tp,
        "n_seq_evento":  text(r, "nSeqEvento"),
        "dh_evento":     text(r, "dhEvento"),
        "xevento":       text(r, "xEvento"),
        "confianca":     "alta" if chave else "baixa",
        "import_origin": import_origin,
        "extra":         {"origem": "sefaz-distribuicao-dfe-resumo-evento"},
    }


# ── Eventos (cancelamento) ───────────────────────────────────────────────────

def _parse_evento(root, filename, import_origin, trace_id):
    inf = first(root, "infEvento")
    tp = text(inf, "tpEvento")
    ch = text(inf, "chCTe") or text(inf, "chNFe") or text(inf, "chNFSe")
    cstat = ""
    for el in root.iter():
        if localname(el.tag) == "cStat":
            cstat = (el.text or "").strip()
            break
    cancelado = (tp == "110111") and (cstat in ("", "135", "136"))
    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "evento",
        "doc_type":      "evento",
        "chave":         only_digits(ch) if ch else None,
        "tipo_evento":   tp,
        "chave_ref":     only_digits(ch) if ch else "",
        "cancelamento":  cancelado,
        "cstat":         cstat,
        "confianca":     "alta",
        "import_origin": import_origin,
        "xinf":          "",
        "extra":         {"origem": "xml-evento"},
    }


# ── PDF: NFS-e São Paulo (capital) + genérico ────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Requer pdfminer.six em runtime; erro amigável se ausente."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError as e:
        raise RuntimeError(
            "Para importar NFS-e em PDF instale pdfminer.six (requirements.txt)."
        ) from e
    return extract_text(io.BytesIO(pdf_bytes))


_SP_PDF_MARKERS = ("PREFEITURA DO MUNIC", "SÃO PAULO", "NOTA FISCAL ELETR")


def _looks_like_sp_nfse(txt: str) -> bool:
    up = txt.upper()
    return all(m in up for m in _SP_PDF_MARKERS)


def _parse_nfse_sp_pdf(txt: str, filename: str, import_origin: str, trace_id: str) -> dict:
    """NFS-e Prefeitura de São Paulo (capital) a partir do PDF oficial.
    Usa o Identificador Nacional como chave (>44 dígitos — não truncar)."""

    def m(pat, fl=0):
        r = re.search(pat, txt, fl)
        return r.group(1).strip() if r else ""

    def money(s):
        s = (s or "").strip().replace(".", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return 0.0

    chave  = m(r"Identificador Nacional:\s*(\d{40,80})")
    numero = m(r"N[úu]mero da Nota\s*\n+\s*(\d+)")
    cod    = m(r"C[óo]digo de Verifica[çc][ãa]o\s*\n+\s*([A-Z0-9-]+)")
    emi    = m(r"(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}:\d{2}")
    serie  = m(r"RPS N[ºo]\s*\d+\s*S[ée]rie\s*(\w+)")
    iso    = f"{emi[6:10]}-{emi[3:5]}-{emi[0:2]}" if len(emi) == 10 else ""
    ano    = int(iso[:4]) if iso else None
    mes    = int(iso[5:7]) if iso else None
    dh_emi_utc = f"{iso}T00:00:00" if iso else ""

    prest_seg = txt.split("TOMADOR DE SERVI")[0]
    toma_seg  = (txt.split("TOMADOR DE SERVI", 1) + [""])[1].split("INTERMEDI", 1)[0]
    cnpj_re   = r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"
    prest_cnpj = only_digits((re.search(cnpj_re, prest_seg) or [""])[0]
                             if re.search(cnpj_re, prest_seg) else "")
    toma_cnpj  = only_digits((re.search(cnpj_re, toma_seg) or [""])[0]
                             if re.search(cnpj_re, toma_seg) else "")
    prest_nome = m(r"\n([A-ZÀ-Ú][A-ZÀ-Ú0-9 .,&/'\-]{6,}?)\n+\s*"
                   r"(?:R |RUA |AV |AVENIDA |AL |ALAMEDA |TRAV|P;A)")
    toma_nome  = ""
    m_toma = re.search(
        r"TOMADOR DE SERVI[^\n]*\n[\s\S]{0,300}?"
        r"Nome\s*/\s*Raz[aã]o\s+Social:?\s*\n?\s*"
        r"([A-ZÀ-Ú][A-ZÀ-Ú0-9 .,&/'\-]{4,})",
        txt, re.IGNORECASE)
    if m_toma:
        toma_nome = m_toma.group(1).strip()
    if toma_nome.upper().startswith("CPF") or toma_nome.upper().startswith("NOME"):
        toma_nome = ""

    valor = money(m(r"VALOR TOTAL DO SERVI[ÇC]O = R\$\s*([\d.,]+)"))
    aliq  = money(m(r"Al[íi]quota[^\n]*?([\d,]+)%", re.S)
                  or m(r"([\d,]+)%"))
    iss   = round(valor * aliq / 100, 2) if aliq else 0.0
    cserv = m(r"C[óo]digo do Servi[çc]o\s*\n+\s*(\d+ -[^\n]+)")

    if not chave:
        chave = _nfse_synth_key(prest_cnpj, numero, iso, valor)

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfse",
        "doc_type":      "nfse",
        "chave":         chave,
        "numero":        numero or None,
        "serie":         serie,
        "natureza":      cserv,
        "cfop":          "",
        "emit_cnpj":     prest_cnpj,
        "emit_nome":     prest_nome,
        "dest_cnpj":     toma_cnpj,
        "dest_nome":     toma_nome,
        "dh_emi_utc":    dh_emi_utc,
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   valor,
        "valor_icms":    0.0,
        "valor_pis":     0.0,
        "valor_cofins":  0.0,
        "valor_iss":     iss,
        "ncm_itens":     [],
        "uf_ini":        "SP",
        "uf_fim":        None,
        "mun_ini":       "São Paulo",
        "mun_fim":       "",
        "confianca":     "alta",
        "import_origin": import_origin,
        "xinf":          "",
        "extra": {
            "cod_verificacao": cod,
            "aliquota":        aliq,
            "origem":          "nfse-sp-pdf",
        },
    }


def _parse_nfse_text_generic(txt: str, filename: str, import_origin: str, trace_id: str) -> dict:
    """Extração por padrões genéricos do texto de um PDF de NFS-e.
    Melhor esforço: número/data/valor/CNPJ costumam bater; ISS depende do layout."""
    MONEY = r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"

    def grab(patterns):
        for p in patterns:
            m = re.search(p, txt, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    numero = grab([
        r"n[uú]mero\s+da\s+nota[\s\S]{0,18}?(\d{3,})",
        r"n[uú]mero[\s\S]{0,18}?(\d{3,})",
        r"n[º°]\s*0*(\d{3,})",
        r"\bnota\b[\s\S]{0,18}?(\d{5,})",
    ])
    numero = numero.lstrip("0") or numero

    data = grab([
        r"emi\w*[\s\S]{0,25}?(\d{2}/\d{2}/\d{2,4})",
        r"(\d{2}/\d{2}/\d{4})",
        r"(\d{2}/\d{2}/\d{2})(?!\d)",
        r"(\d{4}-\d{2}-\d{2})",
    ])
    iso, ano, mes = parse_date(data)
    dh_emi_utc = f"{iso}T00:00:00" if iso else ""

    valor = to_float(grab([
        r"valor\s+total\s+d[oa]s?\s+servi[çc]os?[\s\S]{0,20}?" + MONEY,
        r"valor\s+total\s+da\s+nota[\s\S]{0,20}?" + MONEY,
        r"valor\s+l[ií]quido[\s\S]{0,40}?" + MONEY,
        r"valor\s+total[\s\S]{0,20}?" + MONEY,
    ]))
    iss = to_float(grab([
        r"total\s+issqn[\s\S]{0,12}?" + MONEY,
        r"valor\s+do\s+iss[^\n]{0,15}?r\$\s*" + MONEY,
    ]))

    cnpjs = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", txt)
    uniq = []
    for c in cnpjs:
        if c not in uniq:
            uniq.append(c)
    emit_cnpj = only_digits(uniq[0]) if uniq else ""
    dest_cnpj = only_digits(uniq[1]) if len(uniq) > 1 else ""
    if not dest_cnpj:
        cpfs = re.findall(r"\d{3}\.\d{3}\.\d{3}-\d{2}", txt)
        if cpfs:
            dest_cnpj = only_digits(cpfs[0])

    chave = _nfse_synth_key(emit_cnpj, numero, iso, valor)

    return {
        "ok":            True,
        "trace_id":      trace_id,
        "file":          filename,
        "type":          "nfse",
        "doc_type":      "nfse",
        "chave":         chave,
        "numero":        numero,
        "serie":         "",
        "natureza":      "",
        "cfop":          "",
        "emit_cnpj":     emit_cnpj,
        "emit_nome":     "",
        "dest_cnpj":     dest_cnpj,
        "dest_nome":     "",
        "dh_emi_utc":    dh_emi_utc,
        "dh_emi":        iso,
        "ano":           ano,
        "mes":           mes,
        "valor_total":   valor,
        "valor_icms":    0.0,
        "valor_pis":     0.0,
        "valor_cofins":  0.0,
        "valor_iss":     iss,
        "ncm_itens":     [],
        "uf_ini":        None,
        "uf_fim":        None,
        "confianca":     "baixa",
        "import_origin": import_origin,
        "xinf":          "",
        "extra":         {"origem": "nfse-pdf-generico", "revisar": True},
    }


# ── APIs públicas ─────────────────────────────────────────────────────────────

def parse_xml(xml_bytes: bytes, filename: str = "",
              import_origin: str = "fiscalone_upload",
              trace_id: str = "") -> dict:
    """Parseia XML de NF-e, CT-e, MDF-e, NFS-e ou evento fiscal.
    Retorno sempre inclui `status_xml` e `parser_version` (em ok:true e ok:false).
    """
    if not trace_id:
        trace_id = f"fo-{uuid.uuid4().hex}"
    try:
        root = _load_xml_root(xml_bytes)
    except ET.ParseError as e:
        return _falha_processamento("PARSE_ERROR", f"XML invalido: {e}",
                                     filename, trace_id)

    tipo = _detect_type_by_localnames(root)
    if tipo is None:
        return _falha_processamento(
            "PARSE_UNSUPPORTED",
            "Tipo de documento nao reconhecido (layout desconhecido)",
            filename, trace_id,
        )

    # Resumos DFe primeiro (nao sao layouts fiscais completos)
    if tipo in ("resumo_nfe", "resumo_cte", "resumo_evento"):
        if tipo == "resumo_nfe":
            out = _parse_resumo_nfe(root, filename, import_origin, trace_id)
        elif tipo == "resumo_cte":
            out = _parse_resumo_cte(root, filename, import_origin, trace_id)
        else:
            out = _parse_resumo_evento(root, filename, import_origin, trace_id)
        out.setdefault("parser_version", PARSER_VERSION)
        return out

    if tipo == "nfe":
        out = _parse_nfe(root, filename, import_origin, trace_id)
    elif tipo == "cte":
        out = _parse_cte(root, filename, import_origin, trace_id)
    elif tipo == "mdfe":
        out = _parse_mdfe(root, filename, import_origin, trace_id)
    elif tipo == "evento":
        out = _parse_evento(root, filename, import_origin, trace_id)
    else:
        tag_root = getattr(root, "tag", "")
        if "sped.fazenda.gov.br/nfse" in tag_root or first(root, "infNFSe") is not None:
            out = _parse_nfse_nacional(root, filename, import_origin, trace_id)
        else:
            out = _parse_nfse_abrasf(root, filename, import_origin, trace_id)
    if isinstance(out, dict):
        # EVENTO: quando o parser detectou infEvento/tpEvento
        if out.get("ok") and out.get("type") == "evento":
            out.setdefault("status_xml", STATUS_XML_EVENTO)
        elif out.get("ok"):
            out.setdefault("status_xml", STATUS_XML_COMPLETO)
        # parser_version obrigatorio em qualquer retorno
        out.setdefault("parser_version", PARSER_VERSION)
    return out


def parse_pdf(pdf_bytes: bytes, filename: str = "",
              import_origin: str = "fiscalone_upload",
              trace_id: str = "") -> dict:
    """Parseia PDF de NFS-e. Reconhece layout SP; senão faz melhor esforço."""
    if not trace_id:
        trace_id = f"fo-{uuid.uuid4().hex}"
    try:
        txt = _extract_pdf_text(pdf_bytes)
    except RuntimeError as e:
        return _falha_processamento("PARSE_ERROR", str(e), filename, trace_id, "nfse")
    except Exception as e:
        return _falha_processamento("PARSE_ERROR", f"PDF ilegivel: {e}",
                                     filename, trace_id, "nfse")

    if not txt or not txt.strip():
        return _falha_processamento("PARSE_ERROR", "PDF sem texto extraivel",
                                     filename, trace_id, "nfse")

    out = (_parse_nfse_sp_pdf(txt, filename, import_origin, trace_id)
           if _looks_like_sp_nfse(txt)
           else _parse_nfse_text_generic(txt, filename, import_origin, trace_id))
    if isinstance(out, dict):
        if out.get("ok"):
            out.setdefault("status_xml", STATUS_XML_COMPLETO)
        out.setdefault("parser_version", PARSER_VERSION)
    return out


# ── APIs por tipo — roteamento por doc_type explicito ────────────────────────
# Estas funcoes NAO inferem tipo pelo XML: exigem que o chamador declare
# doc_type. Se o XML nao casar com o esperado, retornam FALHA_PROCESSAMENTO.
# Uso previsto: providers/nfe_provider, cte_provider, nfse_provider.

def parse_nfe(xml: str, import_origin: str = "fiscalone_gov_fetch",
              trace_id: str = "", filename: str = "nfe.xml") -> dict:
    """Parse NF-e com doc_type declarado. Nao infere pelo layout."""
    xml_bytes = xml.encode("utf-8") if isinstance(xml, str) else xml
    out = parse_xml(xml_bytes, filename, import_origin, trace_id)
    if not out.get("ok"):
        out["doc_type"] = "nfe"
        return out
    if out.get("doc_type") != "nfe":
        return _falha_processamento(
            "DOC_TYPE_DIVERGENTE",
            f"esperado nfe, veio {out.get('doc_type')}",
            filename, out.get("trace_id") or trace_id, "nfe",
        )
    return out


def parse_cte(xml: str, import_origin: str = "fiscalone_gov_fetch",
              trace_id: str = "", filename: str = "cte.xml") -> dict:
    """Parse CT-e com doc_type declarado. Nao infere pelo layout."""
    xml_bytes = xml.encode("utf-8") if isinstance(xml, str) else xml
    out = parse_xml(xml_bytes, filename, import_origin, trace_id)
    if not out.get("ok"):
        out["doc_type"] = "cte"
        return out
    if out.get("doc_type") != "cte":
        return _falha_processamento(
            "DOC_TYPE_DIVERGENTE",
            f"esperado cte, veio {out.get('doc_type')}",
            filename, out.get("trace_id") or trace_id, "cte",
        )
    return out


def parse_nfse(xml: str, import_origin: str = "fiscalone_nfse_adn",
               trace_id: str = "", filename: str = "nfse.xml") -> dict:
    """Parse NFS-e com doc_type declarado. Nao infere pelo layout."""
    xml_bytes = xml.encode("utf-8") if isinstance(xml, str) else xml
    out = parse_xml(xml_bytes, filename, import_origin, trace_id)
    if not out.get("ok"):
        out["doc_type"] = "nfse"
        return out
    if out.get("doc_type") not in ("nfse",):
        return _falha_processamento(
            "DOC_TYPE_DIVERGENTE",
            f"esperado nfse, veio {out.get('doc_type')}",
            filename, out.get("trace_id") or trace_id, "nfse",
        )
    return out


def parse_document(data: bytes, filename: str = "",
                   import_origin: str = "fiscalone_upload",
                   trace_id: str = "") -> dict:
    """Dispatcher pelo sufixo do filename: .xml → parse_xml; .pdf → parse_pdf."""
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        return parse_pdf(data, filename, import_origin, trace_id)
    if ext == ".xml":
        return parse_xml(data, filename, import_origin, trace_id)
    # tentativa por conteúdo
    head = (data[:5] or b"").lower()
    if head.startswith(b"%pdf"):
        return parse_pdf(data, filename, import_origin, trace_id)
    return parse_xml(data, filename, import_origin, trace_id)


# ── Autoteste ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        data = open(path, "rb").read()
        print(json.dumps(parse_document(data, path), indent=2, ensure_ascii=False, default=str))
        raise SystemExit(0)

    xml = b'''<?xml version="1.0"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
<NFe><infNFe Id="NFe35260607219398000109550010000001231000001231">
<ide><nNF>123</nNF><serie>1</serie><dhEmi>2026-06-27T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>07219398000109</CNPJ><xNome>Auto Posto Teste</xNome></emit>
<dest><CNPJ>07219398000109</CNPJ><xNome>Racing Logistica</xNome></dest>
<det nItem="1"><prod><NCM>27101921</NCM><xProd>Diesel</xProd><qCom>1000</qCom><vProd>6000.00</vProd></prod></det>
<total><ICMSTot><vNF>759.60</vNF><vICMS>0</vICMS></ICMSTot></total>
<infAdic><xInfCpl>PLACA EFO9I83 KM 712181 MOTORISTA DIMAS</xInfCpl></infAdic>
</infNFe></NFe></nfeProc>'''
    r = parse_xml(xml, "teste.xml")
    assert r["ok"] is True, r
    assert r["type"] == "nfe"
    assert r["chave"] == "35260607219398000109550010000001231000001231"
    assert r["emit_cnpj"] == "07219398000109"
    assert r["valor_total"] == 759.60
    assert r["ncm_itens"] == ["27101921"]
    assert r["xinf"] == "PLACA EFO9I83 KM 712181 MOTORISTA DIMAS"
    assert r["import_origin"] == "fiscalone_upload"
    assert r["trace_id"].startswith("fo-")
    print("OK autoteste NF-e")

    r2 = parse_xml(b"<algo><outracoisa></outracoisa></algo>", "desconhecido.xml")
    assert r2["ok"] is False
    assert r2["codigo"] == "PARSE_UNSUPPORTED"
    print("OK autoteste PARSE_UNSUPPORTED")

    print("TODOS OS AUTOTESTES PASSARAM")
