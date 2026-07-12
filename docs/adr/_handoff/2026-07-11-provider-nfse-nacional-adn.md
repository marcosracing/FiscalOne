# Handoff — 2026-07-11 · Provider NFS-e Nacional (ADN) por NSU

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)
**Inicio operacional:** 2026-07-01

## Objetivo

Habilitar busca ativa de NFS-e recebida pela **NFS-e Nacional / ADN**
(Distribuicao DFe por NSU) mantendo o FiscalOne como gateway puro:

- nao persiste NSU, cooldown, XML ou cert
- MapOne continua dono da persistencia (`op_gov_cooldown`, `op_fiscal_xml`, `op_dfe_evento`)
- **emissao fiscal continua bloqueada por design** — inalterada nesta fase

## Alteracoes

### Novo: `providers/nfse_nacional_provider.py`

- Funcao publica: `consultar_dfe_nsu(cert_pem, key_pem, cnpj, nsu, ambiente, trace_id, incluir_xml_bruto=True)`
- Hosts:
  - `producao` → `adn.nfse.gov.br` (`ambiente_adn: producao`)
  - `homologacao` → `adn.producaorestrita.nfse.gov.br` (`ambiente_adn: producao_restrita`)
- Path: `GET /contribuintes/DFe/{NSU}` com `Accept: application/json`
- mTLS reutiliza `services/dfe_fetch_service._build_mtls_ctx` — PEM temp
  chmod 600, unlink em finally.
- Codigos controlados: `NFSE_ADN_HTTP_ERRO`, `NFSE_ADN_TIMEOUT`,
  `NFSE_ADN_XML_INVALIDO`, `NFSE_ADN_AUTH_ERRO`, `NFSE_ADN_LOTE_SEM_ARQUIVO`,
  `NFSE_ADN_DECODE_FALHOU`, `NFSE_ADN_PARSE_FALHOU`.
- Cooldowns recomendados (segundos):
  - docs encontrados → 0
  - SEM_DOCUMENTO (204/404 ou LoteDFe vazio) → 3600 (1h)
  - HTTP erro → 900 (15 min)
  - 403 AUTH → 3600 (1h)

### `services/dfe_fetch_service.py`

- `fetch_dfe(...)` ganha branch para `tipo="nfse"`: delega ao provider
  ADN e traduz o retorno para o envelope comum
  (`documentos[]`/`resumos[]`/`erros[]`/`results[]`) + campos NFS-e
  (`provider`, `ambiente_adn`, `status`, `status_processamento`).
- `cstat`/`xmotivo` continuam `None` no envelope NFS-e.
- Novo parametro `data_inicio` (eco em NFS-e e em NF-e/CT-e para simetria).

### `providers/sefaz_provider.py`

- `gov_fetch` aceita `tipo in ("nfe","cte","nfse")` e propaga
  `data_inicio` ao service.

### `app.py`

- `/fiscal/gov/fetch`:
  - validacao aceita `tipo="nfse"`; mensagem atualizada para listar `nfe/cte/nfse`
  - envelope agora expoe `provider`, `ambiente_adn`, `status`,
    `status_processamento`, `data_inicio`
  - `_status_para_codigo` mapeia codigos `NFSE_ADN_*` → HTTP 502
- `/fiscal/health` expoe:
  - `capacidade.gov_fetch_nfse: true`
  - `capacidade.nfse_adn_inicio_operacional: "2026-07-01"`

## Regras arquiteturais preservadas

- Zero persistencia no FiscalOne (ADR-0035)
- Cert A1 em transito (fonte 1: `cert_pfx_base64` do MapOne)
- PEM temp chmod 600, unlink em finally
- Nunca loga PFX/PEM/senha/base64/xml_bruto/token
- Emissao/cancelamento/inutilizacao/CC-e/MDF-e continuam bloqueados
- Sempre JSON — nenhum HTML/traceback

## Validacoes executadas

**py_compile** — OK para: `app.py`, `services/dfe_fetch_service.py`,
`services/cert_provider.py`, `providers/sefaz_provider.py`,
`providers/nfse_nacional_provider.py`, `xml_parser.py`.

**Smoke standalone (FiscalOne local, porta 5002):**

| Cenario | Esperado | Observado |
|---|---|---|
| `GET /fiscal/health` | gov_fetch_nfse=true, nfse_adn_inicio_operacional=2026-07-01, emissao_ativa=false | OK |
| `POST /fiscal/cte` | 403 EMISSAO_BLOQUEADA | OK |
| `POST /fiscal/gov/fetch tipo=nfse` sem cert | 400 CERT_NAO_CONFIGURADO + data_inicio ecoado | OK |
| `POST /fiscal/gov/fetch tipo=nfse` base64 invalido | 400 CERT_BASE64_INVALIDO | OK |
| `POST /fiscal/gov/fetch tipo=mdfe` | 400 TIPO_NAO_SUPORTADO (mensagem "'nfe', 'cte' ou 'nfse'") | OK |
| Grep segredos no log | vazio | OK |

