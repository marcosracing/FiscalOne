# Handoff — 2026-07-09 · DFe RESUMO vs COMPLETO

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADR:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## Motivacao

Em tela de outro sistema, foi observado que a SEFAZ Distribuicao DFe devolve,
para o mesmo interessado, mistura de:

- Documentos **COMPLETOS** (`procNFe_v4.00` / `procCTe_v4.00`)
- Documentos **RESUMO** (`resNFe_v1.01` / `resCTe_v1.00` / `resEvento_v1.01`)

Exemplo real: NSU inicial 125643 · NSU 256517 veio como RESUMO · demais como COMPLETO.

**Bug corrigido:** o FiscalOne descartava silenciosamente docZips resumo (filtro
`<infnfe>`/`<infcte>` no service) e — via `/fiscal/documents/import` — devolvia
`PARSE_UNSUPPORTED` para XMLs resumo.

## Correcao

### xml_parser.py

- `_detect_type_by_localnames`: reconhece `resnfe`, `rescte`, `resevento`.
- Novos: `_parse_resumo_nfe`, `_parse_resumo_cte`, `_parse_resumo_evento`.
  Retornam `ok:false, codigo:"RESUMO_DFE_RECEBIDO", status_xml:"RESUMO"` com
  chave, emit_cnpj, emit_nome, dh_emi, valor_total e campos auxiliares
  (cSitNFe, tpNF, digVal etc.).
- `parse_xml`: encaminha aos parsers de resumo; para documentos COMPLETOS
  adiciona `status_xml:"COMPLETO"` no retorno ok.

### services/dfe_fetch_service.py

- Removido o filtro que descartava docZips resumo.
- Cada docZip do lote e classificado em:
  - `documentos[]` (COMPLETO)
  - `resumos[]` (RESUMO)
  - `erros[]` (schema desconhecido ou decode falhou)
- Retorno inclui `results[]` unificado com `categoria` e `status_xml` por item.

### app.py — envelope /fiscal/gov/fetch

    {
      "ok": true, "cstat": "138", "xmotivo": "...",
      "ultimo_nsu": "...", "max_nsu": "...",
      "cooldown_recomendado_seg": 0,
      "documentos": [ {status_xml: "COMPLETO", ...} ],
      "resumos":    [ {status_xml: "RESUMO", codigo: "RESUMO_DFE_RECEBIDO", ...} ],
      "erros":      [ {codigo: "PARSE_UNSUPPORTED_DFE"|"DOCZIP_DECODE_FALHOU", ...} ],
      "results":    [ {..., categoria: "COMPLETO"|"RESUMO"|"ERRO"} ]
    }

## Regras arquiteturais preservadas

- Zero persistencia no FiscalOne (ADR-0035)
- Certificado A1 em transito; PEM temporario chmod 600, unlink em finally
- Nunca loga cert/PFX/senha/PEM/token/xml_bruto
- Emissao, cancelamento, MDF-e continuam bloqueados
- Nenhum caminho devolve traceback HTML

## MapOne — regra de persistencia

- **COMPLETO** → grava em `op_fiscal_xml` como documento fiscal oficial
  (xml_bruto + hash + parser_version + nsu + schema).
- **RESUMO** → **NAO** entra em `op_fiscal_xml`. Persistir como pendencia
  operacional (ex.: tabela `op_dfe_resumo_pendente`) com chave + emit + valor.
  Quando o COMPLETO chegar em NSU posterior, fechar a pendencia.
- **ERRO** → registrar log/incidente. Nao bloquear o drain do NSU.

## Validacoes executadas

- `py_compile` em app.py, xml_parser.py, providers/*, services/* — OK
- `/fiscal/documents/import` com resNFe simulado → 200 ok:true,
  `results[0]` com `status_xml:"RESUMO"`, `codigo:"RESUMO_DFE_RECEBIDO"`
- `/fiscal/documents/import` com procNFe simulado → 200 ok:true,
  `results[0]` com `status_xml:"COMPLETO"`, campos fiscais normais
- `/fiscal/documents/import` com resEvento simulado → 200 ok:true,
  `results[0]` com `status_xml:"RESUMO"`, `doc_type:"evento"`
- `/fiscal/gov/fetch` sem cert → 400 `CERT_NAO_CONFIGURADO` com envelope
  `documentos:[], resumos:[], erros:[], results:[]`
- `/fiscal/gov/fetch` payload vazio → 400 `PAYLOAD_INVALIDO`
- **Teste unitario** do `dfe_fetch_service.fetch_dfe` com resposta SEFAZ
  simulada contendo 1 procNFe + 1 resNFe + 1 schema desconhecido:
  classificacao correta em `documentos[1]`, `resumos[1]`, `erros[1]`.

**Nao houve chamada real a SEFAZ.**

## Pendencias

- MapOne: adicionar tabela de pendencias de RESUMO (chave + emit + dh_emi +
  valor + `pendente_completo` boolean).
- MapOne: rotina de reconciliacao — ao receber um COMPLETO cuja chave estava
  em pendencia, atualizar o registro operacional e liberar o Gerenciador
  Fiscal 50.1.1.
- FiscalOne (opcional futura Fase 2): endpoint de consulta chave-a-chave para
  forcar retrieval do COMPLETO (`NFeConsultaProtocolo` / `CTeConsultaProtocolo`).
