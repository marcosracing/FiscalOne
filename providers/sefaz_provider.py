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
        Executa uma consulta DFe.
          - tipo "nfe"|"cte" → SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse.
          - tipo "nfse"      → ADN NFS-e Nacional por NSU.

        Payload esperado:
          cnpj_tenant, ambiente, tipo, ultimo_nsu,
          cert_pfx_base64 (opcional), cert_password (opcional), cert_source,
          data_inicio (opcional YYYY-MM-DD, metadado NFS-e).
        """
        from services import cert_provider
        from services import dfe_fetch_service

        cnpj_tenant = payload.get("cnpj_tenant") or ""
        tipo        = (payload.get("tipo") or "").lower().strip()
        ambiente    = (payload.get("ambiente") or "homologacao").lower().strip()
        ultimo_nsu  = payload.get("ultimo_nsu") or "0"
        data_inicio = payload.get("data_inicio")

        if tipo not in ("nfe", "cte", "nfse"):
            return {
                "ok": False,
                "codigo": "TIPO_NAO_SUPORTADO",
                "erro": "tipo deve ser 'nfe', 'cte' ou 'nfse'",
            }

        try:
            bundle = cert_provider.resolve_cert(payload, cnpj_tenant)
        except cert_provider.CertResolveError as e:
            return {"ok": False, "codigo": e.codigo, "erro": e.mensagem}

        cert_fonte = bundle.get("fonte")
        try:
            result = dfe_fetch_service.fetch_dfe(
                cert_pem    = bundle["cert_pem"],
                key_pem     = bundle["key_pem"],
                cnpj        = cnpj_tenant,
                tipo        = tipo,
                ambiente    = ambiente,
                ultimo_nsu  = ultimo_nsu,
                trace_id    = trace_id,
                data_inicio = data_inicio,
            )
        finally:
            cert_provider.wipe(bundle)

        result["cert_fonte"] = cert_fonte
        return result

    def consultar_dfe_nsu(self, cert_pem, key_pem, cnpj, nsu, ambiente, trace_id):
        """
        Consulta por NSU — delega ao dfe_fetch_service com tipo=nfe (default).
        Uso interno / diagnostico. Producao continua indo por gov_fetch.
        """
        from services import dfe_fetch_service
        return dfe_fetch_service.fetch_dfe(
            cert_pem=cert_pem, key_pem=key_pem, cnpj=cnpj,
            tipo="nfe", ambiente=ambiente, ultimo_nsu=nsu, trace_id=trace_id,
        )

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
