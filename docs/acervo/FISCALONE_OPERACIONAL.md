# Acervo Digital — FiscalOne Operacional

## Identidade

- Produto: FiscalOne
- Papel: gateway fiscal técnico RLogix
- Porta local padrão: `127.0.0.1:5002`
- Persistência própria: não
- Certificado A1 em repouso: não

## Rotina operacional

- Desenvolvimento: `~/Documents/FiscalOne`
- VM: `/home/ubuntu/FiscalOne`
- Serviço: `fiscalone.service`
- Deploy: `scripts/deploy_fiscalone_vm.sh`
- Health: `GET /fiscal/health`

## Segurança

FiscalOne recebe o certificado A1 em trânsito por requisição, usa em memória e
descarta. Não deve gravar `.env` com certificado, PFX, PEM, senha, base64 ou XML
bruto em log.

## Documentação de suporte

- `README.md`
- `docs/manual-tecnico-FiscalOne.md`
- `docs/adr/_handoff/`

## Registro operacional — 2026-07-11

NFS-e Nacional ADN está habilitado como DFe recebido. O provider deve preservar
o tipo retornado pelo parser:

- documento NFS-e completo: `doc_type=nfse`;
- evento NFS-e: `doc_type=evento`.

Eventos não são persistidos no acervo fiscal da vertical como documentos. O
MapOne usa essa distinção para gravar apenas XML fiscal completo em
`op_fiscal_xml` e tratar eventos em `op_dfe_evento`.

## Registro operacional — 2026-07-11 b

FiscalOne foi ajustado para subir com `threaded=True` no servidor Flask
local/VM simples. O objetivo é aceitar chamadas simultâneas controladas do
agendador multiempresa do MapOne.

Limites preservados:

- sem persistência própria;
- sem certificado em repouso;
- sem emissão fiscal ativa;
- logs sem PFX, senha, base64 ou XML completo.

## Fase 2-prep — 2026-07-17 (FocusNFeProvider infra sem HTTP)

- `normalizar_nsu` agora suporta `focusnfe`/`fiscalone_focusnfe` — preserva `versao` como string, aceita int, `None`/vazio → `"0"`.
- `FocusNFeProvider` recebe `EmissaoProibida` (emitir_cte/emitir_mdfe), `_masked_token`, `__init__` com envs (`FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL`, `FOCUSNFE_TIMEOUT`), `_require_token` para Fase 2.
- Schemas expandidos: `ImportOrigin += "fiscalone_focusnfe"`; `NFeDocOpcional` inclui `versao`, `raw_json_focus`, `danfe_sha256`, `danfe_fonte`.
- `gov_fetch`/`consultar_dfe_nsu` seguem stub — Fase 2 HTTP separada.
- Nenhuma alteração em MapOne/CtrlOne/LegalOne. Sem HTTP real. Sem push. Sem deploy.

## Fase 2 HTTP — 2026-07-16 (FocusNFeProvider real, testes mockados)

- `gov_fetch()` real: `GET /v2/nfes_recebidas?cnpj=&versao=`, HTTP Basic `base64(token:)`, cursor via `X-Max-Version`.
- `consultar_dfe_nsu()` delega para `gov_fetch()`; `baixar_danfe()` com 302 sem Authorization no segundo GET.
- 11 códigos Focus (`FOCUS_*` e `DANFE_*`) mapeados para HTTP status em `app.py::_status_para_codigo`.
- Envelope canônico com `documentos`, `resumos`, `erros`, `ultimo_nsu`, `max_nsu`, `cursor_tipo=versao`, `nsu_avancou`.
- Item inválido no lote não derruba os demais.
- Segurança: token nunca em log/envelope/raw_json_focus; segundo GET DANFE sem Authorization (validado por teste).
- 142/142 testes passam. 51 novos em `test_focusnfe_http.py`. Zero HTTP real. Sem push. Sem deploy.

## Fase D — 2026-07-17 (provider e token FocusNFe por requisição)

- `/fiscal/gov/fetch` agora aceita `provider` e `focusnfe_token` no payload. Provider inválido → 400 `PROVIDER_INVALIDO`.
- `get_provider(provider_name=None, token=None)` — allowlist `{sefaz, focusnfe}`; fallback ao env `FISCAL_PROVIDER` quando parâmetro ausente.
- `FocusNFeProvider(token=None)` — precedência: injetado > `FOCUSNFE_TOKEN` env > vazio.
- Blindagem: `provider="focusnfe"` remove `cert_pfx_base64`/`cert_password`/`cert_cnpj`/`cert_valid_until` do payload antes de instanciar (defesa em profundidade contra bug/legado MapOne).
- SEFAZ 100% compatível — sem regressão. 183/183 testes passam. Zero HTTP real.
- Detalhes: `docs/adr/_handoff/2026-07-17-fase-d-fiscalone-provider-token-por-request.md`.

