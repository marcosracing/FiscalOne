# Handoff Fase E4c — FiscalOne: NFSe Nacional Recebidas via FocusNFe

Data: 2026-07-17
Base: ADR-0043 · Commit anterior: `43a9cf2` (E4a mapper NF-e).
Discovery: relatório E4c (rota `/v2/nfses_recebidas`; schema `NfseRecebida`
confirmado na doc oficial FocusNFe).

## Objetivo

Estender `FocusNFeProvider` para atender **NFSe Nacional recebida**
(tenant = tomador). NFSe emitida/receita fica fora desta fase (ADN NFSe
não é tocada).

## Alterações

### A1 · `providers/focusnfe_provider.py:340-352` — validação por tipo

Antes:
```python
if tipo != "nfe":
    return _envelope_erro(..., "FOCUS_TIPO_NAO_SUPORTADO", ...)
```

Depois:
```python
if tipo not in ("nfe", "nfse"):
    return _envelope_erro(..., "FOCUS_TIPO_NAO_SUPORTADO",
        "FocusNFe suporta apenas tipo='nfe' ou 'nfse'.", ...)
```

CT-e / MDF-e continuam bloqueados (delegar SEFAZ/outros).

### A1b · URL + `completa=1` (`focusnfe_provider.py:365-378`)

- `tipo="nfe"` → `/v2/nfes_recebidas` (rota preservada).
- `tipo="nfse"` → `/v2/nfses_recebidas` + `params["completa"]="1"`.

Cursor `versao` incremental (X-Max-Version) reusado — comum aos dois.

### A2 · Novo `_mapear_nfse_focus` (`focusnfe_provider.py:284-402`)

Mapper dedicado ao schema oficial `NfseRecebida`. Contrato distinto do
NF-e:

- **Sem cStat SEFAZ** — NFSe não tem cStat 100/101/110. Emitido apenas
  `situacao_nfse = autorizada | cancelada | substituida`.
- **Sem DV DFe 44** — `chave` NFSe é opaca; `validar_chave_dfe` **não**
  é chamada.
- **Prestador → `emit_*`** (fornecedor). **Tomador → `dest_*`** (tenant
  nesta fase). Campos: `emit_cnpj`, `emit_doc_tipo` (cnpj|cpf),
  `emit_nome`, `emit_ie` (inscrição municipal), `dest_cnpj`,
  `dest_doc_tipo`, `dest_nome`.
- `servicos.valor_servicos` → `valor_total`; `servicos.valor_iss` →
  `valor_iss`; `iss_retido`, `valor_liquido`, `discriminacao`
  emitidos com nome canônico.
- `status` (int Focus): 1 autorizada → `cancelado=0, substituido=0`;
  2 → `cancelado=1`; 3 → `substituido=1`.
- `import_origin = "fiscalone_focusnfe_nfse"` — string dedicada,
  distinta de `fiscalone_focusnfe` (que fica com NF-e). MapOne precisa
  aceitar (migration + allowlist Python — parte B).
- `status_sefaz = "focusnfe"` (rastreabilidade no grid do MapOne).
- `parser_version = "focus_nfse_v1"`.
- `raw_json_focus` sanitizado (mesmo padrão do NF-e; `authorization`
  mascarado).

### A3 · `baixar_xml_nfse(url_xml)` (`focusnfe_provider.py:901-1024`)

Novo método:
- Endpoint: URL fornecida pelo item Focus em `url_xml` (não é rota
  construída pelo provider).
- Headers: `Authorization: Basic ...` + `Accept: application/xml`.
- Timeout curto: `min(self._timeout, 5)`.
- `allow_redirects=False`. Se receber 3xx com `Location`, faz **segundo
  GET SEM Authorization** (URL pré-assinada — mesmo padrão do
  `baixar_danfe`).
- Códigos: `FOCUS_BAD_REQUEST`, `FOCUS_TOKEN_AUSENTE`,
  `FOCUS_XML_NAO_ENCONTRADO`, `FOCUS_XML_HTTP_ERROR`,
  `FOCUS_XML_NO_LOCATION`, `FOCUS_XML_TIMEOUT`, `FOCUS_XML_ERRO`,
  `FOCUS_XML_VAZIO`.
- `finally: del token; del headers_auth`.

### A3b · Integração `gov_fetch` (`focusnfe_provider.py:604-676`)

Loop pós-mapper agora dispatcheia por `tipo`:

- **NFSe** com `url_xml` presente e status=1: chama `baixar_xml_nfse`;
  sucesso promove `status_xml="COMPLETO"` + `xml_bruto` + hash.
