"""
nfse_nacional_provider — gateway ADN NFS-e Nacional (Distribuicao DFe por NSU).

ADR-0035 · Regras invioláveis:
  - Gateway puro: nao persiste NSU, XML, cooldown ou cert.
  - Certificado A1 vem em memoria pela requisicao; PEM temp chmod 600 e
    unlink em finally (reutiliza dfe_fetch_service._build_mtls_ctx).
  - NUNCA loga PFX/PEM/senha/base64/xml_bruto.
  - Nao emite NFS-e. Apenas consulta de DFe recebida.

Fluxo NFS-e Nacional / ADN (Distribuicao DFe por NSU):

    GET https://<HOST_ADN>/contribuintes/DFe/{NSU}
    Header: Accept: application/json
    mTLS: cert A1 do interessado (prestador/tomador/intermediario)

Resposta esperada (JSON):
    { "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS" | "..." ,
      "UltimoNSU": "...", "MaxNSU": "...",
      "LoteDFe": [ { "NSU": "...", "ArquivoXml": "<gzip+base64>", ... } ] }

Codigos HTTP tratados:
  200 → payload JSON com/sem documentos
  204 / 404 / lote vazio → SEM_DOCUMENTO
  403 → cert nao habilitado no ADN
  outros → NFSE_ADN_HTTP_ERRO
"""
import base64
import gzip
import hashlib
import http.client
import json
import ssl
import time


ADN_HOST_PROD          = "adn.nfse.gov.br"
ADN_HOST_HOMOLOG       = "adn.producaorestrita.nfse.gov.br"
ADN_PATH_DFE           = "/contribuintes/DFe/{nsu}"
TIMEOUT_SEG            = 60

# Recomendacoes de cooldown por resultado (segundos).
_COOLDOWN_SEM_DOC      = 60 * 60   # 1h — comportamento simetrico a cStat 137
_COOLDOWN_DOCS         = 0         # docs encontrados: vertical drena
_COOLDOWN_HTTP_ERRO    = 15 * 60   # 15 min — erros transitorios
_COOLDOWN_AUTH         = 60 * 60   # 1h — cert nao habilitado; nao adianta bater


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _host_por_ambiente(ambiente):
    a = (ambiente or "").lower().strip()
    if a in ("1", "producao", "prod", "production"):
        return ADN_HOST_PROD, "producao"
    # homologacao / producao_restrita
    return ADN_HOST_HOMOLOG, "producao_restrita"


def _get_mtls(host, path, cert_pem, key_pem, accept="application/json"):
    """GET HTTPS mTLS reusando o build de contexto do dfe_fetch_service."""
    from services.dfe_fetch_service import _build_mtls_ctx
    ctx  = _build_mtls_ctx(cert_pem, key_pem)
    conn = http.client.HTTPSConnection(host, 443, context=ctx, timeout=TIMEOUT_SEG)
    try:
        conn.request("GET", path, headers={"Accept": accept})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        try: conn.close()
        except Exception: pass


def _decode_arquivo(arquivo_b64):
    """Base64+gzip → XML texto. Erros levantam para o caller decidir."""
    return gzip.decompress(base64.b64decode(arquivo_b64)).decode("utf-8", "replace")


def _parse_xml(xml_bruto, trace_id):
    """Parse via xml_parser do FiscalOne — reconhece NFS-e nacional/ABRASF."""
    from xml_parser import parse_xml
    return parse_xml(
        xml_bruto.encode("utf-8"),
        filename="nfse_adn.xml",
        import_origin="fiscalone_nfse_adn",
        trace_id=trace_id,
    )


