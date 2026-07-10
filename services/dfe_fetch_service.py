"""
dfe_fetch_service — orquestra uma consulta NFeDistDFeInteresse / CTeDistDFeInteresse.

ADR-0035 · Regras invioláveis:
  - FiscalOne NAO persiste NSU, XML, cooldown, resposta SEFAZ.
  - Retorna documentos parseados + cooldown recomendado no envelope.
  - Vertical (MapOne) e responsavel por persistir estado.
  - PEM temporario existe apenas durante o handshake TLS; unlink imediato.
  - Nunca loga cert/PFX/senha/PEM/token/xml_bruto.

Estrategia:
  - Uma unica pagina/lote por chamada (a vertical decide se recorre).
  - Cooldown recomendado devolvido em segundos com base no cStat SEFAZ.
"""
import base64
import gzip
import hashlib
import http.client
import os
import re
import ssl
import tempfile
import time
import xml.etree.ElementTree as ET


# ── Endpoints SEFAZ ─────────────────────────────────────────────────────────

UF_AUTOR      = "35"
NFE_HOST      = "www1.nfe.fazenda.gov.br"
NFE_HOM_HOST  = "hom1.nfe.fazenda.gov.br"
NFE_PATH      = "/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
CTE_HOST      = "www1.cte.fazenda.gov.br"
CTE_HOM_HOST  = "hom1.cte.fazenda.gov.br"
CTE_PATH      = "/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx"
NFSE_NAC_HOST = "adn.nfse.gov.br"

TIMEOUT_SEG = 60

# Cooldowns recomendados (segundos)
_COOLDOWN_656 = 65 * 60   # Consumo indevido — 1h05
_COOLDOWN_137 = 60 * 60   # Sem novos docs — 1h
_COOLDOWN_138 =  0        # docs encontrados — vertical pode consultar de novo
_COOLDOWN_589 =  1        # NSU > maxNSU — reset


# ── TLS / mTLS ──────────────────────────────────────────────────────────────

def _make_ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("GOV_TLS_INSECURE", "") == "1":
        # Somente para diagnostico local; produção NUNCA.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass
    return ctx


def _build_mtls_ctx(cert_pem, key_pem):
    """Contexto TLS cliente. Temp PEM chmod 600, unlink imediato apos load_cert_chain."""
    cf = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    kf = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    try:
        cf.write(cert_pem); cf.close()
        kf.write(key_pem);  kf.close()
        os.chmod(cf.name, 0o600)
        os.chmod(kf.name, 0o600)
        ctx = _make_ctx()
        ctx.load_cert_chain(certfile=cf.name, keyfile=kf.name)
    finally:
        for f in (cf.name, kf.name):
            try:
                os.unlink(f)
            except OSError:
                pass
    return ctx


def _post_soap(host, path, body, cert_pem, key_pem):
    ctx  = _build_mtls_ctx(cert_pem, key_pem)
    conn = http.client.HTTPSConnection(host, 443, context=ctx, timeout=TIMEOUT_SEG)
    try:
        conn.request(
            "POST", path,
            body=body.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        try: conn.close()
        except Exception: pass


# ── XML helpers ─────────────────────────────────────────────────────────────

def _lname(tag):
    return tag.rsplit("}", 1)[-1]


def _find_text(root, name):
    for el in root.iter():
        if _lname(el.tag) == name and el.text:
            return el.text.strip()
    return ""


# ── Envelopes SOAP ──────────────────────────────────────────────────────────

def _env_nfe(cnpj, ult_nsu, tp_amb):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        '<nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">'
        '<nfeDadosMsg>'
        '<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.35">'
        f'<tpAmb>{tp_amb}</tpAmb><cUFAutor>{UF_AUTOR}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ><distNSU><ultNSU>{ult_nsu}</ultNSU></distNSU>'
        '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse></soap:Body></soap:Envelope>'
    )