## Fase E1B — 2026-07-17 (results/documentos compat MapOne)

- `/fiscal/gov/fetch`: `results_arr = result.get("results") or docs_arr` (app.py:594-599).
- MapOne recebe `documentos[]` do FocusNFe também em `results[]` sem alteração no provider.
- `results[]` explícito de outros providers preservado (SEFAZ, ADN).
- 189/189 testes verdes (6 novos). Zero HTTP real. Sem push/deploy.

## Fase E4a — 2026-07-17 (mapper schema real Focus + XML por chave + fix cStat)

- Corrigido **BUG FISCAL GRAVE**: `cStat="100" if tem_xml else "101"` marcava resumo autorizado como cancelado (cStat=101). Agora: cStat=100 para `autorizada`, 101 SÓ para `cancelada`, 110 para `denegada`. Blindagem em teste explícito.
- `CNPJ_emit` sai de `documento_emitente` (nome real da doc oficial) — antes ficava sempre vazio.
- Novo método `FocusNFeProvider.baixar_xml_completo(chave, ambiente)`: `GET /v2/nfes_recebidas/{chave}.xml`, `Accept: application/xml`, timeout `min(self._timeout, 5)`, sem redirect. Códigos: `FOCUS_XML_NAO_ENCONTRADO`/`FOCUS_XML_HTTP_ERROR`/`FOCUS_XML_TIMEOUT`/`FOCUS_XML_ERRO`/`FOCUS_XML_VAZIO`.
- `gov_fetch` loop pós-mapper: para `nfe_completa=True` (e não cancelada), chama `baixar_xml_completo(chNFe)`, anexa `xml_bruto` e promove `status_xml=COMPLETO`. Cap `_XML_BATCH_CAP=25` (override via env). Excedentes/falhas viram `xml_pending=True` — falha individual não derruba batch.
- Envelope acrescido de `xmls_baixados`, `xmls_pendentes`.
- `NFeDocOpcional`: novos campos opcionais `nfe_completa`, `tipo_nfe`, `manifestacao`, `situacao_focus`, `cancelado`, `xml_pending`, `data_cancelamento`, `justificativa_cancelamento`.
- Nota `cancelada` NÃO baixa XML — E4b (evento de cancelamento).
- 205/205 testes verdes (+17 novos). Zero HTTP real. Zero token vazado. Sem push/deploy.
- Detalhes: `docs/adr/_handoff/2026-07-17-fase-e4a-mapper-schema-real-focus.md`.

## Fase E4c — 2026-07-17 (NFSe Nacional recebidas via FocusNFe)

- `FocusNFeProvider.gov_fetch` aceita `tipo="nfse"` (`providers/focusnfe_provider.py:340`). URL `/v2/nfsens_recebidas` + `params["completa"]="1"`. Cursor `versao` reusado. CT-e / MDF-e permanecem bloqueados.
- Novo mapper `_mapear_nfse_focus` (`providers/focusnfe_provider.py:284-402`) — schema `NfseRecebida`. Sem cStat SEFAZ, sem DV DFe 44. `situacao_nfse ∈ {autorizada, cancelada, substituida}` a partir de `status ∈ {1,2,3}`. `import_origin="fiscalone_focusnfe_nfse"` (dedicado, distingue de NF-e Focus). `status_sefaz="focusnfe"`. Prestador → `emit_*`; tomador → `dest_*`.
- Novo `baixar_xml_nfse(url_xml)` (`providers/focusnfe_provider.py:901-1024`): baixa XML via URL fornecida pelo item. Padrão análogo ao `baixar_danfe` (302 → segundo GET sem Authorization).
- `gov_fetch` loop pós-mapper dispatcheia por `tipo`. NFSe com `url_xml` presente e `status=1` promove `COMPLETO`; `status=2/3` (cancelada/substituida) **não** baixa. Cap `_XML_BATCH_CAP=25` compartilhado. Falha individual não derruba batch.
- 403 body `{"codigo":"empresa_nao_habilitada"}` traduzido para código canônico `FOCUS_NFSE_NAO_HABILITADA` (ação: contato suporte Focus).
- ADN NFSe (`providers/nfse_nacional_provider.py`) **intocada**. NFSe emitida/receita fora.
- 232/232 testes verdes (+27 novos). Zero HTTP real. Zero token vazado. Sem push/deploy.
- Detalhes: `docs/adr/_handoff/2026-07-17-fase-e4c-nfse-nacional-focusnfe.md`.