- **NFSe** sem `url_xml` ou status ∈ {2,3}: permanece `RESUMO`, sem
  chamada HTTP.
- Cancelada/substituída **não** baixa XML.
- Cap `_XML_BATCH_CAP=25` compartilhado com NF-e — excedentes viram
  RESUMO + `xml_pending=True`.
- Falha individual **nunca** derruba batch — item vira RESUMO+pending
  e loop continua.

### A4 · `empresa_nao_habilitada` (`focusnfe_provider.py:543-570`)

Detecção fina no 403 body: se o JSON retornar
`{"codigo":"empresa_nao_habilitada"}`, envelope canônico devolve:

```json
{"ok": false, "codigo": "FOCUS_NFSE_NAO_HABILITADA",
 "erro": "Empresa nao habilitada no FocusNFe para NFSe Nacional. Contate o suporte Focus para habilitar o CNPJ antes de acionar buscas."}
```

Sem eco de payload/token. Outros 403 (sem esse `codigo` no body, ou
body não-JSON) continuam como `FOCUS_FORBIDDEN` genérico. Ação
operacional: contato Focus (não retry).

## Testes

**Novo:** `tests/test_focusnfe_nfse_e4c.py` — 27 casos:

| Suite | # |
|---|---|
| `TestMapperNfse` — status/campos/CPF/chave ausente | 7 |
| `TestGovFetchTipoNfse` — URL, params, regressão NF-e, CT-e bloqueado, item inválido | 4 |
| `TestGovFetchNfseXmlUrl` — url_xml sucesso/404/timeout/ausente/cancelada+substituída | 5 |
| `TestBaixarXmlNfse` — 200/302 sem auth/404/timeout/url vazia/sem token/authorization não vaza | 7 |
| `TestEmpresaNaoHabilitada` — código dedicado/genérico/body não JSON | 3 |
| `TestSegurancaEnvelopeNfse` — token não vaza no envelope | 1 |

Regressão NF-e (E4a): `tipo=nfe` continua usando `/v2/nfes_recebidas`
sem `completa`; `_mapear_nfe_focus` inalterado.

## Validações

```
python3 -m py_compile app.py providers/focusnfe_provider.py        # OK
git diff --check                                                    # OK
.venv/bin/pytest tests/test_focusnfe_nfse_e4c.py -v                 # 27 passed
.venv/bin/pytest                                                    # 232 passed (0 regressão)
```

Anterior: 205 (E4a). Agora: **232** (+27 novos, 0 removidos). Zero HTTP
real. Zero token vazado.

## Pendências obrigatórias

- **Namespace NFSe Nacional** — o XML final vem via `url_xml`; parser
  MapOne precisa confirmar o namespace do XML real. Nesta fase, o
  FiscalOne apenas devolve `xml_bruto` como string. **Ficará para o
  piloto** confirmar via primeiro XML real recebido.
- **`url_xml` comportamento em produção** — segundo GET sem
  Authorization (302 → pré-assinado) baseado em padrão análogo do
  DANFE. Se em produção o Focus decidir devolver 200 direto sem
  redirect, o método já cobre esse caso (branch `elif status_code ==
  200`). Se aparecer status novo (ex.: 401 no pré-assinado por URL
  expirada), avaliar retry/refresh — fora do escopo E4c.
- **ADN NFSe** — 100% intocada. Continua como provider legado.
- **NFSe emitida/receita** — fora. Provider FocusNFe atual só cobre
  recebimento.

## Fora de escopo E4c

- Manifestação NFSe (não existe no Focus).
- Cancelamento programado / retenções avançadas.
- Contas a pagar / FATURAS.
- CT-e, ABRASF municipal legado, SPED.

## Push/Deploy

- Commit: pendente (junto com este handoff).
- Push: **NÃO executado** — aguardando autorização.
- Deploy: **NÃO executado** — aguardando autorização.
- **Ordem de deploy obrigatória**: FiscalOne primeiro (mapper novo →
  campos `import_origin=fiscalone_focusnfe_nfse`); MapOne depois
  (migration 051 + allowlist + consumidor).

## Próximo passo

Deploy conjunto com MapOne E4c (migration + parser + persistência).
Piloto operacional: 1 tenant + 1 CNPJ habilitado no Focus para NFSe
Nacional; disparar busca manual `doc_type=nfse` no Gerenciador Fiscal;
conferir que resposta vem com `documentos[]` e envelope inclui
`xmls_baixados`/`xmls_pendentes` para NFSe.
