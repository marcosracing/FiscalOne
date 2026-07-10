"""
FiscalOne — Gateway fiscal técnico do ecossistema RLogix. Fase 1 DFe recebidos.
ADR-0035: gateway puro sem persistência própria.

Capacidade atual:
  - Parseia XML/PDF/ZIP recebidos: NF-e, CT-e, MDF-e, NFS-e nacional/ABRASF
    e NFS-e PDF Prefeitura de São Paulo (POST /fiscal/documents/import).
  - NÃO assina, NÃO transmite, NÃO consulta SEFAZ — providers são stubs.
  - Busca ativa SEFAZ/DFe: stub (Fase 2 pendente).
  - Emissão de CT-e, MDF-e, cancelamento, eventos fiscais: honest-stub, 501.
  - Produção fiscal: bloqueada por padrão via flags duplas.
  - Sem banco, sem XML raw, sem cooldown, sem certificado em repouso.
  - Toda persistência é responsabilidade da vertical (MapOne, CtrlOne).
  - trace_id propaga em toda operação — não armazena.
Porta: 5002
"""
import os
import uuid
import time
import zipfile
import io
from flask import Flask, jsonify, request

from xml_parser import parse_xml, parse_pdf, parse_document

app = Flask(__name__)

FISCAL_PROVIDER = os.getenv("FISCAL_PROVIDER", "sefaz")
_PROD_AMBIENTES = {"prod", "producao", "produção", "production"}
_TRUE_VALUES = {"1", "true", "yes", "sim", "on"}

def _ambiente():
    return os.getenv("FISCALONE_AMBIENTE", "homologacao").strip().lower()

def _flag(name):
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES

def _producao_bloqueada():
    if _ambiente() not in _PROD_AMBIENTES:
        return False
    return not (
        _flag("FISCALONE_ENABLE_PRODUCAO")
        and _flag("MAPONE_FISCAL_PRODUCAO_READY")
    )

def _bloqueio_producao(operacao, trace_id, source_system="desconhecido"):
    _log_stdout(
        operacao,
        "bloqueado_producao",
        trace_id,
        source_system=source_system,
        erro_msg="Ambiente de produção bloqueado até MapOne estar pronto e testado",
    )
    return jsonify({
        "ok": False,
        "trace_id": trace_id,
        "codigo": "FISCALONE_PRODUCAO_BLOQUEADA",
        "erro": (
            "Operação fiscal em produção bloqueada. Use homologação até o MapOne "
            "estar exaustivamente testado e as flags FISCALONE_ENABLE_PRODUCAO e "
            "MAPONE_FISCAL_PRODUCAO_READY serem liberadas explicitamente."
        ),
        "ambiente": _ambiente(),
        "required_flags": [
            "FISCALONE_ENABLE_PRODUCAO=true",
            "MAPONE_FISCAL_PRODUCAO_READY=true",
        ],
    }), 403

def _provider_response(operacao, payload, status_padrao=501):
    status = 200 if payload.get("ok") else status_padrao
    return jsonify(payload), status

def _emissao_nao_liberada(doc_type, trace_id, source_system="desconhecido"):
    _log_stdout(
        f"emitir_{doc_type}",
        "bloqueado_validacoes",
        trace_id,
        source_system=source_system,
        erro_msg="Emissão fiscal bloqueada até gates TMS estarem completos",
    )
    return jsonify({
        "ok": False,
        "trace_id": trace_id,
        "codigo": "EMISSAO_FISCAL_NAO_LIBERADA",
        "erro": (
            f"Emissão de {doc_type.upper()} ainda não liberada. Testes devem ocorrer "
            "somente em homologação; produção permanece bloqueada até validação "
            "exaustiva do MapOne."
        ),
        "ambiente": _ambiente(),
        "gates_obrigatorios": [
            "CIOT quando aplicável",
            "VPO / Vale-Pedágio Obrigatório",
            "RNTRC / ANTT",
            "seguro e averbação",
            "documentos fiscais vinculados",
            "veículo e condutor válidos",
        ],
    }), 501