## Fix — 2026-07-18 (NFSe FocusNFe · `servicos` como lista ou dict)

- Bug: `_mapear_nfse_focus` tratava `item["servicos"]` só como dict; quando FocusNFe entregava lista (schema oficial `NfseRecebida`), o mapper descartava silenciosamente `valor_servicos`, `valor_iss`, `valor_liquido`, `iss_retido`, `discriminacao`, `item_lista_servico`, `codigo_cnae`.
- Fix: helpers privados `_normalizar_servicos_nfse` (dict/list/None → dict canônico com `Decimal` para somas monetárias, `" | "` como separador de discriminação, OR para `iss_retido` entre itens) e `_normalizar_iss_retido_nfse` (bool/int/float/str; aceita `"true"/"1"/"sim"/"s"` e string numérica > 0). Ambos em `providers/focusnfe_provider.py:284-408`.
- Mapper agora chama os helpers; `iss_retido` no doc virou `bool` (antes vinha string "False" truthy). Novos campos `item_lista_servico` e `codigo_cnae` emitidos no doc final.
- 253/253 testes verdes (+21 novos: T1..T11 + variantes de normalização). `_mapear_nfe_focus` intocado (regressão T11). Zero HTTP real. Sem push/deploy.
- Detalhes: `docs/adr/_handoff/2026-07-18-fix-nfse-focusnfe-servicos-lista.md`.

## Fix — 2026-07-22 (NFSe FocusNFe · rota `nfsens_recebidas`)

- Sintoma: MapOne chamava FiscalOne para `doc_type=nfse`, provider
  `focusnfe`, ambiente produção, mas FiscalOne retornava
  `FOCUS_HTTP_ERROR` por HTTP 404.
- Causa: rota de listagem NFSe Nacional recebida estava como
  `/v2/nfses_recebidas`; o endpoint correto usado pelo contrato atual é
  `/v2/nfsens_recebidas`.
- Fix: `FocusNFeProvider.gov_fetch(tipo="nfse")` usa
  `/v2/nfsens_recebidas` mantendo `cnpj`, `versao` e `completa="1"`.
- NF-e, manifestação de Ciência, ADN NFSe e emissão fiscal permanecem
  intocados.
- Detalhes: `docs/adr/_handoff/2026-07-22-fix-nfse-focusnfe-rota-nfsens.md`.

## Fix — 2026-07-24 (FocusNFe · paginacao segura e cursor v2)

- Sintoma: MapOne perdia documentos NF-e/NFSe recebidos via FocusNFe
  em lotes maiores que o cap (default 25). O cursor `X-Max-Version`
  era propagado como `ultimo_nsu` mesmo com XMLs pendentes; a proxima
  consulta pulava o pendente.
- Causa 1: `gov_fetch` propagava `X-Max-Version` sem descontar itens
  com `xml_pending=True` ou erro de mapper.
- Causa 2: erros tecnicos FocusNFe (`FOCUS_TIMEOUT`,
  `FOCUS_HTTP_ERROR`, etc) nao estavam em `_CODIGO_TECNICO_ERRO` do
  classificador em `app.py` — caiam no fallback SEM_DOCUMENTO e o
  consumidor avancava cursor apesar da falha.
- Causa 3: NFSe dependia exclusivamente de `url_xml`; sem `url_xml`
  ou com falha, o item ia para `xml_pending` mesmo com endpoint
  oficial `/v2/nfsens_recebidas/{chave}.xml` disponivel.
- Fix: `gov_fetch` calcula `cursor_seguro` que nunca ultrapassa a
  menor versao com pendencia OU erro de mapper. Retorna tambem
  `versao_entrada`, `versao_pagina`, `total_count`,
  `quantidade_retornada`, `has_more`, `menor_versao_pendente_ou_erro`,
  `xmls_baixados`, `xmls_pendentes`. `ultimo_nsu`/`max_nsu` legados
  apontam para `cursor_seguro`.
- Novo metodo `baixar_xml_nfse_por_chave(chave, ambiente)`: fallback
  oficial via `GET /v2/nfsens_recebidas/{chave}.xml`. Comentario/
  docstring que afirmava "nao pertence ao contrato oficial" foi
  corrigido.
