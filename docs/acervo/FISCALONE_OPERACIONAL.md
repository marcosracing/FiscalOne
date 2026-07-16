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
