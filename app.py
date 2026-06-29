"""
FiscalOne — Ponte técnica fiscal do ecossistema RLogix.
ADR-0035: gateway puro sem persistência própria.
  - Parseia, assina, transmite, consulta serviços fiscais.
  - Não persiste XML, protocolo, evento, cooldown ou certificado.
  - Toda persistência é responsabilidade da vertical (MapOne, CtrlOne).
  - trace_id obrigatório em toda operação — propaga, não armazena.
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
        "ok":       True,
        "provider": FISCAL_PROVIDER,
        "version":  "0.3.0",
        "adr":      "ADR-0035",
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
    return jsonify(get_provider().sync(cnpj))

@app.route("/fiscal/nfe/<cnpj>")
def listar_nfe(cnpj):
    return jsonify(get_provider().listar_nfe(cnpj))

@app.route("/fiscal/cte/<cnpj>")
def listar_cte(cnpj):
    return jsonify(get_provider().listar_cte(cnpj))

@app.route("/fiscal/nfe/chave/<chave>")
def detalhe_nfe(chave):
    return jsonify(get_provider().detalhe_nfe(chave))

@app.route("/fiscal/cte/chave/<chave>")
def detalhe_cte(chave):
    return jsonify(get_provider().detalhe_cte(chave))

@app.route("/fiscal/cte", methods=["POST"])
def emitir_cte():
    return jsonify(get_provider().emitir_cte(
        request.get_json(force=True) or {}))

@app.route("/fiscal/mdfe", methods=["POST"])
def emitir_mdfe():
    return jsonify(get_provider().emitir_mdfe(
        request.get_json(force=True) or {}))

@app.route("/fiscal/mdfe/<chave>/encerrar", methods=["POST"])
def encerrar_mdfe(chave):
    return jsonify(get_provider().encerrar_mdfe(chave))

@app.route("/fiscal/mdfe/<chave>/condutor", methods=["POST"])
def incluir_condutor_mdfe(chave):
    return jsonify(get_provider().incluir_condutor_mdfe(
        chave, request.get_json(force=True) or {}))

@app.route("/fiscal/cte/<chave>", methods=["DELETE"])
def cancelar_cte(chave):
    data = request.get_json(force=True) or {}
    return jsonify(get_provider().cancelar_cte(
        chave, data.get("justificativa", "")))

@app.route("/fiscal/status/<uf>")
def status_sefaz(uf):
    return jsonify(get_provider().status_sefaz(uf))

if __name__ == "__main__":
    app.run(port=5002, debug=False)
