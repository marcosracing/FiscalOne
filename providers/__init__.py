"""
GovProvider — contrato abstrato do FiscalOne para providers de DFe.

Providers concretos DEVEM implementar `gov_fetch` e `consultar_dfe_nsu`.
Metodos nao implementados levantam NotImplementedError; a rota
`/fiscal/gov/fetch` traduz para o codigo controlado
`PROVIDER_NAO_IMPLEMENTADO` no envelope.
"""
from abc import ABC, abstractmethod


class GovProvider(ABC):
    # ── Metodos ABSTRATOS — provider concreto deve implementar ─────────────
    @abstractmethod
    def gov_fetch(self, payload: dict, trace_id: str) -> dict:
        """Executa uma consulta DFe (SEFAZ ou ADN). Retorna envelope dict."""
        raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: gov_fetch")

    @abstractmethod
    def consultar_dfe_nsu(self, cert_pem, key_pem, cnpj, nsu,
                           ambiente, trace_id) -> dict:
        """Consulta DFe por NSU (uso interno / diagnostico)."""
        raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: consultar_dfe_nsu")

    # ── Rotas legadas (stubs opcionais — nunca liberadas nesta fase) ───────
    def sync(self, cnpj): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: sync")
    def listar_nfe(self, cnpj, pagina=1): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: listar_nfe")
    def listar_cte(self, cnpj, pagina=1): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: listar_cte")
    def detalhe_nfe(self, chave): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: detalhe_nfe")
    def detalhe_cte(self, chave): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: detalhe_cte")
    def emitir_cte(self, payload): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: emitir_cte")
    def emitir_mdfe(self, payload): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: emitir_mdfe")
    def cancelar_cte(self, chave, justificativa): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: cancelar_cte")
    def encerrar_mdfe(self, chave): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: encerrar_mdfe")
    def incluir_condutor_mdfe(self, chave, payload): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: incluir_condutor_mdfe")
    def status_sefaz(self, uf): raise NotImplementedError("PROVIDER_NAO_IMPLEMENTADO: status_sefaz")
