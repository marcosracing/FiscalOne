# Handoff Fase D — FiscalOne provider e token FocusNFe por requisição

Data: 2026-07-17
Base: ADR-0043 (`RLogix_shared/adr/ADR-0043-focusnfe-provider-dfe-multitenant-multibanco.md`)
Commit anterior: `573d350` (Fase 2 HTTP + normalização de base URL).

## Objetivo

Permitir que `/fiscal/gov/fetch` escolha o provider por requisição e aceite token FocusNFe injetado no payload, sem quebrar SEFAZ.

`FISCAL_PROVIDER` e `FOCUSNFE_TOKEN` globais ficam apenas como **fallback de dev/teste local**. Em produção com múltiplos tenants em ambientes diferentes (Racing prod + R1 hom, etc.), a decisão vive na requisição.

## Contrato novo de `/fiscal/gov/fetch`

```json
{
  "provider": "sefaz|focusnfe",      // opcional; se ausente, fallback env FISCAL_PROVIDER
  "focusnfe_token": "...",           // opcional; usado só se provider=focusnfe
  "ambiente": "homologacao|producao",
  "tipo": "nfe|cte|nfse",
  "cnpj_tenant": "14 dígitos",
  "ultimo_nsu": "0|...",
  // Certificado A1 (só para SEFAZ):
  "cert_pfx_base64": "...",
  "cert_password": "...",
  "cert_cnpj": "...",
  "cert_valid_until": "..."
}
```

## Regras

| Cenário | Comportamento |
|---|---|
| `provider` ausente | Fallback ao env `FISCAL_PROVIDER` (comportamento legado) |
| `provider="sefaz"` | Instancia `SefazProvider`. Certificado A1 continua sendo resolvido do payload/env como antes |
| `provider="focusnfe"` | Instancia `FocusNFeProvider(token=focusnfe_token)`. Token no payload > env `FOCUSNFE_TOKEN` > vazio (erro) |
| `provider` fora da allowlist `{sefaz, focusnfe}` | HTTP **400 `PROVIDER_INVALIDO`**, sem fallback silencioso, sem eco do valor inválido |
| `provider="focusnfe"` **+** payload com `cert_pfx_base64`/`cert_password`/... | **Ignorado defensivamente** — chaves são removidas do payload antes de instanciar o provider. FiscalOne não deixa cert vazar para Focus |
| `provider="focusnfe"` sem token no payload e sem env | HTTP 400 `FOCUS_TOKEN_AUSENTE` (contrato existente preservado) |

## Arquivos alterados

- `app.py:215-247` — `get_provider(provider_name=None, token=None)`. Nova allowlist `_PROVIDERS_SUPORTADOS` no nível de módulo. Fallback: `resolved = provider_name or FISCAL_PROVIDER`. Se `provider_name` fornecido mas inválido → `raise ValueError`. Se ausente e env desconhecido → cai para SEFAZ (compat retro).
- `app.py:503-542` (bloco novo antes do `try` que instancia o provider) — extração de `provider_payload` + allowlist com HTTP 400. `focusnfe_token` extraído via `payload.pop()` (nunca `get`) para eliminar do dict antes de qualquer serialização. Se `provider="focusnfe"`, remove também `cert_pfx_base64/cert_password/cert_cnpj/cert_valid_until` como defesa em profundidade.
- `app.py::_status_para_codigo` — novo mapeamento `PROVIDER_INVALIDO → 400`.
- `providers/focusnfe_provider.py:232-244` — `FocusNFeProvider.__init__(token: str | None = None)`. Precedência: `token` injetado (strip + truthy) > `os.environ.get("FOCUSNFE_TOKEN", "")` > `""`. **Sem mutação de `self._token` em métodos** — instância nova por request via `get_provider()`.
- `_require_token()` inalterado — a mensagem literal `"FOCUSNFE_TOKEN obrigatorio para provider focusnfe."` já não expõe token.

## Regra SEFAZ × FocusNFe