def _env_cte(cnpj, ult_nsu, tp_amb):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        '<cteDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe">'
        '<cteDadosMsg>'
        '<distDFeInt xmlns="http://www.portalfiscal.inf.br/cte" versao="1.00">'
        f'<tpAmb>{tp_amb}</tpAmb><cUFAutor>{UF_AUTOR}</cUFAutor>'
        f'<CNPJ>{cnpj}</CNPJ><distNSU><ultNSU>{ult_nsu}</ultNSU></distNSU>'
        '</distDFeInt></cteDadosMsg></cteDistDFeInteresse></soap:Body></soap:Envelope>'
    )


# ── Endpoint resolver ───────────────────────────────────────────────────────

def _endpoint(tipo, ambiente):
    """Retorna (host, path, env_fn, tp_amb) — hom1 em homologacao, www1 em prod."""
    is_prod = ambiente in ("1", "producao", "prod", "production")
    tp_amb  = "1" if is_prod else "2"
    if tipo == "nfe":
        return (NFE_HOST if is_prod else NFE_HOM_HOST), NFE_PATH, _env_nfe, tp_amb
    if tipo == "cte":
        return (CTE_HOST if is_prod else CTE_HOM_HOST), CTE_PATH, _env_cte, tp_amb
    raise ValueError(f"tipo nao suportado: {tipo}")


# ── Documento extraido do lote (parse via xml_parser do FiscalOne) ──────────

def _parse_doc(xml_bruto, trace_id):
    """Parseia XML NF-e/CT-e usando o parser do FiscalOne. Devolve dict resumido."""
    from xml_parser import parse_xml
    parsed = parse_xml(xml_bruto.encode("utf-8"), filename="dfe.xml",
                       import_origin="fiscalone_gov_fetch",
                       trace_id=trace_id)
    return parsed


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Fetch principal ─────────────────────────────────────────────────────────

