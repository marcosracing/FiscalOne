# Handoff — 2026-07-13 · cStat + acao + nsu_avancou no envelope /gov/fetch

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## Problema

Log do FiscalOne registrava apenas `resultado=ok` — MapOne nao conseguia
distinguir se a SEFAZ:
- Devolveu documentos (cStat=138)
- Nao tem documentos (cStat=137)
- Rejeitou a consulta (cStat=656 ou similar)

Consequencia: NSU avancava mesmo em cStat=656, causando perda da janela de
retentativa.

## Solucao

Envelope `/fiscal/gov/fetch` agora expoe:

- **`acao`** (str): DOCUMENTOS | SEM_DOCUMENTO | REJEITADO | ERRO
- **`nsu_avancou`** (bool): decisao pronta para MapOne — true em DOCUMENTOS
  e SEM_DOCUMENTO; false em REJEITADO e ERRO
- **`ultimo_nsu_antes`** (str): eco do NSU enviado pelo MapOne (auditoria)
- **`ultimo_nsu`** (str): NSU pos-consulta (SEFAZ/ADN)
- **`max_nsu`** (str): teto conhecido pela SEFAZ/ADN
- **`cstat`** / **`xmotivo`**: ja existiam (mantidos)

Log estruturado tambem inclui os 5 novos campos:
```json
{"operacao":"gov_fetch", "resultado":"ok",
 "cstat":"656", "acao":"REJEITADO",
 "ultimo_nsu_antes":"7400", "ultimo_nsu":"000000000007400",
 "max_nsu":"000000000007414", "nsu_avancou":false, ...}
```

## Nota importante sobre cStat

O prompt de origem mencionou `cstat=100→DOCUMENTOS, cstat=138→SEM_DOCUMENTO`.
A spec **NFeDistDFeInteresse** define o inverso:

- cStat=137 → **nenhum** documento
- cStat=138 → **documentos** localizados
- cStat=656 → Consumo Indevido
- cStat=589 → NSU consultado > maxNSU

Implementacao adotou a semantica correta da spec SEFAZ, e o classificador e
robusto contra ambiguidade: **usa contagem de documentos como fonte primaria**
e cStat como fonte secundaria. Assim, mesmo que um cStat seja renumerado em
uma revisao futura, `docs_count > 0 → DOCUMENTOS` permanece verdadeiro.

## Codigo — arquivos alterados

### `app.py`

- `_classificar_acao_gov_fetch(result, docs_count) -> str` — nova funcao
- `_nsu_avancou(acao) -> bool` — nova funcao
- Envelope `/fiscal/gov/fetch` inclui `acao`, `nsu_avancou`, `ultimo_nsu_antes`
- `_log_stdout` estendido para `cstat`, `acao`, `ultimo_nsu_antes`,
  `ultimo_nsu`, `max_nsu`, `nsu_avancou`
- Contadores `_CSTAT_OK_SEM_DOC`, `_CSTAT_OK_COM_DOC`, `_CSTAT_REJEICAO_SEFAZ`,
  `_CODIGO_TECNICO_ERRO` centralizados

### `tests/test_gov_fetch_acao.py` (novo — 14 testes)

- 8 testes do classificador (`_classificar_acao_gov_fetch`)
  - cstat 138 com/sem docs, 137, 656, 589
  - erro tecnico FiscalOne
  - NFS-e ADN status DOCUMENTOS_LOCALIZADOS e SEM_DOCUMENTO
- 6 testes end-to-end via `/fiscal/gov/fetch`
  - cstat 138 → DOCUMENTOS + nsu_avancou=true
  - cstat 137 → SEM_DOCUMENTO + nsu_avancou=true
  - cstat 656 → REJEITADO + nsu_avancou=**false** (regressao original)
  - erro tecnico (sem cert) → ERRO + nsu_avancou=false
  - envelope sempre tem cstat/xmotivo/acao/nsu_avancou
  - NFS-e ADN → mesmo envelope, sem cstat

### `docs/manual-tecnico-FiscalOne.md`

- Nova secao 2e: tabela cStat → acao → nsu_avancou → cooldown
- Regra de consumo pelo MapOne (`if nsu_avancou: atualiza CtrlOne`)

## Regras preservadas

- Emissao fiscal continua bloqueada por design (10 rotas + guard)
- Cert em transito, PEM chmod 600 + unlink em finally
- Zero persistencia (ADR-0035)
- Log sem PFX/PEM/senha/base64/xml_bruto (grep confirmado)
- 3 flags de producao inalteradas

## Validacoes

- `py_compile` em app.py — OK
- `pytest tests/` — **60/60 verde** (46 antigos + 14 novos)
- Classificador testado com 9 cenarios sinteticos + 6 end-to-end
- **Nenhuma chamada real a SEFAZ/ADN**

## Contrato final do envelope

```json
{
  "ok": true,
  "trace_id": "fo-...",
  "codigo": null,
  "acao": "REJEITADO",
  "nsu_avancou": false,
  "status_lote": "SEM_DOCUMENTO",
  "recebidos": 0, "processados": 0, "persistidos": 0,
  "duplicados": 0, "resumos_count": 0, "eventos": 0, "erros_count": 0,
  "cstat": "656",
  "xmotivo": "Rejeicao: Consumo Indevido...",
  "provider": "sefaz",
  "ultimo_nsu_antes": "7400",
  "ultimo_nsu": "000000000007400",
  "max_nsu": "000000000007414",
  "cooldown_recomendado_seg": 3900,
  "documentos": [], "resumos": [], "erros": [], "results": [],
  "ambiente": "producao", "tipo": "nfe"
}
```

## Pendencias para o MapOne

1. Consumir `acao` e `nsu_avancou` no orquestrador de busca DFe:
   ```python
   if envelope["nsu_avancou"]:
       ctrlone.update_nsu(cnpj, tipo, envelope["ultimo_nsu"])
   ```
2. Aumentar `proxima_consulta_utc` de `op_gov_cooldown` para
   `agora + cooldown_recomendado_seg` em qualquer acao (nao so REJEITADO).
3. Logar `acao`+`cstat`+`xmotivo` no `op_fiscal_log` para visibilidade
   operacional.
4. Alerta operacional: se `acao=REJEITADO` mais de 3x consecutivas para o
   mesmo `(tenant, tipo)`, criar incidente (indica bloqueio SEFAZ persistente).

## Push

**Nao executado.** Aguardando aprovacao explicita do operador.
