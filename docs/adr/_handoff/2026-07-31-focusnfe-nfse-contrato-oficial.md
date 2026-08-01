# FocusNFe · NFS-e recebida — contrato oficial alinhado (2026-07-31)

**Data:** 2026-07-31

**Vínculos:** ADR-0045 (Gateway como fronteira única), ADR-0049 (Espelho
canônico); handoffs `2026-07-17-fase-e4c-nfse-nacional-focusnfe.md`,
`2026-07-18-fix-nfse-focusnfe-servicos-lista.md`,
`2026-07-22-fix-nfse-focusnfe-rota-nfsens.md`,
`2026-07-30-danfse-html-recebida-m2m.md`.

**Modo:** correção cirúrgica autorizada; **sem deploy** nesta rodada;
timer segue **enabled/inactive** por decisão operacional anterior.

## Objetivo

Alinhar o provider `FocusNFeProvider` ao contrato oficial atual da
Focus para o recebimento de NFS-e Nacional
(`GET /v2/nfsens_recebidas` e `GET /v2/nfsens_recebidas/{chave}.xml`),
preservando:

- isolamento absoluto NF-e × NFS-e;
- cursor `versao` string opaca;
- ordem de processamento determinística por versão;
- não avanço de cursor em falha temporária ou pendência;
- XML canônico como única fonte fiscal.

## Documentação oficial vinculante

- <https://doc.focusnfe.com.br/reference/consultar_nfsen_recebidas>
- <https://doc.focusnfe.com.br/reference/consultar_nfsen_recebida_individual_xml>

## Contrato oficial (2026)

`GET /v2/nfsens_recebidas` devolve JSON array cujos itens contêm:

- `chave_nfse` (string opaca — identidade do documento);
- `situacao` textual: `"autorizado"` | `"cancelado"` | `"substituido"`;
- `nome_prestador`, `documento_prestador` (planos);
- `nome_tomador`, `documento_tomador` (planos, quando presentes);
- `valor_total`, `valor_iss`, `valor_liquido`;
- `data_emissao`, `data_geracao`;
- `versao` (inteiro incremental — cursor opaco);
- opcionais de cancelamento (`data_cancelamento`) e substituição
  (`chave_nfse_substituida`);
- opcionais operacionais: `numero`, `serie`, `codigo_verificacao`,
  `competencia`, `discriminacao`.

Headers da resposta:

- `X-Total-Count` — total de documentos no intervalo consultado;
- `X-Max-Version` — maior versão contida na página (**limite da
  página**, não autorização para avançar o cursor).

Query params:

- `cnpj` (obrigatório), `versao` (cursor de entrada), `completa=1`
  (para trazer o item completo em vez de resumo).

## Contrato antigo × contrato oficial

| Aspecto | Layout antigo (histórico) | Contrato oficial (2026) |
|---|---|---|
| Identidade | `chave` | `chave_nfse` |
| Estado | `status` numérico (1/2/3) | `situacao` textual (`autorizado`/`cancelado`/`substituido`) |
| Prestador/Tomador | dicts aninhados obrigatórios | campos planos no root (`nome_prestador`, `documento_prestador`) |
| Serviços | dict aninhado obrigatório | `valor_total`/`valor_iss`/… no root |
| XML canônico | `url_xml` obrigatória | `GET /v2/nfsens_recebidas/{chave}.xml` |
| Situação desconhecida | default silencioso "autorizada" | **erro nominal** (item bloqueia cursor) |

O layout antigo permanece aceito **apenas** como adaptador explícito
(sinalizado por `_layout_focus="legacy"` no dict devolvido). Nunca
misturado silenciosamente.

## Correções aplicadas

### 1. `providers/focusnfe_provider.py::_mapear_nfse_focus`

Reescrito (linhas 489-701) com precedência estrita pelo contrato
oficial:

- lê `chave_nfse` primeiro; adaptador legacy aceita `chave`/`chNFe`/
  `chave_nfe` (marca `_layout_focus="legacy"`);
