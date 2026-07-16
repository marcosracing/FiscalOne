# Handoff Fase 2 HTTP — FocusNFeProvider gov_fetch real

Data: 2026-07-16
Escopo: FiscalOne apenas — nenhuma alteração em MapOne/CtrlOne/LegalOne.
Commit base: `0887aa5` (Fase 2-prep).

## Objetivo

Implementar HTTP real do `FocusNFeProvider` no FiscalOne:
- `gov_fetch()` real via `GET /v2/nfes_recebidas`.
- `consultar_dfe_nsu()` delegando para `gov_fetch()`.
- `baixar_danfe()` com fluxo 302 correto (segundo GET sem Authorization).

**Testes 100% mockados via `unittest.mock.patch("requests.get")`. Zero chamada HTTP real disparada.**

## Discovery (arquivo:linha)

- `providers/focusnfe_provider.py:66-70` (Fase 2-prep) — `gov_fetch` e `consultar_dfe_nsu` retornavam `_STUB PROVIDER_NAO_IMPLEMENTADO`.
- `app.py:505` — aplicação chama `provider.gov_fetch(payload, trace_id)`. **Não** chama `consultar_dfe_nsu` (esse fica delegando por completude do contrato ABC).
- `app.py:604-617` — `_status_para_codigo` mapeava só SEFAZ/ADN e `PROVIDER_NAO_IMPLEMENTADO`. **Faltavam mapeamentos Focus** — adicionados.
- `requirements.txt:3` — `requests>=2.31.0` presente. SEFAZ e ADN usam `http.client` para SOAP mTLS; Focus é REST + JSON → adotado `requests`.
- Testes que dependiam de stub e precisaram atualização: `test_provider_contract::TestFocusNFeStub` (3 casos), `test_focusnfe_preparacao::TestStubsPreservados` (2 casos), `test_health_e_boot::test_focusnfe_gera_warning_e_501`. Nenhum teste foi removido — todos migraram para o novo contrato (`FOCUS_TOKEN_AUSENTE` no lugar de `PROVIDER_NAO_IMPLEMENTADO`).

## Arquivos alterados

**Código:**
- `providers/focusnfe_provider.py` — reescrito. Adiciona `_basic_auth_header`, `_resolve_base_url`, `_envelope_erro`, `_sanitize_focus_item`, `_dump_focus_json`, `_mapear_nfe_focus`. Implementa `gov_fetch()`, `consultar_dfe_nsu()` (delegação), `baixar_danfe()`. Preserva `EmissaoProibida`, `_masked_token`, `__init__` sem fail-fast global, `_require_token()` fail-fast local, guards em `emitir_*`, e stubs de `cancelar_*/encerrar_*/incluir_*/sync/listar_*/detalhe_*/status_sefaz`.
- `app.py:604-617` — `_status_para_codigo` recebeu 11 novos códigos Focus com mapeamento HTTP explícito.

**Testes:**
- `tests/test_focusnfe_http.py` (novo) — 51 casos, todos mockados. Cobre: helpers, mapper, `gov_fetch` (sucesso, validações, HTTP 400/401/403/429/5xx, timeout, connection error, JSON inválido, JSON não-lista, item inválido no lote), `baixar_danfe` (302 sem Authorization no segundo GET, 200 direto, 302 sem Location, timeout, download error), delegação `consultar_dfe_nsu → gov_fetch`, segurança (envelope não vaza token/Authorization, `raw_json_focus` mascara campos sensíveis).
- `tests/test_provider_contract.py` — `TestFocusNFeStub` renomeada para `TestFocusNFeSemToken`; asserções agora esperam `FOCUS_TOKEN_AUSENTE`; refatorado para usar `monkeypatch` fixture (evita vazamento de env entre testes).
- `tests/test_focusnfe_preparacao.py` — `TestStubsPreservados` renomeada para `TestSemTokenErroEstruturado`; refatorado para `monkeypatch`. `TestInitSemFailFast::test_base_url_*` atualizado para nova API (`_base_url_env` + `_base_url_for(ambiente)`); default seguro agora é `homologacao` (Fase 2-prep default era `producao` — mudança intencional).
- `tests/test_health_e_boot.py::test_focusnfe_gera_warning_e_501` renomeado para `test_focusnfe_sem_token_devolve_400_estruturado`; expectativa mudou de 501 para 400 (FOCUS_TOKEN_AUSENTE mapeia para 400 em `_status_para_codigo`).

