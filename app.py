"""
FiscalOne — Gateway fiscal técnico do ecossistema RLogix. Fase 1 parcial.
ADR-0035: gateway puro sem persistência própria.

Capacidade atual:
  - Parseia XML/ZIP de NF-e, CT-e, MDF-e básico (POST /fiscal/documents/import).
  - NÃO assina, NÃO transmite, NÃO consulta SEFAZ — providers são stubs.
  - Busca ativa SEFAZ/DFe: stub (Fase 2 pendente).
  - CT-e, MDF-e, cancelamento, eventos fiscais: honest-stub, retornam 501.
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

from xml_parser import parse_xml

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
        "fase":                "fase_1_parse_parcial",
        "sefaz_ativo":         False,
        "emissao_ativa":       False,
        "persistencia_propria": False,
        "capacidade": {
            "parse_xml_zip":   True,
            "gov_fetch":       False,
            "emitir_cte":      False,
            "emitir_mdfe":     False,
            "consulta_sefaz":  False,
            "certificado":     False,
        },
        "version":             "0.3.0",
        "adr":                 "ADR-0035",
    })

# ── POST /fiscal/documents/import ─────────────────────────────────────────────

@app.route("/fiscal/documents/import", methods=["POST"])
def import_documents():
    """
    Recebe XML/ZIP, parseia e retorna JSON normalizado.
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

    # Expandir ZIPs
    raw = []
    for f in files:
        fname = f.filename or ""
        data  = f.read()
        if fname.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for name in z.namelist():
                        if name.lower().endswith(".xml"):
                            raw.append((name, z.read(name)))
            except Exception as e:
                raw.append((fname, None, str(e)))
        elif fname.lower().endswith(".xml"):
            raw.append((fname, data))

    if not raw:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PARSE_ERROR",
            "erro":     "Nenhum XML encontrado nos arquivos enviados",
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

        parsed = parse_xml(
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
    Busca ativa DFe na SEFAZ/Nacional.
    Stub até Fase 2 (migração gov_import.py do CtrlOne — ADR-0034).
    Cooldown recomendado devolvido no envelope — vertical persiste se necessário.
    """
    trace_id      = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")

    if _producao_bloqueada():
        return _bloqueio_producao("gov_fetch", trace_id, source_system)

    _log_stdout("gov_fetch", "stub", trace_id, source_system=source_system)

    return jsonify({
        "ok":       False,
        "trace_id": trace_id,
        "codigo":   "ADR_0034_FASE_2_PENDENTE",
        "erro":     "Busca SEFAZ não implementada — aguarda Fase 2 (migração gov_import.py do CtrlOne)",
        "adr":      "ADR-0034",
        "fase":     "2.1",
        "cooldown_recomendado_seg": None,
    }), 501

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