- exige `versao` válida (via `_versao_focus_valida`); ausência ou 0 →
  `ValueError` capturado pelo pré-mapper → cursor bloqueado antes do
  item;
- lê `situacao` textual do root; adaptador legacy converte
  `status` numérico (1→"autorizado", 2→"cancelado", 3→"substituido");
- situação desconhecida → `ValueError` — **nunca** default
  silencioso;
- prestador/tomador lidos primeiro dos campos planos no root, depois
  dos aninhados como adaptador;
- `valor_total` no root; fallback `servicos.valor_servicos` marcado
  como legacy;
- preserva `data_emissao`, `data_geracao` (novo), `data_cancelamento`,
  `chave_nfse_substituida`.

DTO ganhou:

- `chave_nfse` (nome oficial), `chave`, `chNFe` (compat);
- `data_geracao`;
- `chave_nfse_substituida`;
- `data_cancelamento` (garantido no dict, mesmo vazio);
- `situacao_focus` (textual bruta);
- `_layout_focus` (`"oficial"` | `"legacy"`) — telemetria.

### 2. `providers/focusnfe_provider.py::_http_get_xml_bytes_upstream`

Após receber os bytes, valida:

- `Content-Type` explicitamente HTML/JSON/texto → rejeita com
  `FOCUS_XML_CONTENT_TYPE_INVALIDO`;
- prefixo `<html`/`<!doctype html` sem Content-Type → mesma rejeição
  (sanity check contra proxy respondendo HTML);
- ausência de Content-Type + corpo XML → aceita (storages
  pré-assinadas às vezes omitem).

Preserva bytes opacos quando o formato é aceitável.

### 3. Isolamento NF-e × NFS-e

Mapper NF-e (`_mapear_nfe_focus`) **não escreve** `chave_nfse` nem
`situacao_nfse` no dict — evita cross-contamination downstream. Provado
por teste estrutural (`TestRegressaoNfe`).

Endpoints separados no dispatch de `gov_fetch` (linhas 706-709):

- NF-e → `/v2/nfes_recebidas`
- NFS-e → `/v2/nfsens_recebidas`

Uma execução `tipo="nfse"` nunca chama endpoint NF-e (e vice-versa) —
provado pelos testes 21 e 22.

### 4. Cursor seguro

Sem alteração — a lógica de `cursor_seguro`/`menor_versao_pendente_ou_erro`/
`gap_sem_versao` (linhas ~1017-1136) permanece íntegra. Mapper agora
rejeita item sem `versao` antes de qualquer default silencioso.

### 5. Cancelada e substituída

XML **não é baixado** para status cancelado/substituído (linhas
960-963) — comportamento pré-existente preservado. O item entra em
`documentos[]` com `status_xml="RESUMO"`; o consumidor persiste
evento/estado nominal a partir dos metadados da listagem, **sem
fabricar Espelho válido**. `chave_nfse_substituida` é preservada
quando fornecida (novo).

**Se a Focus disponibilizar o XML para cancelado/substituído** em
alguma configuração de tenant, o endpoint canônico
`baixar_xml_nfse_por_chave` continua acessível e o Parser_Fiscal
recebe o XML normalmente — este comportamento fica NÃO COMPROVADO
enquanto não houver documento real desse tipo no ATP.

## Testes

### FiscalOne — focados novos

`tests/test_focusnfe_nfse_contrato_oficial.py` — **34 testes** cobrindo
os 25 requisitos §8 do prompt:

1. `chave_nfse` aceita ✓
2. `chave` histórica não confundida com contrato oficial ✓
3. `situacao=autorizado` ✓
4. `situacao=cancelado` ✓ (com `data_cancelamento`)
5. `situacao=substituido` ✓ (com `chave_nfse_substituida`)
6. situação desconhecida → `ValueError` nominal ✓
7. campos planos do prestador ✓
8. versão preservada como cursor opaco (`"1"` não vira `"000000000000001"`) ✓
9. consulta com `cnpj`, `versao`, `completa=1` ✓
10. paginação de 100 itens (has_more) ✓
11. `X-Max-Version` consumido ✓
12. `X-Total-Count` consumido ✓
13. XML individual recuperado pela chave (`/v2/nfsens_recebidas/{chave}.xml`) ✓
14. resposta HTML rejeitada com `FOCUS_XML_CONTENT_TYPE_INVALIDO` ✓
    (+ 14b: Content-Type ausente + corpo XML aceito;
     14c: `<html…` sem Content-Type ainda rejeitado)
