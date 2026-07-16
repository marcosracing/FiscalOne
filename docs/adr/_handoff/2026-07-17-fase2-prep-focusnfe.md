# Handoff Fase 2-prep — FocusNFeProvider sem HTTP real

Data: 2026-07-17
Escopo: FiscalOne apenas — nenhuma alteração em MapOne/CtrlOne/LegalOne.
Commit base: `73ce2b0`.

## Objetivo

Preparar o FiscalOne para receber o `FocusNFeProvider` real futuramente (Fase 2 HTTP), sem ativar qualquer integração externa nesta fase. Infraestrutura local: normalização de cursor, guard de emissão, mascaramento de token, leitura segura de envs, schemas expandidos e allowlist de origem.

## Arquivos alterados

- `services/nsu_utils.py` — novo branch `_FOCUSNFE_PROVIDERS`, regra de cursor `versao` preservada como string, docstring atualizada, assinatura passa a aceitar `int` (para `versao` que vem como int em JSON Focus).
- `providers/focusnfe_provider.py` — nova classe `EmissaoProibida(RuntimeError)`, função `_masked_token()`, `__init__` lendo envs sem fail-fast, `_require_token()` como fail-fast local para uso futuro, guards em `emitir_cte` e `emitir_mdfe`. `gov_fetch` e `consultar_dfe_nsu` continuam retornando `_STUB` (PROVIDER_NAO_IMPLEMENTADO). Métodos `cancelar_cte`, `encerrar_mdfe`, `incluir_condutor_mdfe` **preservados como stub** (não são `emitir_*` estritamente).
- `schemas/__init__.py` — `ImportOrigin` estendido com `"fiscalone_focusnfe"`.
- `schemas/nfe_schema.py` — `NFeDocOpcional` estendido com campos `versao: int`, `raw_json_focus: str`, `danfe_sha256: str`, `danfe_fonte: str`.
- `.env.example` — bloco Focus reescrito. Adicionadas `FOCUSNFE_AMBIENTE=producao` e `FOCUSNFE_TIMEOUT=30`. `FOCUSNFE_HOMOLOGACAO` mantida como legacy (não lida pelo provider atual — comentário adicionado).
- `tests/test_nsu_utils.py` — nova classe `TestNsuFocusNFe` (7 casos); `TestNsuProviderDesconhecido` agora usa `"bogus_provider"` em vez de `"focusnfe"`.
- `tests/test_focusnfe_preparacao.py` — novo arquivo, 24 casos cobrindo `_masked_token`, `EmissaoProibida`, stubs preservados, `__init__` sem fail-fast, `_require_token`, `ImportOrigin`.
- `docs/adr/_handoff/2026-07-17-fase2-prep-focusnfe.md` — este handoff.

## Implementado

- `normalizar_nsu("focusnfe" | "fiscalone_focusnfe", ...)` preserva `versao` como string; aceita `int`, `str`, `None`; nunca aplica zfill; None/vazio → `"0"`.
- `EmissaoProibida(RuntimeError)` levantada em `emitir_cte` e `emitir_mdfe` do `FocusNFeProvider`.
- `_masked_token(token)` mascara para logs: `None`/vazio → `"***[ausente]"`; ≤4 chars → `"***"`; senão `"***" + últimos 4`. Nunca retorna valor completo.
- `FocusNFeProvider.__init__()` lê `FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL` (`.rstrip("/")` — remove barra final), `FOCUSNFE_TIMEOUT` (int com fallback 30 se inválido). **Nenhum fail-fast no boot global do Flask.**
- `_require_token()` fail-fast local para uso em Fase 2 quando os métodos passarem a fazer HTTP real.
- `NFeDocOpcional` inclui os 4 campos Focus opcionais. Consumidores que ignoram chaves desconhecidas seguem funcionando.
- `ImportOrigin` inclui `"fiscalone_focusnfe"`. `VALID_IMPORT_ORIGIN` (frozenset derivado) reflete automaticamente.
- `.env.example` documenta `FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL`, `FOCUSNFE_AMBIENTE`, `FOCUSNFE_TIMEOUT`.

## Decisões arquiteturais nesta fase

1. **Não fail-fast no boot.** `FISCAL_PROVIDER=focusnfe` sem `FOCUSNFE_TOKEN` NÃO derruba o app. Os métodos hoje são stub — `_require_token()` só precisa ser chamado quando implementarmos HTTP real em Fase 2. Isto preserva o comportamento observado por `test_health_e_boot.py::test_focusnfe_gera_warning_e_501`.
2. **Escopo estrito de `EmissaoProibida`.** Apenas `emitir_*` levantam. `cancelar_cte`, `encerrar_mdfe`, `incluir_condutor_mdfe` continuam retornando `_STUB` — não são `emitir_*` estritamente. Rotas do `app.py` já bloqueiam essas operações via `bloquear_emissao()` (defesa em profundidade nas rotas). Se for necessário reforçar no provider também, tratar como fase separada.
3. **Sem `_sanitize_headers`.** `_log_stdout` (`app.py:227-261`) tem schema JSON fixo com chaves permitidas — não loga headers nem tokens hoje. Nada a fazer nesta fase.
4. **Compat retro preservada.** Atributo de módulo `FOCUSNFE_BASE_URL` (usado historicamente por importadores externos) foi mantido no final do arquivo.

## Fora de escopo

- Nenhum HTTP real para `api.focusnfe.com.br`.
- Nenhum `gov_fetch()` real — segue stub.
- Nenhum download de XML/DANFE.
- Nenhuma persistência.
- Nenhuma alteração em MapOne, CtrlOne ou LegalOne.
- Nenhuma ativação em produção. Nenhum deploy. Nenhum push.

## Testes executados

```
python3 -m py_compile app.py providers/*.py services/*.py schemas/*.py   # OK
git diff --check                                                          # OK
python -m pytest tests/test_nsu_utils.py tests/test_focusnfe_preparacao.py -v
    # 43 passed
python -m pytest                                                          # 91 passed
```

Toda a suite existente (SefazProvider, health, boot warnings, parsers, envelope, provider contract) continua verde. Zero chamada HTTP real disparada.

## Próxima fase

**Fase 2 HTTP** — `FocusNFeProvider.gov_fetch()` real com:
- HTTP Basic (`Authorization: Basic base64(token:)` — senha vazia, NÃO Bearer).
- `GET /v2/nfes_recebidas?cnpj=...&versao=...`.
- Parse de `X-Total-Count` e `X-Max-Version`.
- Chamar `_require_token()` no início.
- Mapear resposta JSON Focus → `NFeDocOpcional` com `versao`, `raw_json_focus`.
- Testes com mock (`responses` lib ou stub HTTP local); zero chamada real.
- Fase separada para DANFE PDF (`GET /v2/nfes_recebidas/{chave}.pdf` com 302 redirect sem Authorization no segundo GET; hash + persist BLOB no MapOne).

## Status

- Sem push. Sem deploy.
- Commit local — hash a preencher após execução.
