# Handoff Fase E4a — FiscalOne: mapper schema real + XML por chave + correção fiscal cStat

Data: 2026-07-17
Base: ADR-0043 · Commit anterior: `5caf23a` (E1B `results=documentos`).
Discovery: relatório E4a (mapper `_mapear_nfe_focus` vs doc oficial FocusNFe validado).

## Objetivo

Alinhar `providers/focusnfe_provider.py` com o schema real da API oficial
FocusNFe (`NfeRecebidaResumo`), corrigir bug fiscal grave no mapeamento
de cStat, e implementar busca de XML completo via endpoint separado
(`GET /v2/nfes_recebidas/{chave}.xml`).

## Bugs eliminados

### 1. BUG FISCAL GRAVE — cStat=101 para resumo autorizado

**Antes** (`focusnfe_provider.py:212`):
```python
"cStat": "100" if tem_xml else "101"
```
`tem_xml` era `bool(_get_str(item, "xml", ...))` mas **a Focus nunca
retorna `xml` na listagem** (`/v2/nfes_recebidas`). Consequência: todo
resumo virava `cStat=101` — que na tabela SEFAZ significa
**"Cancelamento de NF-e homologado"**. Ou seja, o mapper classificava
notas autorizadas como canceladas na persistência do MapOne.

**Depois:** `cStat=100` para autorizada (com ou sem XML). Distinção
COMPLETO/RESUMO fica **exclusivamente** em `status_xml`. `cStat=101`
só quando `situacao == "cancelada"`; `cStat=110` quando `denegada`.

Teste de blindagem: `test_autorizada_sem_xml_blindagem_bug_fiscal`
com assert explícito `d["cStat"] != "101"`.

### 2. CNPJ_emit sempre vazio — nome de campo errado

**Antes:** mapper buscava `cnpj_emitente` / `CNPJ_emit`.
**Real:** doc oficial diz `documento_emitente`.
**Depois:** `_get_str(item, "documento_emitente", "cnpj_emitente", "CNPJ_emit")`
— nome real primeiro; fallbacks preservados para compat.

### 3. Fluxo COMPLETO/RESUMO nunca disparava XML

**Antes:** `_mapear_nfe_focus` procurava `xml` no resumo (inexistente)
e nunca chamava endpoint separado — **tudo virava RESUMO permanente**.
**Depois:** mapper deixa `status_xml="RESUMO"` como default; `gov_fetch`
busca XML via `baixar_xml_completo(chave)` para itens com
`nfe_completa=True` e promove a `COMPLETO`.

## Alterações

### A1-A2 · `providers/focusnfe_provider.py:171-227` — mapper reescrito

Novos campos lidos do resumo Focus: `nfe_completa`, `situacao`,
`tipo_nfe`, `manifestacao_destinatario`, `data_cancelamento`,
`justificativa_cancelamento`. `documento_emitente` como primeira opção
para `CNPJ_emit`.

Novos campos emitidos no doc: `nfe_completa`, `tipo_nfe`, `manifestacao`,
`situacao_focus`, `cancelado`, `data_cancelamento`/`justificativa_cancelamento`
(condicionais).

Regra cStat/xMotivo por `situacao`:

| situacao   | cStat | xMotivo                  | cancelado |
|------------|-------|--------------------------|-----------|
| autorizada | 100   | "Autorizado" (se COMPLETO), senão "Resumo FocusNFe" | 0 |
| cancelada  | 101   | "Cancelamento homologado" | 1 |
| denegada   | 110   | "Uso denegado"            | 0 |
| vazio      | 100   | "Resumo FocusNFe"         | 0 |

### A3 · `providers/focusnfe_provider.py:591-681` — `baixar_xml_completo`

Novo método:
- Endpoint: `GET {base_url}/v2/nfes_recebidas/{chave}.xml`
- Headers: `Authorization: Basic ...` + `Accept: application/xml`
- Timeout curto: `min(self._timeout, 5)` — evita travar batch.
- `allow_redirects=False` (diferente do DANFE — Focus entrega XML direto).
- Retorno OK: `{ok, provider, xml_bruto, xml_hash_sha256, tamanho}`.
- Códigos de erro: `FOCUS_BAD_REQUEST`, `FOCUS_TOKEN_AUSENTE`,
  `FOCUS_XML_NAO_ENCONTRADO` (404), `FOCUS_XML_HTTP_ERROR`,
  `FOCUS_XML_TIMEOUT`, `FOCUS_XML_ERRO`, `FOCUS_XML_VAZIO`.
- `finally: del token; del headers_auth` — defesa em profundidade.

### A4 · `providers/focusnfe_provider.py` — integração no `gov_fetch`

Módulo-level: `_XML_BATCH_CAP = int(os.environ.get("FOCUSNFE_XML_BATCH_CAP", "25"))`
(l. ~152, com fallback defensivo).