**Teste unitario `nfse_nacional_provider` com ADN mockado (6 cenarios):**

| Cenario | Esperado | Observado |
|---|---|---|
| A. XML NFS-e completo (200 + LoteDFe) | status=DOCUMENTOS_LOCALIZADOS, docs=1, status_xml=COMPLETO | OK |
| B. HTTP 404 | ok=true, status=SEM_DOCUMENTO, cooldown=3600 | OK |
| C. HTTP 500 | ok=false, codigo=NFSE_ADN_HTTP_ERRO, cooldown=900 | OK |
| D. HTTP 403 | ok=false, codigo=NFSE_ADN_AUTH_ERRO, cooldown=3600 | OK |
| E. 200 body nao-JSON | ok=false, codigo=NFSE_ADN_XML_INVALIDO | OK |
| F. LoteDFe com ArquivoXml corrompido | ok=true, docs=0, erros=1 (NFSE_ADN_DECODE_FALHOU) | OK |

**Teste E2E `fetch_dfe(tipo="nfse")` com provider mockado:**

- envelope compativel MapOne: `documentos + resumos + erros + results`
- `provider="nfse_nacional"`, `ambiente_adn="producao"`, `status="DOCUMENTOS_LOCALIZADOS"`
- `cstat`/`xmotivo` = None; `data_inicio="2026-07-01"` ecoado
- `results[0].categoria="COMPLETO"`, `doc0.doc_type="nfse"`
- `incluir_xml_bruto=False` respeitado

**Nenhuma chamada real ao ADN.**

## Como o MapOne consome (contrato)

    POST http://<fiscalone>/fiscal/gov/fetch
    {
      "cnpj_tenant": "07219398000109",
      "ambiente":    "producao",
      "tipo":        "nfse",
      "ultimo_nsu":  "125643",
      "data_inicio": "2026-07-01",
      "cert_pfx_base64": "<PFX>",
      "cert_password":   "<senha>"
    }

Resposta (envelope compativel com NF-e/CT-e):

    {
      "ok": true, "trace_id": "fo-...",
      "provider": "nfse_nacional",
      "ambiente_adn": "producao",
      "status": "DOCUMENTOS_LOCALIZADOS",
      "status_processamento": "DOCUMENTOS_LOCALIZADOS",
      "ultimo_nsu": "125700", "max_nsu": "125800",
      "cooldown_recomendado_seg": 0,
      "documentos": [
        { "doc_type": "nfse", "status_xml": "COMPLETO", "nsu": "...",
          "chave": "...", "emit_cnpj": "...", "emit_nome": "...",
          "dh_emi": "...", "valor_total": 0.0,
          "xml_bruto": "...", "xml_hash_sha256": "...",
          "parser_version": "fiscalone_xml_parser" }
      ],
      "resumos": [], "erros": [],
      "results": [ { "...", "categoria": "COMPLETO" } ],
      "data_inicio": "2026-07-01"
    }

`cstat`/`xmotivo` sao `null` no envelope NFS-e — usar `status` e
`status_processamento`.

## Riscos

- ADN nao devolve resumos hoje. Se um dia comecar a devolver, o provider
  passara os itens problematicos para `erros[]` com codigo `NFSE_ADN_PARSE_FALHOU`.
- O manual oficial do ADN pode evoluir. Se mudar o path (`/contribuintes/DFe/{NSU}`)
  ou o encoding do `ArquivoXml` (hoje `gzip+base64`), so o provider precisa mudar.

## Pendencias para o MapOne

1. Agendamento DFe deve iterar tambem `tipo="nfse"` para as tenants
   habilitadas (a partir de 2026-07-01), respeitando `cooldown_recomendado_seg`
   por par `(tenant, tipo)`.
2. `op_gov_cooldown` precisa aceitar `tipo="nfse"` na sua PK/UK.
3. `op_fiscal_xml.import_origin='fiscalone_nfse_adn'` para os XMLs vindos
   do ADN (o provider marca `parser_version="fiscalone_xml_parser"` e
   `import_origin="fiscalone_nfse_adn"` no parse interno).
4. `data_inicio` — MapOne persiste o corte por data e filtra os documentos
   com `dh_emi < 2026-07-01` antes de expor ao Gerenciador Fiscal 50.1.1.
5. Smoke integrado via `POST /fiscal/api/buscar-dfe` do MapOne com
   `tipo="nfse"` para validar ponta a ponta.

## Fora do escopo desta fase (permanece bloqueado)

- Emissao NFS-e nacional
- Cancelamento / substituicao NFS-e
- Consulta chave-a-chave NFS-e (ainda nao implementada)
- Qualquer endpoint emissor