def get_provider():
    if FISCAL_PROVIDER == "focusnfe":
        from providers.focusnfe_provider import FocusNFeProvider
        return FocusNFeProvider()
    from providers.sefaz_provider import SefazProvider
    return SefazProvider()

def _trace(req):
    """Extrai ou gera trace_id. FiscalOne gera fo- se ausente."""
    tid = req.headers.get("X-Trace-Id", "").strip()
    return tid if tid else f"fo-{uuid.uuid4().hex}"

def _log_stdout(operacao, resultado, trace_id, **kwargs):
    """
    Log técnico estruturado em stdout — sem banco próprio (ADR-0035).
    A vertical persiste o log consultável em op_fiscal_log.
    """
    import json
    entry = {
        "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service":     "fiscalone",
        "operacao":    operacao,
        "resultado":   resultado,
        "trace_id":    trace_id,
        "source":      kwargs.get("source_system", "desconhecido"),
        "cnpj":        kwargs.get("company_cnpj"),
        "doc_type":    kwargs.get("doc_type"),
        "chave":       kwargs.get("chave_doc"),
        "duracao_ms":  kwargs.get("duracao_ms"),
        "erro":        kwargs.get("erro_msg"),
    }
    entry = {k: v for k, v in entry.items() if v is not None}
    print(json.dumps(entry, ensure_ascii=False), flush=True)

# ── Health ─────────────────────────────────────────────────────────────────────

@app.route("/fiscal/health")
def health():
    return jsonify({
        "ok":                  True,
        "provider":            FISCAL_PROVIDER,
        "ambiente":            _ambiente(),
        "producao_bloqueada":  _producao_bloqueada(),
        "fase":                "fase_1_dfe_recebidos",
        "sefaz_ativo":         False,
        "emissao_ativa":       False,
        "persistencia_propria": False,
        "capacidade": {
            "parse_xml_zip":   True,
            "parse_pdf":       True,
            "parse_nfse_xml":  True,
            "parse_nfse_pdf":  True,
            "gov_fetch":       False,
            "emitir_cte":      False,
            "emitir_mdfe":     False,
            "consulta_sefaz":  False,
            "certificado":     False,
        },
        "version":             "0.4.0",
        "adr":                 "ADR-0035",
    })

# ── POST /fiscal/documents/import ─────────────────────────────────────────────

@app.route("/fiscal/documents/import", methods=["POST"])
def import_documents():
    """
    Recebe XML, PDF ou ZIP com XML/PDF, parseia e retorna JSON normalizado.
    Não persiste nada — a vertical persiste no próprio banco.
    """
    trace_id      = _trace(request)
    import_origin = request.args.get("origin", "fiscalone_upload")
    source_system = request.headers.get("X-Source-System", "desconhecido")
    t0            = time.monotonic()

    files = request.files.getlist("files") or request.files.getlist("arquivo")
    if not files:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PARSE_ERROR",
            "erro":     "Nenhum arquivo enviado",
        }), 400

    # Expandir ZIPs (XML e PDF internos)
    raw = []  # cada item: (filename, data) ou (filename, None, erro)
    for f in files:
        fname = f.filename or ""
        data  = f.read()
        low   = fname.lower()
        if low.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for name in z.namelist():
                        nlow = name.lower()
                        if nlow.endswith(".xml") or nlow.endswith(".pdf"):
                            raw.append((f"{fname}/{name}", z.read(name)))
            except Exception as e:
                raw.append((fname, None, str(e)))
        elif low.endswith(".xml") or low.endswith(".pdf"):
            raw.append((fname, data))
        else:
            raw.append((fname, None, "Extensão não suportada (aceita .xml, .pdf, .zip)"))

    if not raw:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PARSE_ERROR",
            "erro":     "Nenhum XML/PDF encontrado nos arquivos enviados",
        }), 400

    results = []
    for item in raw:
        fname = item[0]
        data  = item[1] if len(item) >= 2 else None

        if data is None:
            err = item[2] if len(item) >= 3 else "erro desconhecido"
            results.append({
                "ok": False, "file": fname, "trace_id": trace_id,
                "codigo": "PARSE_ERROR", "erro": err,
            })
            continue

        parsed = parse_document(
            data, fname,
            import_origin=import_origin,
            trace_id=trace_id,
        )
        results.append(parsed)

    duracao = int((time.monotonic() - t0) * 1000)
    ok_count  = sum(1 for r in results if r.get("ok"))
    err_count = len(results) - ok_count

    _log_stdout("parse_xml",
                "ok" if ok_count > 0 else "erro",
                trace_id,
                source_system=source_system,
                duracao_ms=duracao)

    return jsonify({
        "ok":       True,
        "trace_id": trace_id,
        "total":    ok_count,
        "erros":    err_count,
        "results":  results,
    })