Loop pós-mapper (após l. 434):
```python
for doc in documentos:
    if doc.get("cancelado") == 1:
        continue                        # cancelada NÃO baixa XML nesta fase (E4b)
    if not doc.get("nfe_completa"):
        continue                        # RESUMO (correto — Focus ainda não tem XML)
    if xml_baixados >= _XML_BATCH_CAP:
        doc["xml_pending"] = True       # segunda passada (E4a-2)
        continue
    res = self.baixar_xml_completo(doc["chNFe"], ambiente)
    if res.get("ok"):
        doc["xml_bruto"]       = res["xml_bruto"]
        doc["xml_hash_sha256"] = res["xml_hash_sha256"]
        doc["status_xml"]      = "COMPLETO"
        doc["xMotivo"]         = "Autorizado"
        xml_baixados += 1
    else:
        doc["xml_pending"] = True       # 1 falha não derruba batch
```

Envelope acrescido de `xmls_baixados` e `xmls_pendentes` (telemetria
operacional). Uma falha individual (timeout, 404, erro rede) **nunca**
derruba o batch — item vira RESUMO com `xml_pending=True`.

### A5 · `schemas/nfe_schema.py:50-65` — `NFeDocOpcional` expandido

Novos campos opcionais (`total=False`): `nfe_completa`, `tipo_nfe`,
`manifestacao`, `situacao_focus`, `cancelado`, `xml_pending`,
`data_cancelamento`, `justificativa_cancelamento`. Zero campo obrigatório
adicionado — consumidores rígidos não quebram.

### A6 · testes

**`tests/test_focusnfe_http.py`** — 17 testes novos:

`TestMapper` (5 novos + 2 renomeados):
- `test_payload_real_doc_focus` — schema real da Focus, CNPJ_emit ← documento_emitente
- `test_autorizada_sem_xml_blindagem_bug_fiscal` — assert `cStat != "101"`
- `test_situacao_vazia_default_autorizada`
- `test_situacao_cancelada` — cStat=101, cancelado=1, data/just preservadas
- `test_situacao_denegada` — cStat=110

`TestBaixarXmlCompleto` (7):
- 200/404/timeout/erro genérico/chave vazia/sem token/Authorization não vaza

`TestGovFetchComXml` (6):
- `nfe_completa=True` + XML 200 → COMPLETO
- `nfe_completa=True` + XML 404 → RESUMO+xml_pending (cStat continua 100)
- Timeout em 1 XML não derruba batch — 2º sucede
- Cap 25 com 30 docs `nfe_completa=True` → 25 COMPLETO, 5 pending
- `situacao=cancelada` (mesmo com `nfe_completa=True`) NÃO baixa XML
- `nfe_completa=False` NÃO chama endpoint

`TestGovFetchSucesso::test_200_com_3_docs` atualizado — usa payload
real (`situacao=autorizada`, sem `xml`); todos ficam RESUMO com cStat=100.

## Validações

```
python3 -m py_compile app.py providers/focusnfe_provider.py schemas/nfe_schema.py  # OK
git diff --check                                                                    # OK
.venv/bin/pytest tests/test_focusnfe_http.py -v                                     # 80 passed
.venv/bin/pytest                                                                    # 205 passed (0 regressão)
```

Anterior: 189 (E1B). Agora: **205** (+16 novos, 0 removidos). Zero HTTP real. Zero token vazado.

## Pendência E4b — XML/evento de cancelamento

Nesta fase, **notas com `situacao=cancelada` não baixam XML**. Motivação:
- Cancelamento tem estrutura de dados diferente (evento CC de cancelamento
  na SEFAZ) que a Focus expõe como `data_cancelamento` +
  `justificativa_cancelamento` no resumo — já capturados.
- XML de cancelamento requer endpoint distinto (a definir com base na doc
  oficial em E4b).
- Persistir XML puro de nota autorizada de cancelamento ainda depende de
  decidir o formato canônico no MapOne.

Consequência: `data_cancelamento` e `justificativa_cancelamento` já vão
no doc mapeado (persistíveis pelo MapOne se o schema `op_fiscal_xml`
tiver coluna correspondente).

## Cap `_XML_BATCH_CAP` — decisão de dimensionamento

25 XMLs × timeout 5s = ~2min pior caso por batch. Se rate-limit real da
Focus impuser 429, `gov_fetch` já retorna `FOCUS_RATE_LIMIT` com
`cooldown_recomendado_seg`. Ajuste operacional via env
`FOCUSNFE_XML_BATCH_CAP`. Segunda passada de `xml_pending=true` fica
para **E4a-2** (fora desta fase).

## Fora de escopo E4a

- **E4b** — manifestação (POST `/v2/nfes_recebidas/{chave}/manifesto`) e
  XML/evento de cancelamento.
- **E4c** — NFS-e Nacional recebidas via Focus (hoje só NF-e).
- **E4a-2** — segunda passada para itens `xml_pending=True`.
- **CtrlOne, rlogix, RLogix_shared** — nenhum toque.

## Push/Deploy

- Commit: pendente (junto com este handoff).
- Push: **NÃO executado** — aguardando autorização.
- Deploy: **NÃO executado** — aguardando autorização.

## Próximo passo

Deploy conjunto com MapOne E4a (persistência `cancelado`). Ordem
recomendada: FiscalOne primeiro (mapper novo → doc contém `cancelado`),
MapOne depois (consome `cancelado` no INSERT).