def _normalizar_doc(item, xml, parsed):
    """Monta dict compativel com o envelope /fiscal/gov/fetch."""
    status_xml = parsed.get("status_xml") or ("COMPLETO" if parsed.get("ok") else None)
    parsed_doc_type = (parsed.get("doc_type") or parsed.get("type") or "nfse").lower()
    return {
        "ok":              bool(parsed.get("ok")),
        "provider":        "nfse_nacional",
        "type":            parsed.get("type") or parsed_doc_type,
        "doc_type":        parsed_doc_type,
        "nsu":             str(item.get("NSU") or ""),
        "chave":           parsed.get("chave"),
        "numero":          parsed.get("numero"),
        "emit_cnpj":       parsed.get("emit_cnpj"),
        "emit_nome":       parsed.get("emit_nome") or parsed.get("emit_xnome"),
        "dest_cnpj":       parsed.get("dest_cnpj"),
        "dh_emi":          parsed.get("dh_emi") or parsed.get("data_emissao"),
        "valor_total":     parsed.get("valor_total"),
        "xml_hash_sha256": _sha256(xml),
        "parser_version":  parsed.get("parser_version") or "fiscalone_xml_parser",
        "status_xml":      status_xml,
        "xml_bruto":       xml,
    }


def consultar_dfe_nsu(cert_pem, key_pem, cnpj, nsu, ambiente, trace_id,
                       incluir_xml_bruto=True):
    """
    Executa UMA consulta ADN por NSU. Devolve dict controlado — nao lanca.

    Args:
        cert_pem, key_pem: bytes (bundle da requisicao — sem persistencia).
        cnpj:              14 digitos (interessado; ADN valida por cert).
        nsu:               string. "0" para iniciar do zero.
        ambiente:          "producao" | "homologacao" (ADN usa producao_restrita).
        trace_id:          para propagar em logs.

    Retorno:
        {
          "ok": true|false,
          "provider": "nfse_nacional",
          "ambiente_adn": "producao"|"producao_restrita",
          "nsu": "...",
          "status": "DOCUMENTOS_LOCALIZADOS" | "SEM_DOCUMENTO" | "ERRO",
          "status_processamento": "<repassa StatusProcessamento do ADN>",
          "ultimo_nsu": "...", "max_nsu": "...",
          "cooldown_recomendado_seg": <int>,
          "documentos": [ {...normalizados...} ],   # apenas COMPLETO
          "resumos":    [],                          # ADN nao devolve resumo hoje
          "erros":      [ {...} ],
          "results":    [ {...com categoria...} ],
          "codigo":  "NFSE_ADN_HTTP_ERRO|NFSE_ADN_TIMEOUT|NFSE_ADN_XML_INVALIDO|NFSE_ADN_AUTH_ERRO",
          "erro":    "mensagem legivel",
          "duracao_ms": <int>
        }
    """
    host, ambiente_adn = _host_por_ambiente(ambiente)
    nsu_str = str(nsu or "0").strip() or "0"
    path = ADN_PATH_DFE.format(nsu=nsu_str)

    t0 = time.monotonic()
    try:
        status, _hdrs, body = _get_mtls(host, path, cert_pem, key_pem)
    except ssl.SSLError:
        return {
            "ok": False, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "codigo": "NFSE_ADN_AUTH_ERRO",
            "erro": "Falha TLS no handshake com o ADN (cert nao aceito?)",
            "cooldown_recomendado_seg": _COOLDOWN_AUTH,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": int((time.monotonic() - t0) * 1000),
        }
    except (TimeoutError, http.client.HTTPException, OSError) as e:
        return {
            "ok": False, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "codigo": "NFSE_ADN_TIMEOUT" if isinstance(e, TimeoutError) else "NFSE_ADN_HTTP_ERRO",
            "erro": f"Erro de conexao com ADN: {type(e).__name__}",
            "cooldown_recomendado_seg": _COOLDOWN_HTTP_ERRO,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": int((time.monotonic() - t0) * 1000),
        }

    duracao_ms = int((time.monotonic() - t0) * 1000)

    if status == 403:
        return {
            "ok": False, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "codigo": "NFSE_ADN_AUTH_ERRO",
            "erro": "Certificado nao habilitado no ADN NFS-e Nacional (HTTP 403)",
            "cooldown_recomendado_seg": _COOLDOWN_AUTH,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": duracao_ms,
        }

    if status in (204, 404):
        return {
            "ok": True, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "status": "SEM_DOCUMENTO",
            "status_processamento": "SEM_NOVIDADE",
            "ultimo_nsu": nsu_str, "max_nsu": nsu_str,
            "cooldown_recomendado_seg": _COOLDOWN_SEM_DOC,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": duracao_ms,
        }

    if status != 200:
        return {
            "ok": False, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "codigo": "NFSE_ADN_HTTP_ERRO",
            "erro": f"ADN respondeu HTTP {status}",
            "cooldown_recomendado_seg": _COOLDOWN_HTTP_ERRO,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": duracao_ms,
        }

    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return {
            "ok": False, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "codigo": "NFSE_ADN_XML_INVALIDO",
            "erro": "Resposta ADN nao e JSON valido",
            "cooldown_recomendado_seg": _COOLDOWN_HTTP_ERRO,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": duracao_ms,
        }

    lote     = payload.get("LoteDFe") or []
    status_p = payload.get("StatusProcessamento") or "DESCONHECIDO"
    ult_nsu  = str(payload.get("UltimoNSU") or nsu_str)
    max_nsu  = str(payload.get("MaxNSU") or "")

    if not isinstance(lote, list) or not lote:
        return {
            "ok": True, "provider": "nfse_nacional",
            "ambiente_adn": ambiente_adn, "nsu": nsu_str,
            "status": "SEM_DOCUMENTO",
            "status_processamento": status_p,
            "ultimo_nsu": ult_nsu, "max_nsu": max_nsu,
            "cooldown_recomendado_seg": _COOLDOWN_SEM_DOC,
            "documentos": [], "resumos": [], "erros": [], "results": [],
            "duracao_ms": duracao_ms,
        }

    documentos, erros = [], []
    ult_processado = ult_nsu
    for item in lote:
        item_nsu = str(item.get("NSU") or "")
        arq      = item.get("ArquivoXml")
        if item_nsu:
            ult_processado = item_nsu

        if not arq:
            erros.append({
                "ok": False, "provider": "nfse_nacional",
                "nsu": item_nsu, "codigo": "NFSE_ADN_LOTE_SEM_ARQUIVO",
                "erro": "Item do LoteDFe sem ArquivoXml",
                "status_xml": "ERRO",
            })
            continue

        try:
            xml = _decode_arquivo(arq)
        except Exception:
            erros.append({
                "ok": False, "provider": "nfse_nacional",
                "nsu": item_nsu, "codigo": "NFSE_ADN_DECODE_FALHOU",
                "erro": "Falha ao descompactar/decodificar ArquivoXml",
                "status_xml": "ERRO",
            })
            continue

        parsed = _parse_xml(xml, trace_id)
        # ADN entrega NFS-e completa; nao ha resumo hoje.
        if parsed.get("ok"):
            doc = _normalizar_doc(item, xml, parsed)
            if not incluir_xml_bruto:
                doc.pop("xml_bruto", None)
            documentos.append(doc)
        else:
            erros.append({
                "ok": False, "provider": "nfse_nacional",
                "nsu": item_nsu,
                "codigo": parsed.get("codigo") or "NFSE_ADN_PARSE_FALHOU",
                "erro":   parsed.get("erro")   or "Nao foi possivel parsear o XML NFS-e",
                "status_xml": "ERRO",
            })

    return {
        "ok": True,
        "provider": "nfse_nacional",
        "ambiente_adn": ambiente_adn,
        "nsu": nsu_str,
        "status": "DOCUMENTOS_LOCALIZADOS" if documentos else "SEM_DOCUMENTO",
        "status_processamento": status_p,
        "ultimo_nsu": ult_processado,
        "max_nsu":    max_nsu,
        "cooldown_recomendado_seg": _COOLDOWN_DOCS if documentos else _COOLDOWN_SEM_DOC,
        "documentos": documentos,
        "resumos":    [],
        "erros":      erros,
        # results[] com categoria — montado pelo caller (dfe_fetch_service) ou aqui
        "results":    (
            [{**d, "categoria": "COMPLETO"} for d in documentos]
            + [{**e, "categoria": "ERRO"} for e in erros]
        ),
        "duracao_ms": duracao_ms,
    }