# ── POST /fiscal/gov/fetch ─────────────────────────────────────────────────────

@app.route("/fiscal/gov/fetch", methods=["POST"])
def gov_fetch():
    """
    Busca ativa DFe na SEFAZ (NF-e / CT-e — NFeDistDFeInteresse / CTeDistDFeInteresse).

    Gateway puro (ADR-0035): nao persiste NSU, XML ou cooldown.
    Certificado A1 vem por requisicao (cert_pfx_base64+cert_password) ou via env
    (fallback homologacao). Descartado ao final da chamada.

    Payload:
      cnpj_tenant, ambiente ("producao"|"homologacao"), tipo ("nfe"|"cte"),
      ultimo_nsu, cert_pfx_base64 (opcional), cert_password (opcional),
      cert_source (opcional: "inline_base64"|"env").

    Sempre devolve JSON — nunca traceback HTML.
    """
    trace_id      = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    t0            = time.monotonic()

    if _producao_bloqueada():
        return _bloqueio_producao("gov_fetch", trace_id, source_system)

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    if not isinstance(payload, dict) or not payload:
        _log_stdout("gov_fetch", "erro", trace_id,
                    source_system=source_system,
                    erro_msg="payload JSON ausente ou invalido")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PAYLOAD_INVALIDO",
            "erro":     "Payload JSON obrigatorio (cnpj_tenant, ambiente, tipo, ultimo_nsu)",
        }), 400

    cnpj_tenant = (payload.get("cnpj_tenant") or "").strip()
    tipo        = (payload.get("tipo") or "").strip().lower()

    if not cnpj_tenant or len(cnpj_tenant.replace(".", "").replace("/", "").replace("-", "")) < 14:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "CNPJ_INVALIDO",
            "erro":     "cnpj_tenant obrigatorio (14 digitos)",
        }), 400

    if tipo not in ("nfe", "cte"):
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "TIPO_NAO_SUPORTADO",
            "erro":     "tipo obrigatorio: 'nfe' ou 'cte'",
        }), 400

    try:
        provider = get_provider()
        if not hasattr(provider, "gov_fetch"):
            return jsonify({
                "ok":       False,
                "trace_id": trace_id,
                "codigo":   "PROVIDER_SEM_GOV_FETCH",
                "erro":     f"Provider {FISCAL_PROVIDER} nao implementa gov_fetch",
            }), 501
        result = provider.gov_fetch(payload, trace_id)
    except Exception as e:
        # Nao vaza traceback nem stack — apenas tipo de erro
        _log_stdout("gov_fetch", "erro", trace_id,
                    source_system=source_system,
                    erro_msg=f"{type(e).__name__}: excecao interna")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "ERRO_INTERNO",
            "erro":     "Erro interno ao consultar SEFAZ — verifique logs do FiscalOne",
        }), 500

    duracao_ms = int((time.monotonic() - t0) * 1000)
    resultado_log = "ok" if result.get("ok") else "erro"
    _log_stdout("gov_fetch", resultado_log, trace_id,
                source_system=source_system,
                company_cnpj=cnpj_tenant, doc_type=tipo,
                duracao_ms=duracao_ms,
                erro_msg=result.get("codigo") if not result.get("ok") else None)

    envelope = {
        "ok":                       bool(result.get("ok")),
        "trace_id":                 trace_id,
        "codigo":                   result.get("codigo"),
        "cstat":                    result.get("cstat"),
        "xmotivo":                  result.get("xmotivo"),
        "ultimo_nsu":               result.get("ultimo_nsu"),
        "max_nsu":                  result.get("max_nsu"),
        "cooldown_recomendado_seg": result.get("cooldown_recomendado_seg"),
        "documentos":               result.get("documentos") or [],
        "resumos":                  result.get("resumos") or [],
        "erros":                    result.get("erros") or [],
        "results":                  result.get("results") or [],
        "duracao_ms":               result.get("duracao_ms") or duracao_ms,
        "cert_fonte":               result.get("cert_fonte"),
        "erro":                     result.get("erro") if not result.get("ok") else None,
        "ambiente":                 (payload.get("ambiente") or "homologacao").lower(),
        "tipo":                     tipo,
    }
    _ARRAY_KEYS = ("documentos", "resumos", "erros", "results")
    envelope = {k: v for k, v in envelope.items() if v is not None or k in _ARRAY_KEYS or k == "ok"}

    status = 200 if envelope["ok"] else _status_para_codigo(envelope.get("codigo"))
    return jsonify(envelope), status


