"""
FocusNFeProvider — integracao Focus NF-e REST API.
STUB: implementar quando FISCAL_PROVIDER=focusnfe.
Docs: https://focusnfe.com.br/doc/
"""
import os
from providers import GovProvider

FOCUSNFE_BASE_URL = os.getenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
FOCUSNFE_TOKEN    = os.getenv("FOCUSNFE_TOKEN", "")

_STUB = {"ok": False, "erro": "FocusNFeProvider nao implementado", "provider": "focusnfe"}

class FocusNFeProvider(GovProvider):
    def _auth(self): return (FOCUSNFE_TOKEN, "")
    def sync(self, cnpj): return _STUB
    def listar_nfe(self, cnpj, pagina=1): return _STUB
    def listar_cte(self, cnpj, pagina=1): return _STUB
    def detalhe_nfe(self, chave): return _STUB
    def detalhe_cte(self, chave): return _STUB
    def emitir_cte(self, payload): return _STUB
    def emitir_mdfe(self, payload): return _STUB
    def cancelar_cte(self, chave, justificativa): return _STUB
    def encerrar_mdfe(self, chave): return _STUB
    def incluir_condutor_mdfe(self, chave, payload): return _STUB
    def status_sefaz(self, uf): return _STUB
