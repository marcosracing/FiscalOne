# Handoff — CTE-RECEBIDOS (lote de CT-e recebidas via FocusNFe)

**Data:** 2026-09-10 · **Executor:** Claude Code · **Autorização:** Marcos no
chat ("vamos executar o Gate pequeno para importar Cte recebidos para o
Gerenciador fiscal ficar ok").

## Problema

Marcos habilitou DF-e de CT-e no painel da FocusNFe e cadastrou a linha
"CT-e · DFE · FocusNFe · Produção" no grid Integração Fiscal do CtrlOne. O
cursor ficava em 0 porque o provider do FiscalOne recusava `tipo='cte'` no
lote (`FOCUS_TIPO_NAO_SUPORTADO`); só existia XML individual por chave.

## Fonte normativa

`doc.focusnfe.com.br/reference/consultar_ctes_recebidas`, consultado em
2026-09-10: `GET /v2/ctes_recebidas?cnpj=&versao=`, 100 registros por
resposta, headers `X-Total-Count` e `X-Max-Version`, sem parâmetro
`completa`, e a lista de campos do item transcrita no manual.

## Entregue

| Item | Arquivo |
|---|---|
| `gov_fetch` aceita `cte`; endpoint, mapper e loop de XML por tipo | `providers/focusnfe_provider.py` |
| Mapper `_mapear_cte_focus` (campos oficiais → dict canônico, `chave` + `chCTe`) | idem |
| `baixar_xml_completo(..., doc_type="nfe")` genérico | idem |
| Aliases de chave CT-e no normalizador | `MapOne/logione/services/dfe_normalizador.py` |
| 12 testes novos; 2 testes de contrato atualizados | `tests/test_focusnfe_cte_recebidas.py`, `test_focusnfe_nfse_e4c.py`, `test_focusnfe_http.py` |

## Correção de escopo aproveitada

A combinação **MDF-e · DFE** saiu do grid Integração Fiscal do CtrlOne:
MDF-e não é documento recebido contra o CNPJ (quem o emite é a própria
transportadora) e a Focus não tem endpoint equivalente. MDF-e permanece
disponível como **Emissor**.

## Pendências

- `pytest.ini` tem WIP alheio (`-p no:cacheprovider`) não commitado neste gate.
- DACTE (PDF do CT-e) segue ausente: a Focus tem o endpoint
  (`consultar_cte_recebida_individual_pdf`), o FiscalOne ainda não o expõe.
  Entra no gate 2 do Bipe, junto do espelho do DACTE.
- Primeira coleta real depende do token de produção e do serviço habilitado
  na Focus — validar com Marcos após o deploy.