- Mapper de erro preserva `versao` e `chave` (quando derivaveis do
  item bruto), permitindo o cursor seguro travar exatamente antes do
  gap.
- Classificador `_classificar_acao_gov_fetch` (app.py) agora trata os
  13 codigos FOCUS_* como `ERRO` (`nsu_avancou=False`).
- Cap de XML (`FOCUSNFE_XML_BATCH_CAP`) segue como protecao
  operacional, mas nao autoriza descarte — excedentes viram pendencia
  e o cursor seguro fica na versao anterior.
- NF-e e ADN NFSe intocados. Emissao fiscal intocada. Manifestacao
  de Ciencia intocada.
- 313/313 testes verdes (+18 novos em
  `tests/test_focusnfe_safe_cursor.py`; +4 novos e 3 adaptados em
  `tests/test_focusnfe_nfse_e4c.py` para o fallback oficial). Zero
  chamada HTTP real. Zero token vazado.
- Detalhes (MapOne):
  `docs/adr/_handoff/2026-07-24-focusnfe-safe-cursor.md`.

## Fix — 2026-07-24 rev.2 (correcao pos-revisao Codex)

- **Defeito A** — mapper sem versao: `versoes_falha_local` ignorava
  itens invalidos sem versao extraivel; cursor podia avancar. Fix:
  novo `erros_sem_versao` + `gap_sem_versao=True` no envelope; quando
  ativo, `cursor_seguro=versao_entrada`, `has_more=True`,
  `nsu_avancou=False`. Prevalece sobre erros com versao.
- **Sanitizacao de mensagem** — `erro` do item invalido nao contem
  mais o texto arbitrario da excecao (`{exc}`). Agora: apenas
  `"mapper falhou ({type(exc).__name__})"`.
- **CNPJ real** — removidos dos testes novos e substituidos por
  sinteticos reservados. Novo teste
  `test_cnpj_real_removido_dos_novos_testes` bloqueia reintroducao.
- 317/317 testes verdes (+4 novos em
  `tests/test_focusnfe_safe_cursor.py`). Zero HTTP real. Zero token
  ou CNPJ real vazado.
- Detalhes (MapOne):
  `docs/adr/_handoff/2026-07-24-focusnfe-safe-cursor.md`.

## Fix — 2026-07-24 rev.3 (bloqueador final · validacao estrita de versao)

- **Defeito:** mappers NF-e/NFS-e normalizavam `versao` invalida para
  `0` e devolviam documento aparentemente valido; cursor podia
  avancar ate `X-Max-Version`.
- **Fix:** novo helper `_versao_focus_valida(raw)` — aceita apenas
  `int > 0` (nao bool) ou `str` puramente numerica com valor `> 0`.
  Bail-out **pre-mapper** em `gov_fetch` quando versao bruta e'
  invalida: item vira `FOCUS_ITEM_VERSAO_INVALIDA`, nao entra em
  `documentos[]`, nao dispara GET individual de XML, contribui para
  `gap_sem_versao=True` e trava `cursor_seguro` em `versao_entrada`.
  Validacao pos-mapper mantida como rede de seguranca.
- **Definicao de versao FocusNFe valida:** inteiro (nao bool) > 0
  ou string puramente numerica (`.isdigit()`) com valor > 0 apos
  `strip()`. Rejeita `None`, `""`, `0`, `"0"`, negativos, bool,
  float/decimal, texto nao conversivel, lista, dict, tuple.
- **Codigo canonico:** `FOCUS_ITEM_VERSAO_INVALIDA` (novo). Mensagem
  sanitizada `"item com versao FocusNFe invalida"` — sem valor bruto.
  `FOCUS_ITEM_INVALIDO` (excecao pos-mapper) permanece valido.
- **Recuperacao operacional:** a proxima consulta com `versao=
  cursor_seguro` (= `versao_entrada`) devolve os mesmos itens; se o
  Focus corrigir o item, ele passa a ser contabilizado.
- 382/382 testes verdes (+65 vs. rev.2). Zero HTTP real. Zero CNPJ
  real, XML real, chave real ou token vazado.
- MapOne intocado nesta rev.3 (o consumo de `gap_sem_versao` ja foi
  implementado na rev.2 e continua correto).
- Detalhes (MapOne):
  `docs/adr/_handoff/2026-07-24-focusnfe-safe-cursor.md`.