**Docs:**
- `docs/adr/_handoff/2026-07-16-fase2-http-focusnfe.md` — este handoff.
- `docs/manual-tecnico-FiscalOne.md` — seção Fase 2 HTTP.
- `docs/acervo/FISCALONE_OPERACIONAL.md` — nota curta.

## Implementado

### `gov_fetch(payload, trace_id) -> dict`

- Entrada: `{cnpj, tipo, ambiente?, ultimo_nsu?}`. `tipo` deve ser `"nfe"` (fase atual); outros retornam `FOCUS_TIPO_NAO_SUPORTADO`.
- Auth: **HTTP Basic** com `Authorization: Basic base64(token:)` (senha vazia). NÃO Bearer.
- URL: `{base_url}/nfes_recebidas`; query: `{cnpj, versao}`. Token nunca em query string.
- Cursor: lê `X-Max-Version`; se ausente, usa maior `versao` dos itens; se ainda ausente, mantém `versao` de entrada. `cursor_tipo` sempre `"versao"`. `nsu_avancou` = comparação com entrada.
- HTTP tratamento explícito: 200/400/401/403/429 (com `Retry-After`)/500-599/outros/timeout/connection error/RequestException genérica/JSON inválido/JSON não-lista. Cada branch retorna envelope canônico com código único.
- Item inválido no lote (não-dict, sem chave, mapper lança) → entra em `erros[]` com `FOCUS_ITEM_INVALIDO`; **lote não é derrubado**.

### `consultar_dfe_nsu(cert_pem, key_pem, cnpj, nsu, ambiente, trace_id)`

Delega para `gov_fetch()`. `cert_pem`/`key_pem` são ignorados (Focus é REST + Bearer-like, não mTLS). Preserva assinatura do contrato ABC `GovProvider`.

### `baixar_danfe(chave, ambiente=None) -> dict`

- 1º GET: `{base_url}/nfes_recebidas/{chave}.pdf` COM `Authorization`, `allow_redirects=False`.
- Se 301/302/303/307/308: lê `Location`; 2º GET **SEM Authorization** (URL pré-assinada não precisa e não pode vazar token para storage de terceiros).
- Se 200 direto: usa bytes.
- Calcula `sha256`, extrai `mime` (default `application/pdf`), retorna `{ok, bytes, sha256, mime, tamanho}`.
- Erros: `DANFE_NO_LOCATION`, `DANFE_REQUEST_ERROR`, `DANFE_DOWNLOAD_ERROR`, `DANFE_HTTP_ERROR`, `DANFE_UNEXPECTED_HTTP`, `FOCUS_TOKEN_AUSENTE`, `FOCUS_BAD_REQUEST`.

### `_masked_token`, `_basic_auth_header`, `_envelope_erro`, `_sanitize_focus_item`, `_dump_focus_json`

Helpers pequenos, deterministicos, testados individualmente. `_sanitize_focus_item` mascara chaves em `_CAMPOS_SENSIVEIS = {authorization, token, password, senha, secret, api_key, apikey, credential, credentials, x-auth-token}` antes de serializar como `raw_json_focus`.

### Mapper `_mapear_nfe_focus(item, trace_id)`

Tolera variação de nomes de campo (Focus documenta `chave_nfe|chave|chNFe`, `cnpj_emitente|CNPJ_emit`, etc.). Preenche `NFeDoc` + `NFeDocOpcional`. `status_xml=COMPLETO` se `xml` presente, senão `RESUMO`. `cStat=100|101`, `xMotivo="Autorizado"|"Resumo FocusNFe"`. `import_origin="fiscalone_focusnfe"`, `parser_version="focus_v2"`. Sempre grava `raw_json_focus` sanitizado.

### `_status_para_codigo` (app.py)

