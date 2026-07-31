# DANFSe HTML — NFS-e recebida por contrato M2M seguro (2026-07-30)

**Modo:** implementação isolada; sem deploy nesta rodada (FiscalOne
será redeploy junto com o MapOne quando as duas fatias estiverem verdes).

**Endpoint FocusNFe oficial:**
`GET https://api.focusnfe.com.br/v2/nfsens_recebidas/{chave}.html`
([documentação](https://doc.focusnfe.com.br/reference/consultar_nfsen_recebida_individual_html))

## Objetivo

Expor a DANFSe HTML da NFS-e recebida por um contrato M2M seguro do
FiscalOne, para que o MapOne possa abri-la em popup autenticado do
Gerenciador Fiscal sem nunca expor o token FocusNFe ao browser.

## Fluxo

```
Browser MapOne (popup)
  → GET /fiscal/api/documentos/<espelho_id>/danfse   (MapOne, sessão)
    → POST /fiscal/nfse/recebida/danfse              (FiscalOne, M2M)
      → GET  https://api.focusnfe.com.br/v2/nfsens_recebidas/{chave}.html
```

FiscalOne é **stateless** — não persiste, não interpreta, não classifica.

## Entregas

### 1. `providers/focusnfe_provider.py::baixar_danfse_nfse`

Método novo com o **mesmo padrão** de `baixar_danfe` (PDF), adaptado
para HTML:

- URL oficial `/v2/nfsens_recebidas/{chave}.html`.
- Chave validada por allowlist `^[A-Za-z0-9._-]{1,80}$` (defesa contra
  path traversal e injeção).
- Basic Auth centralizado (`_basic_auth_header`), `Accept: text/html`.
- `allow_redirects=False`; se houver 301/302/303/307/308:
  - valida host pela allowlist já existente
    (`_xml_redirect_location_permitida`);
  - segundo GET **sem** `Authorization` (URL pré-assinada);
  - host fora da allowlist → `DANFSE_HOST_PROIBIDO`.
- Timeout limitado (`FOCUSNFE_TIMEOUT`).
- Limite defensivo de resposta: 5 MiB (`_DANFSE_MAX_BYTES`).
- MIME validado: aceita `text/html`; qualquer outro → `DANFSE_MIME_INESPERADO`.
- Corpo vazio → `DANFSE_VAZIO`.

Códigos de erro nominais (nunca expõem token/detalhe de driver):

| Código | Situação |
|---|---|
| `FOCUS_BAD_REQUEST` | chave vazia ou inválida |
| `FOCUS_TOKEN_AUSENTE` | token não configurado |
| `DANFSE_TIMEOUT` | timeout no upstream |
| `DANFSE_REQUEST_ERROR` | falha de conexão |
| `DANFSE_NAO_ENCONTRADA` | upstream 404 |
| `DANFSE_NAO_AUTORIZADA` | upstream 401 |
| `DANFSE_NO_LOCATION` | redirect sem Location |
| `DANFSE_HOST_PROIBIDO` | redirect fora da allowlist |
| `DANFSE_DOWNLOAD_ERROR` | falha no GET pré-assinado |
| `DANFSE_HTTP_ERROR` | storage != 200 |
| `DANFSE_UNEXPECTED_HTTP` | status inesperado na origem |
| `DANFSE_MIME_INESPERADO` | Content-Type != text/html |
| `DANFSE_VAZIO` | corpo vazio |
| `DANFSE_MUITO_GRANDE` | corpo excede 5 MiB |

### 2. Rota M2M `POST /fiscal/nfse/recebida/danfse`

Mesma trava M2M constant-time do `xml_por_chave` (`X-RLogix-Service-Token`).

Payload:
```json
{
  "chave":          "<identidade canônica NFS-e>",
  "provider":       "focusnfe",
  "ambiente":       "producao" | "homologacao",
  "focusnfe_token": "<segredo>"
}
```

- `focusnfe_token` é `pop()` **antes** de qualquer log/serialização.
- `provider != "focusnfe"` → 400 `PROVIDER_NAO_SUPORTADO`.
- Chave inválida (control chars, tamanho, formato) → 400 `CHAVE_INVALIDA`.
- Token FocusNFe ausente → 400 `FOCUS_TOKEN_AUSENTE`.
- Ambiente inválido → 400 `AMBIENTE_INVALIDO`.
- 404 upstream → 404; timeout → 504; demais falhas upstream → 502.
- Sucesso: `HTTP 200`, body = HTML EXATO, MIME `text/html; charset=utf-8`.
- Headers de segurança: `Cache-Control: private, no-store`,
  `X-Content-Type-Options: nosniff`.

### 3. Mapeamento de status

`_status_para_codigo_danfse` traduz códigos do provider em HTTP:

- `DANFSE_NAO_ENCONTRADA` → 404
- `DANFSE_TIMEOUT` → 504
- `FOCUS_BAD_REQUEST` / `FOCUS_TOKEN_AUSENTE` → 400
- demais → 502 (upstream ou allowlist).

## Testes

`tests/test_danfse_nfse_focus.py` — **26 provas verdes**:

- Provider (15): URL oficial, chave rejeitada em caracteres perigosos,
  302 sem Authorization no segundo GET, redirect para host proibido,
  redirect sem Location, 401/404/timeout/conexão/vazio/MIME inválido,
  token ausente, token nunca no envelope, Authorization apenas na
  primeira origem.
- Rota M2M (11): sem M2M → 401, M2M não configurado → 503, provider
  incorreto → 400, chave com control char → 400, token FocusNFe
  ausente → 400, sucesso HTML encaminhado com headers de segurança,
  Focus 404 → 404, Focus timeout → 504, Focus 500 → 502, token nunca
  aparece na resposta.

Suíte FiscalOne integral: **478 passed** (nenhuma nova falha).

## Segurança

- Token **jamais** sai do servidor: nunca em log, envelope, header,
  URL, cache ou resposta ao MapOne.
- Authorization **apenas** na primeira origem (endpoint FocusNFe).
  Redirects para storage pré-assinado nunca reencaminham Authorization.
- Allowlist de hosts para redirects (variável
  `FISCALONE_XML_REDIRECT_HOSTS`).
- Limite defensivo de 5 MiB por resposta.
- Sanitização de campos sensíveis do payload antes de qualquer log.

## Limitações

- Nenhuma NFS-e operacional real chegou ao MapOne até esta data
  (drain 2026-07-30 retornou `SEM_DOCUMENTO`). O contrato foi
  provado por testes unitários com mocks; a validação ponta a ponta
  com FocusNFe real ficará pendente até a primeira NFS-e importada.
- Ambiente `producao` é o único caminho canônico exposto ao popup do
  MapOne nesta fatia (a rota M2M aceita `homologacao` para futuras
  ferramentas administrativas).

## Referências cruzadas

- Provider `baixar_danfe` (PDF NF-e) — mesmo padrão de 302 e headers.
- ADR-0049 — Espelho canônico é a fonte de identidade da NFS-e; a
  chave só é obtida no MapOne pelo par `(tenant_id, espelho_id)`.
