# Handoff · E4b-1A · FiscalOne · Ciência NF-e recebida via FocusNFe

Data: 2026-07-19
Fase: E4b-1A (FiscalOne-only, stateless)
Escopo: endpoint dedicado para manifestação de **Ciência da Operação**
(evento SEFAZ **210210**) de NF-e recebida via FocusNFe.

## O que muda

- Novo método no provider: `FocusNFeProvider.manifestar_nfe_recebida(chave,
  tipo="ciencia", ambiente=None, trace_id=None)`
  (`providers/focusnfe_provider.py`).
- Nova rota: `POST /fiscal/nfe/recebida/manifesto` (`app.py`).
- Novos códigos em `_status_para_codigo` (`app.py`).
- Suite de testes: `tests/test_manifesto_ciencia.py` (41 casos, todos
  mockados).

## O que NÃO muda

- FiscalOne segue **stateless**. Sem banco. Sem migration. Sem tabela
  nova. Sem dependência PG/Oracle/ATP.
- Emissão fiscal permanece **bloqueada por design** para NF-e, CT-e,
  NFS-e e MDF-e (rotas separadas retornam `403 EMISSAO_BLOQUEADA`).
- MapOne não é tocado. Fase B (dry-run/auditoria/execução) fica para
  E4b-1B.
- Rota `/fiscal/gov/fetch` **não** é reaproveitada — este é endpoint
  dedicado. Não há endpoint genérico de manifestação.

## Contrato

### Request

```
POST /fiscal/nfe/recebida/manifesto
Content-Type: application/json

{
  "chave":           "44_digitos_numericos",
  "tipo":            "ciencia",
  "ambiente":        "producao" | "homologacao",
  "focusnfe_token":  "<token>",
  "provider":        "focusnfe"   // opcional; default focusnfe
}
```

### Response OK

```
HTTP 200
{
  "ok":          true,
  "provider":    "focusnfe",
  "codigo":      "MANIFESTO_OK",
  "trace_id":    "fo-...",
  "chave":       "44_digitos",
  "tipo":        "ciencia",
  "evento":      "210210",
  "cstat":       "135",
  "xmotivo":     "Evento registrado e vinculado a NF-e",
  "protocolo":   "135260000123456",
  "http_status": 200
}
```

### Códigos de erro

| Código | HTTP | Semântica |
|---|---|---|
| `PAYLOAD_INVALIDO` | 400 | Body JSON ausente / vazio. |
| `FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO` | 400 | `tipo != "ciencia"` (confirmacao/desconhecimento/nao_realizada bloqueados). |
| `FOCUS_MANIFESTO_CHAVE_INVALIDA` | 400 | Chave sem 44 dígitos numéricos. |
| `FOCUS_MANIFESTO_PROVIDER_INVALIDO` | 400 | `provider != "focusnfe"`. |
| `FOCUS_MANIFESTO_INVALIDO` | 400 | Focus rejeitou (400). |
| `FOCUS_TOKEN_AUSENTE` | 400 | `focusnfe_token` não veio no payload nem no env. |
| `FOCUS_AUTH_ERROR` | 401 | Token inválido no Focus. |
| `FOCUS_FORBIDDEN` | 403 | Focus negou acesso. |
| `FOCUS_MANIFESTO_NAO_ENCONTRADO` | 404 | Chave desconhecida no Focus. |
| `FOCUS_MANIFESTO_CONFLITO` | 409 | Focus retornou 409 ou 422 — evento já existe ou regra SEFAZ. |
| `FOCUS_RATE_LIMIT` | 429 | 429 do Focus. |
| `FOCUS_MANIFESTO_HTTP_ERROR` | 502 | 5xx do Focus ou `RequestException`. |

## Eventos SEFAZ — mapa completo

| Evento | Tipo Focus | Fase E4b-1A |
|---|---|---|
| 210210 | `ciencia` | **liberado** neste endpoint. |
| 210200 | `confirmacao` | **bloqueado**. |
| 210220 | `desconhecimento` | **bloqueado**. |
| 210240 | `nao_realizada` | **bloqueado**. |

Bloqueio via `FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO` — request nem chega ao
Focus. Emissão fiscal (NF-e/CT-e/NFS-e/MDF-e) continua completamente
bloqueada — manifestação de destinatário **não** é emissão, mas a trava
de emissão via `bloquear_emissao()` permanece intacta em todas as rotas
`/fiscal/{nfe,cte,mdfe}` e derivadas.

## Segurança

- Log de INFO só carrega **chave mascarada** (`prefixo(6)***sufixo(4)`).
- Nunca aparecem em log, envelope ou mensagem de erro: `token`,
  `Authorization`, `Bearer`, XML, certificado, senha.
- Rota **POP** dos campos sensíveis antes de qualquer serialização:
  `focusnfe_token`, `cert_pfx_base64`, `cert_password`, `cert_cnpj`,
  `cert_valid_until`, `Authorization`, `authorization`.
- Provider descarta refs a `token` e `headers` em `finally:` após o POST.

## Testes

- `tests/test_manifesto_ciencia.py` — 41 casos, 100 % mock (nenhum POST
  real ao FocusNFe):
  - Trava `tipo` (confirmacao/desconhecimento/nao_realizada/vazio) — sem
    POST.
  - Trava `chave` (43 dígitos, letras, vazia) — sem POST.
  - Token ausente — sem POST.
  - Sucesso 200/201/202 → `MANIFESTO_OK`.
  - 400/401/403/404/409/422/429/500 → códigos mapeados.
  - `RequestException` → `FOCUS_MANIFESTO_HTTP_ERROR`.
  - Rota com `provider=sefaz` → `FOCUS_MANIFESTO_PROVIDER_INVALIDO`.
  - Rota sanitiza `cert_pfx_base64` e `cert_password` (envelope não vaza).
  - Log INFO não contém token/Authorization/XML/chave inteira.
  - Regressão: 9 rotas de emissão continuam `403 EMISSAO_BLOQUEADA`.
  - Regressão: rota nova não invoca `emitir_cte` / `emitir_mdfe`.

### Validações executadas

```
python3 -m py_compile app.py providers/focusnfe_provider.py   # OK
python3 -m pytest tests/test_manifesto_ciencia.py             # 41/41
python3 -m pytest tests/                                      # 294/294
git diff --check                                              # OK
```

> Nota: a suíte do FiscalOne é 100 % pytest — `python3 -m unittest discover
> tests` coleta 0 casos (esperado; não há `unittest.TestCase`).
> A validação canônica do repo é pytest.

## Próximos passos (E4b-1B — MapOne)

- Consumir `POST /fiscal/nfe/recebida/manifesto` a partir do MapOne com
  dry-run, auditoria e execução controlada.
- Confirmar registro do evento 210210 no CtrlOne.
- Reutilizar `trace_id` propagado ponta-a-ponta.

## Arquivos alterados

- `providers/focusnfe_provider.py` — método `manifestar_nfe_recebida`.
- `app.py` — rota `/fiscal/nfe/recebida/manifesto` + códigos
  `FOCUS_MANIFESTO_*`.
- `tests/test_manifesto_ciencia.py` — novo arquivo (41 casos).
- `docs/manual-tecnico-FiscalOne.md` — seção E4b-1A.
- `docs/adr/_handoff/2026-07-19-e4b1a-fiscalone-manifesto-ciencia.md`
  — este handoff.