Novos mapeamentos:
- `FOCUS_TOKEN_AUSENTE`, `FOCUS_BAD_REQUEST`, `FOCUS_TIPO_NAO_SUPORTADO` → **400**
- `FOCUS_AUTH_ERROR` → **401**
- `FOCUS_FORBIDDEN` → **403**
- `FOCUS_RATE_LIMIT` → **429**
- `FOCUS_TIMEOUT`, `FOCUS_UNAVAILABLE`, `FOCUS_SERVER_ERROR`, `FOCUS_HTTP_ERROR`, `FOCUS_PARSE_ERROR`, `FOCUS_SCHEMA_ERROR` → **502**

## Segurança (invariantes)

- **Token nunca em log, envelope, mensagem de erro, `raw_json_focus`.** Testes de segurança validam: `test_gov_fetch_envelope_nao_contem_authorization`, `test_gov_fetch_com_erro_nao_vaza_token`, `test_raw_json_focus_mascarara_campo_sensivel`, `test_nao_vaza_token_em_envelope_sem_token`.
- **Segundo GET DANFE nunca envia `Authorization`.** Testado explicitamente em `test_302_segundo_get_sem_authorization`: `assert "Authorization" not in segundo.kwargs["headers"]`.
- **`_log_stdout` (app.py:227-261)** tem schema JSON fixo com chaves permitidas — não loga headers nem token. Nenhuma alteração necessária.
- **`EmissaoProibida`** em `emitir_cte` / `emitir_mdfe` (defesa em profundidade — rotas do `app.py:666-719` já bloqueiam via `bloquear_emissao()`).
- **Retro-compat**: atributo de módulo `FOCUSNFE_BASE_URL` preservado.

## Fora de escopo (Fase 3 MapOne)

- Persistência XML/DANFE no MapOne.
- Atualização de cursor no CtrlOne (`dfe_distribuicao_estado`).
- Extensão do CHECK constraint de `op_fiscal_xml.import_origin` no MapOne (necessário antes de insert real com `fiscalone_focusnfe`).
- Manifestação do destinatário.
- Qualquer emissão fiscal.
- CT-e recebido (fase separada — Focus tem endpoint dedicado `/v2/ctes_recebidos`).
- Chamada HTTP real em qualquer teste automatizado.

## Testes executados

```
python3 -m py_compile app.py providers/*.py services/*.py schemas/*.py   # OK
git diff --check                                                          # OK
source .venv/bin/activate
python -m pytest tests/test_focusnfe_http.py -v            # 51 passed (0.79s)
python -m pytest                                            # 142 passed (1.28s)
```

Suite pré-existente sem regressão. Zero chamada HTTP real disparada (todos os testes usam `@patch("providers.focusnfe_provider.requests.get")`).

## Próxima fase — Fase 3 MapOne

Prep MapOne para consumir Focus como provider ativo:

1. Migration `049_expand_import_origin_focusnfe.sql` — expandir CHECK de `op_fiscal_xml.import_origin` para incluir `fiscalone_focusnfe`.
2. Migration `050_prov_fiscal_provider_ativo.sql` — nova tabela `(tenant_id, cnpj, doc_type, provider)` com UNIQUE `(tenant_id, cnpj, doc_type)`.
3. Helper `_resolve_fiscal_provider_ativo(tenant_id, cnpj, doc_type)` em `logione/services/`.
4. Interceptar 3 pontos que hoje passam `provider="sefaz"` hardcoded:
   - `app.py:5804-5809` (POST /fiscal/api/buscar-dfe)
   - `scripts/dfe_sync.py:182-183`
   - `integrations/fiscalone_client.py:161`
5. UI de cadastro de conexão Focus (fork do CTASmart: `templates/administracao/integracoes_ctasmart.html` + `app.py:1098-1279`).
6. Migration `051_op_fiscal_danfe.sql` — nova tabela `(tenant_id, chave, danfe_blob BLOB, danfe_sha256, ...)` com padrão `prov_pessoa_documento`.
7. Endpoint `/fiscal/api/danfe/<chave>` — dispara `provider.baixar_danfe()` e persiste BLOB no MapOne.

## Status

- **Push: NÃO executado** — aguardando autorização explícita.
- **Deploy: NÃO executado** — aguardando autorização explícita.
- Commit hash: a preencher após execução.
