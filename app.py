"""
FiscalOne — Gateway Gov do ecossistema RLogix.
Motor fiscal completo: comunica SEFAZ, parseia documentos, emite CT-e/MDF-e.
Provider pattern: FISCAL_PROVIDER=sefaz (padrao) | focusnfe.
Porta: 5002
"""
from flask import Flask, jsonify, request
import os

app = Flask(__name__)

FISCAL_PROVIDER = os.getenv("FISCAL_PROVIDER", "sefaz")

def get_provider():
    if FISCAL_PROVIDER == "focusnfe":
        from providers.focusnfe_provider import FocusNFeProvider
        return FocusNFeProvider()
    from providers.sefaz_provider import SefazProvider
    return SefazProvider()

@app.route("/fiscal/health")
def health():
    return jsonify({"ok": True, "provider": FISCAL_PROVIDER, "version": "0.1.0"})

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
    return jsonify(get_provider().emitir_cte(request.get_json(force=True) or {}))

@app.route("/fiscal/mdfe", methods=["POST"])
def emitir_mdfe():
    return jsonify(get_provider().emitir_mdfe(request.get_json(force=True) or {}))

@app.route("/fiscal/mdfe/<chave>/encerrar", methods=["POST"])
def encerrar_mdfe(chave):
    return jsonify(get_provider().encerrar_mdfe(chave))

@app.route("/fiscal/mdfe/<chave>/condutor", methods=["POST"])
def incluir_condutor_mdfe(chave):
    return jsonify(get_provider().incluir_condutor_mdfe(chave, request.get_json(force=True) or {}))

@app.route("/fiscal/cte/<chave>", methods=["DELETE"])
def cancelar_cte(chave):
    data = request.get_json(force=True) or {}
    return jsonify(get_provider().cancelar_cte(chave, data.get("justificativa", "")))

@app.route("/fiscal/status/<uf>")
def status_sefaz(uf):
    return jsonify(get_provider().status_sefaz(uf))

if __name__ == "__main__":
    app.run(port=5002, debug=False)