def fetch_dfe(cert_pem, key_pem, cnpj, tipo, ambiente, ultimo_nsu, trace_id,
              incluir_xml_bruto=True):
    """
    Executa UMA consulta NFeDistDFeInteresse / CTeDistDFeInteresse.

    Args:
        cert_pem, key_pem: bytes (bundle da requisicao — sem persistencia).
        cnpj:              14 digitos (interessado).
        tipo:              "nfe" | "cte".
        ambiente:          "producao" | "homologacao" (ou "1" | "2").
        ultimo_nsu:        string 15 digitos (envia zeros para primeira).
        trace_id:          para propagar em logs e resposta.
        incluir_xml_bruto: se True, devolve o XML por documento (vertical persiste).

    Returns:
        dict com cstat, xmotivo, ultimo_nsu, max_nsu, cooldown_recomendado_seg,
        documentos:[{doc_type, chave, numero, emit_cnpj, emit_nome, dest_cnpj,
                     dh_emi, valor_total, xml_bruto, xml_hash_sha256, parser_version}].
        Nao lanca — retorna ok=False com codigo controlado em caso de erro.
    """
    cnpj = re.sub(r"\D", "", cnpj or "")
    if len(cnpj) != 14:
        return {
            "ok": False,
            "codigo": "CNPJ_INVALIDO",
            "erro": "cnpj_tenant deve ter 14 digitos",
        }

    ult = re.sub(r"\D", "", ultimo_nsu or "") or "0"
    ult = ult.zfill(15)

    try:
        host, path, env_fn, tp_amb = _endpoint(tipo, ambiente)
    except ValueError as e:
        return {"ok": False, "codigo": "TIPO_NAO_SUPORTADO", "erro": str(e)}

    body = env_fn(cnpj, ult, tp_amb)

    t0 = time.monotonic()
    try:
        status, data = _post_soap(host, path, body, cert_pem, key_pem)
    except ssl.SSLError as e:
        return {"ok": False, "codigo": "TLS_ERRO",
                "erro": "Falha TLS na comunicacao com a SEFAZ"}
    except Exception as e:
        return {"ok": False, "codigo": "SEFAZ_INDISPONIVEL",
                "erro": f"Erro de conexao com SEFAZ: {type(e).__name__}"}

    duracao_ms = int((time.monotonic() - t0) * 1000)

    if status != 200:
        return {
            "ok": False, "codigo": "SEFAZ_HTTP_ERRO",
            "erro": f"SEFAZ respondeu HTTP {status}",
            "duracao_ms": duracao_ms,
        }

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return {"ok": False, "codigo": "SEFAZ_XML_INVALIDO",
                "erro": f"Resposta SEFAZ nao e XML valido: {e}",
                "duracao_ms": duracao_ms}

    cstat   = _find_text(root, "cStat")
    xmotivo = _find_text(root, "xMotivo")
    ult_ret = _find_text(root, "ultNSU") or ult
    max_nsu = _find_text(root, "maxNSU") or ""

    cooldown = _COOLDOWN_138
    if cstat == "656":
        cooldown = _COOLDOWN_656
    elif cstat == "137":
        cooldown = _COOLDOWN_137
    elif cstat == "589":
        cooldown = _COOLDOWN_589

    documentos, resumos, erros = [], [], []
    if cstat == "138":
        for dz in [el for el in root.iter() if _lname(el.tag) == "docZip"]:
            nsu    = dz.get("NSU")
            schema = dz.get("schema") or ""
            try:
                xml = gzip.decompress(
                    base64.b64decode(dz.text or "")
                ).decode("utf-8", "replace")
            except Exception:
                erros.append({
                    "ok":         False,
                    "nsu":        nsu,
                    "schema":     schema,
                    "codigo":     "DOCZIP_DECODE_FALHOU",
                    "erro":       "Falha ao descompactar/decodificar docZip",
                    "status_xml": "ERRO",
                })
                continue

            parsed = _parse_doc(xml, trace_id)
            status_xml = parsed.get("status_xml")  # COMPLETO ou RESUMO
            codigo     = parsed.get("codigo")

            item_base = {
                "nsu":              nsu,
                "schema":           schema,
                "doc_type":         parsed.get("doc_type"),
                "chave":            parsed.get("chave"),
                "numero":           parsed.get("numero"),
                "emit_cnpj":        parsed.get("emit_cnpj"),
                "emit_nome":        parsed.get("emit_nome") or parsed.get("emit_xnome"),
                "dest_cnpj":        parsed.get("dest_cnpj"),
                "dh_emi":           parsed.get("dh_emi") or parsed.get("data_emissao"),
                "valor_total":      parsed.get("valor_total"),
                "xml_hash_sha256":  _sha256(xml),
                "parser_version":   parsed.get("parser_version") or "fiscalone_xml_parser",
                "status_xml":       status_xml,
            }

            if parsed.get("ok") and status_xml == "COMPLETO":
                if incluir_xml_bruto:
                    item_base["xml_bruto"] = xml
                documentos.append(item_base)
            elif codigo == "RESUMO_DFE_RECEBIDO":
                item_base["status_xml"] = "RESUMO"
                item_base["codigo"]     = "RESUMO_DFE_RECEBIDO"
                # campos extras uteis so em resumo
                for k in ("cSitNFe", "cSitCTe", "tpNF", "tpCTe", "tipo_evento",
                         "n_seq_evento", "dh_evento", "xevento", "chave_ref"):
                    v = parsed.get(k)
                    if v is not None and v != "":
                        item_base[k] = v
                if incluir_xml_bruto:
                    item_base["xml_bruto"] = xml
                resumos.append(item_base)
            else:
                erros.append({
                    "ok":         False,
                    "nsu":        nsu,
                    "schema":     schema,
                    "codigo":     codigo or "PARSE_UNSUPPORTED_DFE",
                    "erro":       parsed.get("erro") or "Layout de docZip nao suportado",
                    "status_xml": "ERRO",
                })

    # results[] unificado (compatibilidade — cada item traz status_xml)
    results = []
    for d in documentos: results.append({**d, "categoria": "COMPLETO"})
    for r in resumos:    results.append({**r, "categoria": "RESUMO"})
    for e in erros:      results.append({**e, "categoria": "ERRO"})

    return {
        "ok":                       True,
        "cstat":                    cstat,
        "xmotivo":                  xmotivo,
        "ultimo_nsu":               ult_ret,
        "max_nsu":                  max_nsu,
        "cooldown_recomendado_seg": cooldown,
        "documentos":               documentos,
        "resumos":                  resumos,
        "erros":                    erros,
        "results":                  results,
        "duracao_ms":               duracao_ms,
    }