15. 400 `empresa_nao_habilitada` → `FOCUS_NFSE_NAO_HABILITADA` ✓
16. 400 genérico → `FOCUS_BAD_REQUEST` (cursor não avança) ✓
17. 429/timeout/5xx → sem avanço de cursor ✓
18. erro no item intermediário → cursor bloqueado antes ✓
19. limite de XML por rodada (já provado em `test_focusnfe_safe_cursor`
    — regressão verde)
20. lista vazia não regride nem mistura NF-e ✓
21. execução NFS-e não toca endpoint NF-e ✓
22. execução NF-e não toca endpoint NFS-e ✓
23. contadores distinguem documento e erro ✓
24. Parser_Fiscal recebe XML, nunca resumo (`status_xml=COMPLETO`
    exige `xml_bruto`) ✓
25. mapper NF-e intacto ✓

### Regressão focada (FiscalOne)

- `test_focusnfe_nfse_e4c.py` — 49/49 ✓
- `test_focusnfe_http.py` — 80/80 ✓
- `test_focusnfe_safe_cursor.py` — 87/87 ✓
- `test_focusnfe_preparacao.py` — 25/25 ✓
- `test_danfse_nfse_focus.py` — 26/26 ✓

### Suíte integral

- **FiscalOne:** 512/512 ✓ — zero regressão.
- **MapOne:** 1593 passed / 97 skipped; 4 falhas (3 baseline conhecidas
  — `test_e3_sync_multiambiente`, `test_empresa_cedido`,
  `test_focusnfe_ativacao`; 1 flake Oracle SCN documentada —
  `test_override_espelho::test_concorrencia_deixa_um_ativo`). Zero
  regressão desta fatia.
- **Gate ADR-0046:** MapOne 2/2 ✓, CtrlOne (rlogix) 2/2 ✓.

## Prova operacional (§9)

**Fonte:** log `dfe_sync.log` do próprio scheduler MapOne (execução
manual autorizada 2026-07-31 20:18 -03).

- Tenant 1, doc_type=nfse, provider=focusnfe, cursor_tipo=versao,
  cursor entrada=`"0"`, cursor saída=`"0"` (transporte OK, resposta
  `SEM_DOCUMENTO`).
- Tenant 2, doc_type=nfse, idem `SEM_DOCUMENTO`.
- `nsu_avancou=True` no envelope reflete resposta legítima do provider
  (X-Max-Version=0 em página vazia) — cursor não avançou de fato.
- **Cursor NF-e permaneceu inalterado**: última execução NF-e do dia
  (`2026-07-31 19:40:07`) usou o cursor NF-e correspondente e a
  execução NFS-e subsequente não o tocou.
- Sem CNPJ/token no log; chaves mascaradas.

**Classificação:** o transporte respondeu; o mapper com payload NFS-e
real permanece **NÃO COMPROVADO** — sem NFS-e no batch atual.

## Timer (§10)

**Estado atual:**

- `mapone-dfe-sync.timer`: `enabled` mas `inactive (dead) since Wed
  2026-07-29 19:45:17 -03`.
- `mapone-dfe-sync.service`: `disabled`, última execução falhou com
  exit=1 em `2026-07-29 19:37:47`.

**Histórico do journal:** o serviço rodou com sucesso a cada 15min
entre 17:37 e 19:22 do dia 29. Em 19:37:47 houve uma execução com
`exit=1` (traceback não sobreviveu no log em disco). Oito minutos
depois (19:45:17) o timer foi manualmente parado
(`systemctl stop mapone-dfe-sync.timer` — não `disable`).

