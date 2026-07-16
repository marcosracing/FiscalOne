"""
FocusNFeProvider — infraestrutura preparada, sem HTTP real (Fase 2-prep).

- `gov_fetch` e `consultar_dfe_nsu` continuam retornando envelope controlado
  `PROVIDER_NAO_IMPLEMENTADO` — Fase 2 (HTTP real) e prep separada.
- Metodos `emitir_*` levantam `EmissaoProibida` — FocusNFe no FiscalOne e
  usado apenas para recebimento de documentos; emissao permanece bloqueada
  por design em toda esta fase.
- Envs FocusNFe (`FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL`, `FOCUSNFE_TIMEOUT`)
  sao lidas no `__init__` sem fail-fast — o boot global do Flask nao pode
  quebrar so porque o token nao esta setado; a exigencia acontece no ponto
  de uso via `_require_token()`.
- `_masked_token()` mascara tokens em logs futuros. Nunca logar valor bruto.
"""
import os

from providers import GovProvider


class EmissaoProibida(RuntimeError):
    """Emissao via FocusNFe bloqueada por design nesta fase.

    FocusNFe no FiscalOne sera usado apenas para recebimento de documentos.
    """


def _masked_token(token: str | None) -> str:
    """Mascara token para logs. Nunca retorna o valor completo."""
    if not token:
        return "***[ausente]"
    token = str(token)
    if len(token) <= 4:
        return "***"
    return f"***{token[-4:]}"


_STUB = {
    "ok":       False,
    "provider": "focusnfe",
    "codigo":   "PROVIDER_NAO_IMPLEMENTADO",
    "erro":     "Provider nao implementa busca DFe recebida.",
}


class FocusNFeProvider(GovProvider):
    def __init__(self):
        self._token = os.environ.get("FOCUSNFE_TOKEN", "")
        self._base_url = os.environ.get(
            "FOCUSNFE_BASE_URL",
            "https://api.focusnfe.com.br/v2",
        ).rstrip("/")
        try:
            self._timeout = int(os.environ.get("FOCUSNFE_TIMEOUT", "30"))
        except (TypeError, ValueError):
            self._timeout = 30

    def _require_token(self) -> str:
        """Fail-fast local — usar quando implementar HTTP real (Fase 2)."""
        if not self._token:
            raise RuntimeError(
                "FOCUSNFE_TOKEN obrigatorio para provider focusnfe."
            )
        return self._token

    # ── DFe recebido — ainda nao implementado (Fase 2 HTTP real) ───────────
    def gov_fetch(self, payload, trace_id):
        return {**_STUB, "trace_id": trace_id}

    def consultar_dfe_nsu(self, cert_pem, key_pem, cnpj, nsu, ambiente, trace_id):
        return {**_STUB, "trace_id": trace_id}

    # ── Rotas legadas de consulta (stubs) ──────────────────────────────────
    def sync(self, cnpj):                                        return dict(_STUB)
    def listar_nfe(self, cnpj, pagina=1):                        return dict(_STUB)
    def listar_cte(self, cnpj, pagina=1):                        return dict(_STUB)
    def detalhe_nfe(self, chave):                                return dict(_STUB)
    def detalhe_cte(self, chave):                                return dict(_STUB)
    def status_sefaz(self, uf):                                  return dict(_STUB)

    # ── Emissao — bloqueada por design (defesa em profundidade) ────────────
    # As rotas do app.py ja bloqueiam via `bloquear_emissao()`. Aqui garantimos
    # que qualquer chamada direta ao provider (bypass de rota) tambem falha.
    def emitir_cte(self, payload):
        raise EmissaoProibida(
            "emitir_cte via FocusNFe bloqueado — FiscalOne nesta fase e apenas "
            "recebimento DFe."
        )

    def emitir_mdfe(self, payload):
        raise EmissaoProibida(
            "emitir_mdfe via FocusNFe bloqueado — FiscalOne nesta fase e apenas "
            "recebimento DFe."
        )

    # ── Operacoes relacionadas (nao sao emitir_*) — mantidas como STUB ─────
    # Cancelamento, encerramento e inclusao de condutor sao operacoes de
    # escrita fiscal mas nao sao emissao propriamente dita. Fase 2-prep
    # preserva o comportamento original (stub silencioso). Rotas do app.py
    # ja bloqueiam via `bloquear_emissao()`.
    def cancelar_cte(self, chave, justificativa):                return dict(_STUB)
    def encerrar_mdfe(self, chave):                              return dict(_STUB)
    def incluir_condutor_mdfe(self, chave, payload):             return dict(_STUB)


# Compatibilidade retro: alguns modulos importaram `FOCUSNFE_BASE_URL` como
# atributo de modulo. Preservar sem quebrar semantica anterior.
FOCUSNFE_BASE_URL = os.getenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
