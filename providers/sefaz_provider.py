"""
SefazProvider — comunicacao direta com webservices SEFAZ.
STUB: aguarda migracao do gov_import.py do CtrlOne (ADR-0028).
"""
from providers import GovProvider

_STUB = {"ok": False, "erro": "SefazProvider nao implementado — aguarda ADR-0028", "provider": "sefaz"}

class SefazProvider(GovProvider):
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