**Diagnóstico:**

- Unit files (`/etc/systemd/system/mapone-dfe-sync.{timer,service}`)
  íntegras e **idênticas** às versões em `dev/systemd/` do
  repositório.
- Nenhuma alteração de código ou instalação é necessária.
- Causa: **decisão operacional** posterior a uma falha pontual do
  scheduler. Não é bug do repositório.

**Ativação futura (não executada nesta rodada):**

```bash
# Diagnosticar antes de religar
sudo systemctl status mapone-dfe-sync.timer
sudo systemctl status mapone-dfe-sync.service
sudo journalctl -u mapone-dfe-sync.service -n 40 --no-pager

# Reativar (timer já enabled — só start)
sudo systemctl start mapone-dfe-sync.timer
```

## Reconciliação

| Requisito | Status |
|---|:---:|
| Contrato oficial `chave_nfse` + `situacao` textual + planos | ✓ |
| Adaptador legacy explícito e sinalizado | ✓ (`_layout_focus`) |
| Situação desconhecida → erro nominal | ✓ |
| Ausência de `chave_nfse` ou `versao` → erro por doc | ✓ |
| XML canônico via `GET /v2/nfsens_recebidas/{chave}.xml` | ✓ |
| Cancelada/substituída sem fabricar Espelho | ✓ |
| Isolamento NF-e × NFS-e (endpoints, mappers, doc_type) | ✓ |
| Cursor `versao` string opaca | ✓ |
| Cursor não avança em falha (400/429/5xx/timeout/HTML) | ✓ |
| `X-Max-Version` + `X-Total-Count` consumidos, não fonte única | ✓ |
| Corpo não XML rejeitado (`FOCUS_XML_CONTENT_TYPE_INVALIDO`) | ✓ |
| Contadores separam documento vs erro | ✓ (`documentos[]` + `erros[]`) |
| Nenhum HTML/token/CNPJ completo em envelope/log | ✓ |
| Suíte integral verde | ✓ (baseline preservado) |
| Gate ADR-0046 MapOne + CtrlOne | ✓ |

## Limitações e NÃO COMPROVADOS

- Mapper com payload real de NFS-e nacional: **NÃO COMPROVADO** — ATP
  atual devolve `SEM_DOCUMENTO` para todos os tenants configurados.
- Comportamento da Focus quando devolve XML para cancelada/substituída:
  **NÃO COMPROVADO** — sem documento desse tipo no ATP.
- Timer segue **inactive** — reativação requer intervenção operacional
  explícita e não faz parte desta fatia.

## Escopo — arquivos alterados

- **FiscalOne:**
  - `providers/focusnfe_provider.py` — mapper NFS-e + validação
    Content-Type no download XML;
  - `tests/test_focusnfe_nfse_contrato_oficial.py` — 34 provas focadas
    (novo).
- **MapOne:** nenhuma alteração de código; documentação a ser atualizada
  em commit separado.
- **CtrlOne (rlogix):** nenhuma alteração — gate ADR-0046 verde.

**Sem deploy nesta rodada. Sem restart de serviço. Sem ativação de
timer. Sem reimportação de acervo.**

---

## Retificação R1 — revisão independente (2026-07-31)

A revisão pós-entrega encontrou quatro divergências e elas foram
corrigidas antes de qualquer deploy:

1. o teste nominal de HTTP 400 usava 403; agora código e teste tratam
   `empresa_nao_habilitada` no 400 documentado;
2. `recebidos` somava erros; agora recebido, mapeado, XML recuperado,
   pendência e erro são contadores distintos;
3. cancelada/substituída deixa de ser `RESUMO` ambíguo e passa a
   `EVENTO`, sem fabricar Espelho; XML individual continua
   `NAO_COMPROVADO` até existir evidência real;
4. CNPJ/chave de aparência real foram eliminados das fixtures novas e
   substituídos por sentinelas sintéticos.

Prova local R1: 172 testes focados e 514 testes integrais verdes.
Importação assistida e evidência da VM são registradas abaixo somente
após execução controlada.
