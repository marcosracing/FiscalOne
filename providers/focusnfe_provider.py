"""
FocusNFeProvider — stub JSON estruturado.

Nao implementa busca DFe recebida nem qualquer emissao. Todos os metodos
retornam envelope com codigo controlado PROVIDER_NAO_IMPLEMENTADO. Sem
caminho silencioso, sem chamada a SEFAZ/ADN, sem vazamento de detalhes.
"""
import os
from providers import GovProvider

FOCUSNFE_BASE_URL = os.getenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
# Token intencionalmente NAO exposto — nunca logar valor.

_STUB = {
    "ok":       False,
    "provider": "focusnfe",
    "codigo":   "PROVIDER_NAO_IMPLEMENTADO",
    "erro":     "Provider nao implementa busca DFe recebida.",
}


class FocusNFeProvider(GovProvider):
    # ── DFe recebido — nao implementado ────────────────────────────────────
    def gov_fetch(self, payload, trace_id):
        return {**_STUB, "trace_id": trace_id}

    def consultar_dfe_nsu(self, cert_pem, key_pem, cnpj, nsu, ambiente, trace_id):
        return {**_STUB, "trace_id": trace_id}

    # ── Rotas legadas (stubs — nunca liberadas nesta fase) ─────────────────
    def sync(self, cnpj):                                        return dict(_STUB)
    def listar_nfe(self, cnpj, pagina=1):                        return dict(_STUB)
    def listar_cte(self, cnpj, pagina=1):                        return dict(_STUB)
    def detalhe_nfe(self, chave):                                return dict(_STUB)
    def detalhe_cte(self, chave):                                return dict(_STUB)
    def emitir_cte(self, payload):                               return dict(_STUB)
    def emitir_mdfe(self, payload):                              return dict(_STUB)
    def cancelar_cte(self, chave, justificativa):                return dict(_STUB)
    def encerrar_mdfe(self, chave):                              return dict(_STUB)
    def incluir_condutor_mdfe(self, chave, payload):             return dict(_STUB)
    def status_sefaz(self, uf):                                  return dict(_STUB)
