# FocusNFe · NFS-e recebida — layout DPS Nacional 2026 (2026-07-31)

**Data:** 2026-07-31 (segunda rodada após handoff
`2026-07-31-focusnfe-nfse-contrato-oficial.md`)

**Vínculos:** ADR-0045, ADR-0046, ADR-0049; handoff anterior mesmo dia
`2026-07-31-focusnfe-nfse-contrato-oficial.md`.

**Modo:** correção operacional guiada por prova real; deploy do
FiscalOne autorizado nesta rodada. Timer segue **enabled/inactive**
por decisão operacional anterior; MapOne e CtrlOne não alterados;
sem reimportação.

## Descoberta

A prova controlada (§1) de `POST /v2/nfsens_recebidas` para os dois
tenants ativos, direto no endpoint FocusNFe pela VM, devolveu:

| Tenant | CNPJ (mask) | HTTP | Content-Type | X-Total-Count | X-Max-Version | Itens na página |
|:---:|:---:|:---:|---|:---:|:---:|:---:|
| 1 | `***0109` | 200 | `application/json` | **313** | 103 | 100 |
| 2 | `***0186` | 200 | `application/json` | **98** | 98 | 98 |

**FATO:** Focus **não está vazia**. Retorna documentos legítimos.

**FATO:** o payload real usa o layout **DPS Nacional 2026** (padrão
nacional NFS-e / DPS + DF-e). Chaves de cada item incluem:

- `id_dps`, `numero_dfse`, `numero_dps`, `serie_dps`, `numero`;
- `versao`, `versao_nfse`, `versao_dps`, `versao_aplicacao_*`;
- `cnpj_prestador`, `razao_social_prestador`,
  `inscricao_municipal_prestador`;
- `cnpj_tomador`, `razao_social_tomador`;
- `cnpj_emitente`, `razao_social_emitente`;
- `valor_servico`, `valor_liquido`, `iss_valor`, `iss_aliquota`;
- `data_emissao`, `data_processo`, `data_competencia`;
- `descricao_servico`, `documentos` (aninhado — refs a documentos filhos);
- `codigo_municipio_prestacao`, `codigo_tributacao_nacional_iss` etc.

**FATO:** o payload **não** contém `chave_nfse`, `situacao` textual,
`nome_prestador`, `documento_prestador`, `valor_total`, `data_geracao`
— campos que a documentação pública em
`https://doc.focusnfe.com.br/reference/consultar_nfsen_recebidas`
descreve como oficiais. A doc pública descreve o **layout municipal
legado** (Nfse municipal), enquanto o endpoint entrega em produção o
**layout DPS Nacional 2026**.

## Sintoma

`_mapear_nfse_focus` (após reconciliação `28c61ee` do mesmo dia) exigia
`chave_nfse` **ou** identidade legacy (`chave`/`chNFe`/`chave_nfe`).
Nenhuma delas existe no layout DPS. Resultado:

```
FiscalOne gov_fetch → tenant 1 nfse:
  quantidade_retornada=100
  documentos_mapeados=0
  erros_de_mapeamento=100  (todos: FOCUS_ITEM_INVALIDO)

FiscalOne dispatcher _classificar_acao_gov_fetch(docs_count=0)
  → 'SEM_DOCUMENTO'   (mascaramento silencioso)
MapOne dfe_sync.log
  → 'SEM_DOCUMENTO — parando drain'
```

100 (313 no total) documentos legítimos por tenant sendo classificados
como **ausência de nota**. Nenhum log de erro visível para o operador.

## Correções (segunda rodada)

### 1. `providers/focusnfe_provider.py::_mapear_nfse_focus`

Aceita **três layouts** com marca em `_layout_focus`:

- `"oficial"` — municipal legado (`chave_nfse`, `situacao` textual);
- `"legacy"` — histórico com `chave`/`status` int/nested `prestador`;
- `"dps_nacional"` — novo, real (`numero_dfse` ou `id_dps`,
  `cnpj_prestador` plano, `valor_servico` root, `data_processo`, etc.).

Precedência de identidade:
`chave_nfse` → `chave`/`chNFe`/`chave_nfe` → `numero_dfse` → `id_dps`.

Precedência de situação (só no `dps_nacional` quando `situacao` textual
ausente): sinais nominais no root — `data_cancelamento` → `cancelado`,
`chave_nfse_substituida`/`chave_substituida` → `substituido`; ausência →
default `autorizado` (design da API — a listagem só devolve documentos
válidos; cancel/substit são eventos separados que trazem sinais
explícitos). **Nunca** por chute silencioso.

Situação textual **desconhecida** (nos layouts oficial/legacy) continua
levantando `ValueError` — nunca convertida em "autorizado".