def _status_para_codigo(codigo):
    """Mapeia codigo de erro controlado -> HTTP status."""
    if codigo in ("CERT_NAO_CONFIGURADO", "CERT_CNPJ_DIVERGENTE",
                  "CERT_ABERTURA_FALHOU", "CERT_BASE64_INVALIDO",
                  "CERT_INVALIDO", "CERT_ENV_INVALIDO",
                  "CERT_SEM_CNPJ", "CERT_FONTE_NAO_SUPORTADA"):
        return 400
    if codigo in ("SEFAZ_INDISPONIVEL", "SEFAZ_HTTP_ERRO", "SEFAZ_XML_INVALIDO", "TLS_ERRO"):
        return 502
    return 500

# ── Rotas legadas (provider pattern — mantidas, stubs) ────────────────────────

@app.route("/fiscal/sync/<cnpj>", methods=["POST"])
def sync(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("sync", trace_id, source_system)
    return _provider_response("sync", get_provider().sync(cnpj))

@app.route("/fiscal/nfe/<cnpj>")
def listar_nfe(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("listar_nfe", trace_id, source_system)
    return _provider_response("listar_nfe", get_provider().listar_nfe(cnpj))

@app.route("/fiscal/cte/<cnpj>")
def listar_cte(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("listar_cte", trace_id, source_system)
    return _provider_response("listar_cte", get_provider().listar_cte(cnpj))

@app.route("/fiscal/nfe/chave/<chave>")
def detalhe_nfe(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("detalhe_nfe", trace_id, source_system)
    return _provider_response("detalhe_nfe", get_provider().detalhe_nfe(chave))

@app.route("/fiscal/cte/chave/<chave>")
def detalhe_cte(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("detalhe_cte", trace_id, source_system)
    return _provider_response("detalhe_cte", get_provider().detalhe_cte(chave))

@app.route("/fiscal/cte", methods=["POST"])
def emitir_cte():
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("emitir_cte", trace_id, source_system)
    return _emissao_nao_liberada("cte", trace_id, source_system)

@app.route("/fiscal/mdfe", methods=["POST"])
def emitir_mdfe():
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("emitir_mdfe", trace_id, source_system)
    return _emissao_nao_liberada("mdfe", trace_id, source_system)

@app.route("/fiscal/mdfe/<chave>/encerrar", methods=["POST"])
def encerrar_mdfe(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("encerrar_mdfe", trace_id, source_system)
    return _emissao_nao_liberada("mdfe_encerramento", trace_id, source_system)

@app.route("/fiscal/mdfe/<chave>/condutor", methods=["POST"])
def incluir_condutor_mdfe(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("incluir_condutor_mdfe", trace_id, source_system)
    return _emissao_nao_liberada("mdfe_condutor", trace_id, source_system)

@app.route("/fiscal/cte/<chave>", methods=["DELETE"])
def cancelar_cte(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("cancelar_cte", trace_id, source_system)
    return _emissao_nao_liberada("cte_cancelamento", trace_id, source_system)

@app.route("/fiscal/status/<uf>")
def status_sefaz(uf):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("status_sefaz", trace_id, source_system)
    return _provider_response("status_sefaz", get_provider().status_sefaz(uf))

if __name__ == "__main__":
    app.run(port=5002, debug=False)
