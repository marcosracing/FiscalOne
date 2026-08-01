# FocusNFe · NFS-e como Espelho canônico próprio (2026-07-31 R2)

**Data:** 2026-07-31 (rodada R2 após a de mesmo dia sobre layout DPS
Nacional)

**Vínculos:** ADR-0045, ADR-0046, ADR-0049 (retificado); handoffs
`2026-07-31-focusnfe-nfse-dps-nacional.md`,
`2026-07-31-focusnfe-nfse-contrato-oficial.md`; retificação formal do
ADR-0049 em `RLogix_shared/adr/`.

**Modo:** implementação, testes, docs, commit, push e deploy
autorizados.

## Retificação de premissa (FATO)

A premissa "resumo de NF-e vira XML completo" — herdada do provider
NF-e SEFAZ — foi aplicada indevidamente à NFS-e. Consequência real na
execução operacional: 100/98 NFS-e do provider FocusNFe classificadas
como `RESUMO`, zero custódias e zero Espelhos criados, 198 identidades
em `PENDENTE_RECUPERACAO` e recuperação individual respondeu
`FOCUS_XML_RATE_LIMIT` mesmo com M2M restaurado.

**Contrato definitivo:**

- NF-e continua no contrato próprio (resumo pode exigir XML completo).
- NFS-e **não** possui "resumo de NF-e" nem depende de XML completo
  para existir.
- O **payload da listagem `/v2/nfsens_recebidas`** é a fonte de entrada
  do Provider NFS-e FocusNFe.
- Esse payload gera diretamente um **`EspelhoNFSe` canônico** e
  persistente.
- XML de NFS-e é **auxiliar** e não bloqueia persistência, cursor,
  classificação ou grid.
- **DANFSe HTML** é a representação visual — sob demanda por
  `baixar_xml_nfse_por_chave` no endpoint individual, nunca no loop.
- NFS-e **nunca** passa por `is_dfe_resumo()` nem pela fila de
  recuperação de XML.
- **NF-e e seus cursores permanecem integralmente isolados e
  intocados.**

## Correções aplicadas

### `providers/focusnfe_provider.py::_mapear_nfse_focus`

- `status_xml` passou de `"RESUMO"` para **`"ESPELHO_DISPONIVEL"`**
  no dict devolvido (sinaliza que o consumidor pode persistir o
  Espelho imediatamente).
- `xml_pending` passou a ser `False` explícito para NFS-e mapeada.
- Situação desconhecida continua `fail-closed` (`ValueError`).
- DTO preserva `_layout_focus` (`"oficial"` | `"legacy"` |
  `"dps_nacional"`).

### `providers/focusnfe_provider.py::gov_fetch` (loop XML)

- Para `tipo=="nfse"`: o loop **não faz nada** — nenhuma chamada a
  `baixar_xml_nfse`, nenhum uso de `_XML_BATCH_CAP`, nenhum
  `xml_pending`. `xmls_baixados=0`, `xmls_pendentes=0`.
- Cancelada/substituída continua sendo Espelho canônico com flag
  explícita (`cancelado`/`substituido`), não "evento sem espelho".
- NF-e (`tipo=="nfe"`): comportamento anterior preservado
  (`baixar_xml_completo` sob `nfe_completa=True`; cap 25; cancelada
  NF-e não baixa XML — E4b).

### `providers/focusnfe_provider.py::baixar_xml_nfse_por_chave`

- Preservado. Continua acessível para **DANFSe sob demanda** pelo
  endpoint individual — não é chamado no loop de importação.

## Isolamento NF-e × NFS-e (FATO)

- Endpoints separados (`/v2/nfes_recebidas` × `/v2/nfsens_recebidas`).
- Mappers separados (`_mapear_nfe_focus` × `_mapear_nfse_focus`).
- Cursor `versao` por doc_type/provider/ambiente/tenant.
- Guard-rail no MapOne (`is_dfe_resumo`): NFS-e retorna sempre
  `False`.
- Testes estruturais provam que nenhuma execução NFS-e chama endpoint
  NF-e e vice-versa (`test_21_execucao_nfse_nao_toca_endpoint_nfe`,
  `test_22_execucao_nfe_nao_toca_endpoint_nfse`).

## Testes

- **Testes novos e reescritos** (FiscalOne):
  - `test_default_status_xml_espelho_disponivel` (mapper isolado
    devolve `ESPELHO_DISPONIVEL` + `xml_pending=False`).
  - `TestNfseSemRecuperacaoXmlNoLote`:
    - importação não chama endpoint individual XML;
    - `url_xml` presente ou ausente não muda comportamento;
    - cancelada/substituída continuam `ESPELHO_DISPONIVEL`.
  - `TestNfseComoFonteCanonicaPropria`: cursor avança até
    X-Max-Version pois ausência de XML NÃO é pendência.
- **Regressão focada Focus provider**: 250/250 (após ajuste dos 11
  testes legados).
- **Suíte integral FiscalOne**: 520/520 ✓ (zero regressão).
- **Suíte integral MapOne**: 1303 passed / 65 skipped, 2 falhas
  baseline conhecidas.
- **Gate ADR-0046**: MapOne 2/2 ✓, CtrlOne (rlogix) 2/2 ✓.

## Deploy

- `scripts/deploy_fiscalone_vm.sh` **corrigido** (ver handoff MapOne
  `2026-07-31-nfse-espelho-do-payload.md`): rsync já exclui `.env`;
  script **não** recria `.env` nem sobrescreve chaves; valida presença
  de `FISCALONE_M2M_TOKEN` e `FISCALONE_AMBIENTE` sem exibir valores;
  fingerprint SHA-256[:16] impresso para rastreabilidade; `chmod 600`
  garantido. Falha fechado se `.env` ausente.
- `systemd` unit ganhou `EnvironmentFile=/home/ubuntu/FiscalOne/.env`.

## Não escopos

- **NF-e** não foi alterada (nenhuma linha do mapper NF-e nem do
  fluxo NF-e do `gov_fetch`).
- **Cursor NF-e** intocado.
- Nenhuma migration.
