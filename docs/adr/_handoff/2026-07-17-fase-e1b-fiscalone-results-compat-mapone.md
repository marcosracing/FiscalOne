# Handoff Fase E1B — FiscalOne: `results[]` compat MapOne

Data: 2026-07-17
Base: ADR-0043 · Commit anterior: `619ce84` (Fase D — provider/token por requisição).

## Objetivo

Normalizar o envelope de `/fiscal/gov/fetch` para que documentos entregues pelo provider em `documentos[]` também apareçam em `results[]`. MapOne consome `fo_resp["results"]`; providers modernos (`FocusNFeProvider`) preenchem `documentos[]` — sem esta compatibilidade, MapOne recebe lista vazia mesmo quando a Focus retornou NF-e.

## Alteração

**Arquivo:** `app.py:591-599` (bloco de extração de arrays após `provider.gov_fetch`).

Antes:
```python
docs_arr    = result.get("documentos") or []
resumos_arr = result.get("resumos") or []
erros_arr   = result.get("erros") or []
results_arr = result.get("results") or []
```

Depois:
```python
docs_arr    = result.get("documentos") or []
resumos_arr = result.get("resumos") or []
erros_arr   = result.get("erros") or []
# Fase E1B: providers modernos (FocusNFe) preenchem `documentos[]` mas nao
# `results[]`. MapOne consome `results[]`. Fallback: se provider nao entregou
# `results` explicito, espelhamos `documentos` para preservar compat.
# Providers legados que ja preenchem `results` (ex.: SEFAZ) tem esse array
# preservado — nunca sobrescrito.
results_arr = result.get("results") or docs_arr
```

## Regras semânticas do fallback

- Provider preenche **apenas `documentos[]`** (padrão FocusNFe) → `results_arr` recebe cópia de `docs_arr` (mesma referência de lista).
- Provider preenche **apenas `results[]`** → `results_arr` preservado (SEFAZ legado, se aplicável).
- Provider preenche **ambos e ambos não-vazios** → `results` explícito ganha (o `or` só cai no fallback quando o valor à esquerda é falsy).
- Provider preenche **ambos vazios** → `results_arr = []`.

Semântica do `or` em Python: `[] or docs_arr` → `docs_arr`. Portanto se um provider quiser explicitamente devolver `results=[]` (não None) mas ter documentos, o fallback cai em `docs_arr`. Isso é intencional para o piloto E1B — se aparecer caso de uso legítimo pedindo "results semanticamente vazio", promover a lógica para `result.get("results") if "results" in result else docs_arr` numa iteração futura.

## Testes

**Novo:** `tests/test_fase_e1b_envelope_results.py` — 6 casos:
- `TestDocumentosViramResults::test_documentos_com_1_item_vira_results` — docs=[1 item] → results=[1 item], chave preservada
- `TestDocumentosViramResults::test_documentos_vazio_results_vazio` — docs=[] → results=[]
- `TestResultsExplicitoPreservado::test_results_e_documentos_ambos_presentes_results_ganha` — results explícito não sobrescrito
- `TestResultsExplicitoPreservado::test_results_explicito_vazio_nao_usa_documentos` — semântica documentada (comportamento do `or`)
- `TestResultsExplicitoPreservado::test_ambos_vazios` — sanidade
- `TestSegurancaEnvelope::test_envelope_nao_contem_token_authorization_basic` — token `SECRETO_XYZ_123` injetado no payload não vaza no body

**Método de mock:** `patch.object(app_mod, "get_provider", return_value=_ProviderMock(envelope))`. Zero HTTP real.

## Validações executadas

```
python3 -m py_compile app.py                                  # OK
git diff --check                                              # OK
pytest tests/test_fase_e1b_envelope_results.py -v             # 6 passed (0.50s)
pytest tests/test_fase_d_provider_por_request.py tests/test_focusnfe_http.py -v
                                                               # 91 passed (2.68s)
pytest                                                         # 189 passed (1.99s)
```

Suite completa: **189 passed** (183 anteriores + 6 novos E1B). Zero regressão. Zero HTTP real.

## Fora de escopo E1B

- Alteração em `FocusNFeProvider` — mantido como está.
- Alteração em `SefazProvider` — mantido como está.
- Autenticação/token — inalterado.
- DANFE PDF — E2.
- Sync agendado — E3.
- CT-e/NFSe/MDFe via Focus — E4.

## Push/Deploy

- Push: NÃO executado — aguardando autorização.
- Deploy: NÃO executado — aguardando autorização.

## Próximo passo

Parte 2 — MapOne: ajustar `status_sefaz` default por origem (`fiscalone_focusnfe` → `focusnfe`, senão `sefaz`).
