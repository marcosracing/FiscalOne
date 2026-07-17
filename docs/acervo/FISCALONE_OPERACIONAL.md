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

- `FocusNFeProvider.gov_fetch` aceita `tipo="nfse"` (`providers/focusnfe_provider.py:340`). URL `/v2/nfses_recebidas` + `params["completa"]="1"`. Cursor `versao` reusado. CT-e / MDF-e permanecem bloqueados.
- Novo mapper `_mapear_nfse_focus` (`providers/focusnfe_provider.py:284-402`) — schema `NfseRecebida`. Sem cStat SEFAZ, sem DV DFe 44. `situacao_nfse ∈ {autorizada, cancelada, substituida}` a partir de `status ∈ {1,2,3}`. `import_origin="fiscalone_focusnfe_nfse"` (dedicado, distingue de NF-e Focus). `status_sefaz="focusnfe"`. Prestador → `emit_*`; tomador → `dest_*`.
- Novo `baixar_xml_nfse(url_xml)` (`providers/focusnfe_provider.py:901-1024`): baixa XML via URL fornecida pelo item. Padrão análogo ao `baixar_danfe` (302 → segundo GET sem Authorization).
- `gov_fetch` loop pós-mapper dispatcheia por `tipo`. NFSe com `url_xml` presente e `status=1` promove `COMPLETO`; `status=2/3` (cancelada/substituida) **não** baixa. Cap `_XML_BATCH_CAP=25` compartilhado. Falha individual não derruba batch.
- 403 body `{"codigo":"empresa_nao_habilitada"}` traduzido para código canônico `FOCUS_NFSE_NAO_HABILITADA` (ação: contato suporte Focus).
- ADN NFSe (`providers/nfse_nacional_provider.py`) **intocada**. NFSe emitida/receita fora.
- 232/232 testes verdes (+27 novos). Zero HTTP real. Zero token vazado. Sem push/deploy.
- Detalhes: `docs/adr/_handoff/2026-07-17-fase-e4c-nfse-nacional-focusnfe.md`.
