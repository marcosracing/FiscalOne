"""GovProvider — interface base do FiscalOne."""

class GovProvider:
    def sync(self, cnpj: str) -> dict: raise NotImplementedError
    def listar_nfe(self, cnpj: str, pagina: int = 1) -> dict: raise NotImplementedError
    def listar_cte(self, cnpj: str, pagina: int = 1) -> dict: raise NotImplementedError
    def detalhe_nfe(self, chave: str) -> dict: raise NotImplementedError
    def detalhe_cte(self, chave: str) -> dict: raise NotImplementedError
    def emitir_cte(self, payload: dict) -> dict: raise NotImplementedError
    def emitir_mdfe(self, payload: dict) -> dict: raise NotImplementedError
    def cancelar_cte(self, chave: str, justificativa: str) -> dict: raise NotImplementedError
    def encerrar_mdfe(self, chave: str) -> dict: raise NotImplementedError
    def incluir_condutor_mdfe(self, chave: str, payload: dict) -> dict: raise NotImplementedError
    def status_sefaz(self, uf: str) -> dict: raise NotImplementedError