- **SEFAZ:** 100% preservado. Payload aceita certificado A1 (`cert_pfx_base64` + `cert_password`) como antes. Sem alteração no `SefazProvider` nem em `services/cert_provider.py`.
- **FocusNFe:** autenticação via HTTP Basic (`Basic base64(token:)`, senha vazia). Certificado A1 NÃO é usado. Se vier no payload por engano (bug MapOne / legado), é removido antes do provider ser instanciado.

## Regra token injetado × env fallback

Precedência (aplicada dentro de `FocusNFeProvider.__init__`):

1. **Token injetado no construtor** (fornecido por `get_provider(..., token=focusnfe_token)`).
2. **Env `FOCUSNFE_TOKEN`** (fallback local/dev).
3. **Vazio** → `_require_token()` levanta `RuntimeError("FOCUSNFE_TOKEN obrigatorio...")` → `gov_fetch` retorna envelope `FOCUS_TOKEN_AUSENTE` → HTTP 400.

Testes cobrem:
- Injetado ganha de env.
- Injetado vazio/`None`/whitespace cai no env.
- Sem injetado e sem env → erro controlado (nunca crash, nunca vazamento).

## Blindagem contra certificado A1 no provider FocusNFe

O MapOne será ajustado depois para não enviar certificado quando `provider=focusnfe`. Até lá, o FiscalOne se protege:

```python
if provider_payload == "focusnfe":
    payload.pop("cert_pfx_base64", None)
    payload.pop("cert_password", None)
    payload.pop("cert_cnpj", None)
    payload.pop("cert_valid_until", None)
```

Teste `TestBlindagemCertFocusNFe::test_provider_focusnfe_ignora_cert_pfx_base64` valida: envelope de resposta **não contém** nenhum dos campos de cert e a chamada HTTP à Focus não usa cert.

## Segurança

Testes garantem que **nunca** aparecem em envelope, body HTTP ou stdout (`_log_stdout`):
- `focusnfe_token` (chave e valor)
- valor do token de teste (`TOKEN-SECRETO-DE-TESTE-XYZ789`, `TOKEN-DE-VAZAMENTO-XYZ`)
- `Authorization` (header vai só na chamada HTTP à Focus, nunca serializado)
- `Basic ` (o prefixo do header)
- `cert_pfx_base64`, `cert_password` (removidos antes de qualquer log)

`_log_stdout` tem schema JSON fixo — não loga payload nem headers. `@app.errorhandler(Exception)` loga só `type(exc).__name__`. `str(exc)` só aparece em duas linhas do provider (`FOCUS_TOKEN_AUSENTE` no `gov_fetch` e `baixar_danfe`), ambas com mensagens controladas de `_require_token()` que não expõem o token.

## Testes executados

```
python3 -m py_compile app.py providers/focusnfe_provider.py   # OK
git diff --check                                              # OK

source .venv/bin/activate
python -m pytest tests/test_fase_d_provider_por_request.py -v # 27 passed (1.08s)
python -m pytest                                              # 183 passed (1.55s)
```

Suite completa: **183 passed** (156 anteriores + 27 novos Fase D). Zero regressão. Zero HTTP real (todos os novos testes usam `@patch("providers.focusnfe_provider.requests.get")`).

## Riscos / pendências para MapOne Fase D

1. **MapOne deve enviar `focusnfe_token` no payload** quando `resolver_provider_fiscal_ativo` retornar `focusnfe`. Hoje MapOne já envia `provider` mas ainda não envia `focusnfe_token`. Até isso ser feito, o fallback é o env `FOCUSNFE_TOKEN` do FiscalOne (aceitável em dev, evitar em prod).
2. **MapOne não deve enviar certificado A1** quando `provider=focusnfe`. O FiscalOne já ignora defensivamente hoje, mas idealmente MapOne não envia — evita processamento inútil e reduz superfície de exposição.
3. **Coordenação de ambientes**: MapOne precisa passar `ambiente` correto (`producao|homologacao`) por empresa/tenant/AmbienteFiscal, não por env global. Confirmação está no plano de fases.
4. **Tests Fase D em MapOne**: validar end-to-end que `fiscalone_client.buscar_dfe(provider="focusnfe", ...)` monta payload com `focusnfe_token` corretamente.