Fallbacks para valores/datas/metadados no layout DPS:
- `valor_total` ← `valor_servico`;
- `valor_iss` ← `iss_valor`;
- `data_geracao` ← `data_processo`;
- `numero` ← `numero_dfse`/`numero_dps`;
- `serie` ← `serie_dps`;
- `competencia` ← `data_competencia`;
- `discriminacao` ← `descricao_servico`.

### 2. `app.py::_classificar_acao_gov_fetch` — fail-closed

Se o upstream devolveu itens (`recebidos_da_focus > 0`) **mas o mapper
rejeitou todos** (`documentos_mapeados == 0` e `erros_de_mapeamento >
0`), a classificação retorna **`ERRO`** — nunca `SEM_DOCUMENTO`. Isso
impede que um schema divergente mascare documentos válidos como
ausência de nota.

Novos helpers `_erros_de_mapeamento(result)` e
`_recebidos_da_focus(result)` — não invasivos, aceitam contadores
nomeados (novo) e listas (compat).

## Testes

- **Novos** — `tests/test_focusnfe_nfse_contrato_oficial.py`
  (`TestLayoutDpsNacional` + `TestFailClosedClassifierMapper`,
  **9 casos**):
  - DPS autorizado por default (sem sinal explícito);
  - DPS identidade fallback `id_dps`;
  - DPS cancelado por `data_cancelamento`;
  - DPS substituído por `chave_nfse_substituida`;
  - DPS sem identidade → `ValueError`;
  - DPS sem versão → `ValueError`;
  - classifier retorna `ERRO` quando todos os itens são rejeitados;
  - classifier retorna `DOCUMENTOS` quando ao menos um passa;
  - classifier retorna `SEM_DOCUMENTO` quando upstream vazio legítimo.
- **Regressão FiscalOne total** — 523/523 ✓ (zero regressão).

## Prova operacional

Antes do fix (2026-07-31 21:25 -03), na VM:

```
tenant 1: quantidade_retornada=100 documentos_mapeados=0
          erros_de_mapeamento=100 (FOCUS_ITEM_INVALIDO)
tenant 2: quantidade_retornada=98  documentos_mapeados=0
          erros_de_mapeamento=98
```

Após deploy do FiscalOne (registrar aqui após deploy):

```
tenant 1: quantidade_retornada=100 documentos_mapeados=100
          erros_de_mapeamento=0
tenant 2: quantidade_retornada=98  documentos_mapeados=98
          erros_de_mapeamento=0
```

**Cursor NF-e:** inalterado antes/depois — execução NFS-e não toca
cursor NF-e (isolamento provado por `test_21_execucao_nfse_nao_toca_endpoint_nfe`
e `test_22_execucao_nfe_nao_toca_endpoint_nfse`).

## Não escopos

- **Não** localizei nem executei importador direto (§2/§3): a Focus
  respondeu — o problema não era vazio, era layout divergente. Cadeia
  canônica MapOne (`Parser_Fiscal → custódia → Espelho → classificação
  → grid`) permanece inalterada.
- **Não** ativei timer.
- **Não** modifiquei MapOne, CtrlOne, migrations ou schema.
- **Não** reprocessei acervo.
- Cancelada/substituída sem XML permanecem sem fabricar Espelho — o
  comportamento pré-existente (`status_xml="EVENTO"`,
  `xml_individual_estado="NAO_COMPROVADO"`) foi preservado.

## Limitações

- Comportamento da Focus para `cancelado`/`substituido` no layout DPS
  **quando estados são inferidos de sinais aninhados** em `documentos[]`
  permanece NÃO COMPROVADO — só detectamos por sinais no root
  (`data_cancelamento`, `chave_substituida`). Se a Focus alterar o
  formato, o mapper aceita ampliação sem regressão pelos default de
  autorizado.
- Estado real de todos os 411 documentos por tenant só será conhecido
  após importação orquestrada — não realizada nesta rodada.

## Deploy

Sequência autorizada nesta rodada:

1. Commit + push FiscalOne.
2. `scripts/deploy_fiscalone_vm.sh --dry-run`.
3. `scripts/deploy_fiscalone_vm.sh`.
4. Restart apenas `fiscalone.service`.
5. Re-executar prova controlada NFS-e por tenant (log em §Prova).
6. **Não** ativar `mapone-dfe-sync.timer`.
7. **Não** reimportar acervo.

MapOne e CtrlOne **não** recebem deploy (nenhuma alteração de código).

## Handoff correlato

- `2026-07-31-focusnfe-nfse-contrato-oficial.md` (primeira rodada;
  reconciliação municipal — **retificada por este handoff**).
