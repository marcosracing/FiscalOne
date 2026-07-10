"""
SefazProvider — comunicacao com webservices SEFAZ (NFeDistDFeInteresse / CTeDistDFeInteresse).

Fase 1 (ADR-0035):
  - gov_fetch(payload) IMPLEMENTADO: gateway puro sem persistencia propria.
  - Demais operacoes (sync/listar/emitir/detalhe/status) permanecem stub.
"""
from providers import GovProvider

_STUB = {"ok": False, "erro": "Nao implementado — Fase 1 gateway DFe apenas", "provider": "sefaz"}


class SefazProvider(GovProvider):

    def gov_fetch(self, payload, trace_id):
        """
        Executa uma consulta NFeDistDFeInteresse / CTeDistDFeInteresse.
        Payload esperado:
          cnpj_tenant, ambiente, tipo ("nfe"|"cte"), ultimo_nsu,
          cert_pfx_base64 (opcional), cert_password (opcional), cert_source.
        """
        from services import cert_provider
        from services import dfe_fetch_service

        cnpj_tenant = payload.get("cnpj_tenant") or ""
        tipo        = (payload.get("tipo") or "").lower().strip()
        ambiente    = (payload.get("ambiente") or "homologacao").lower().strip()
        ultimo_nsu  = payload.get("ultimo_nsu") or "0"

        if tipo not in ("nfe", "cte"):
            return {
                "ok": False,
                "codigo": "TIPO_NAO_SUPORTADO",
                "erro": "tipo deve ser 'nfe' ou 'cte'",
            }

        try:
            bundle = cert_provider.resolve_cert(payload, cnpj_tenant)
        except cert_provider.CertResolveError as e:
            return {"ok": False, "codigo": e.codigo, "erro": e.mensagem}

        cert_fonte = bundle.get("fonte")
        try:
            result = dfe_fetch_service.fetch_dfe(
                cert_pem   = bundle["cert_pem"],
                key_pem    = bundle["key_pem"],
                cnpj       = cnpj_tenant,
                tipo       = tipo,
                ambiente   = ambiente,
                ultimo_nsu = ultimo_nsu,
                trace_id   = trace_id,
            )
        finally:
            cert_provider.wipe(bundle)

        result["cert_fonte"] = cert_fonte
        return result

    # ── Stubs (Fase 2 pendente) ─────────────────────────────────────────
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
