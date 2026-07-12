# FiscalOne — Gateway Fiscal Técnico RLogix

Gateway fiscal técnico do ecossistema RLogix — **Fase 1 · DFe recebidos**.
Parseia NF-e/CT-e/MDF-e/NFS-e (XML e PDF) localmente. SEFAZ, emissao e busca ativa sao stubs.

## Capacidade atual (2026-07-09)

| Funcionalidade | Status | Observacao |
|---|---|---|
| `POST /fiscal/documents/import` — parse XML/PDF/ZIP | operacional | NF-e, CT-e, MDF-e, NFS-e (nacional/ABRASF), NFS-e PDF (Prefeitura de SP + generico); sem persistencia propria |
| `GET /fiscal/health` | operacional | Reporta fase, flags, capacidades e stubs ativos |
| Trava de producao (flags duplas) | operacional | Bloqueia toda operacao em prod sem flags |
| `POST /fiscal/gov/fetch` — busca SEFAZ/DFe ativo | operacional (fase 1 · NF-e/CT-e por pagina, NFS-e Nacional/ADN por NSU) | Gateway puro; cert A1 por requisicao. NFS-e inicio operacional 2026-07-01 |
| `POST /fiscal/sync/{cnpj}` — sync NF-e/CT-e | stub | SefazProvider nao migrado |
| `GET /fiscal/nfe/{cnpj}`, `GET /fiscal/cte/{cnpj}` | stub | SefazProvider nao migrado |
| `POST /fiscal/cte` — emitir CT-e | bloqueado | Nao implementado; retorna 501 |
| `POST /fiscal/mdfe` — emitir MDF-e | bloqueado | Nao implementado; retorna 501 |
| Cancelamento / encerramento / condutor | bloqueado | Retornam 501 |
| `GET /fiscal/status/{uf}` — status SEFAZ por UF | stub | SefazProvider nao migrado |
| Certificado digital / assinatura XML | nao existe | SefazProvider e stub; nenhuma assinatura real |
| Persistencia propria (banco/XML raw/cooldown) | revogado | ADR-0035: zero persistencia propria |
| FocusNFeProvider | nao existe | Stub; implementar quando necessario |

## ADR-0035 — zero persistencia propria

O FiscalOne nao tem banco, nao persiste XML raw, protocolo, evento, cooldown ou certificado.
Toda persistencia e responsabilidade da vertical (MapOne, CtrlOne).
trace_id propaga — nao armazena. Log vai para stdout; a vertical coleta se necessario.

## Trava de seguranca fiscal — producao liberada APENAS para DFe recebido

Nesta fase o FiscalOne e gateway exclusivo para consulta/recepcao DFe
(NFeDistDFeInteresse / CTeDistDFeInteresse). Emissao fiscal em qualquer
natureza (NF-e, CT-e, MDF-e, cancelamento, inutilizacao, CC-e, encerramento
MDF-e, condutor MDF-e) permanece **bloqueada por design** — mesmo com todas
as flags de producao ligadas.

Padrao obrigatorio (homologacao):

    FISCALONE_AMBIENTE=homologacao
    FISCALONE_ENABLE_PRODUCAO=0
    MAPONE_FISCAL_PRODUCAO_READY=0
    FISCALONE_DFE_RECEBIDO_ONLY=0

Para liberar producao DFe recebido, as **tres** flags precisam estar em `1`:

    FISCALONE_AMBIENTE=producao
    FISCALONE_ENABLE_PRODUCAO=1
    MAPONE_FISCAL_PRODUCAO_READY=1
    FISCALONE_DFE_RECEBIDO_ONLY=1

Regras:

- `POST /fiscal/gov/fetch` opera em producao **somente** com as tres flags = 1.
- Qualquer rota de emissao/cancelamento/inutilizacao/CC-e/encerramento MDF-e
  ou condutor retorna **403 EMISSAO_BLOQUEADA**, ignorando as flags.
- `GET /fiscal/health` reporta `flags_producao`, `flags_producao_faltantes`,
  `escopo_liberado` e `emissao_bloqueada_por_design`.

## Provider pattern (arquitetural — stubs hoje)

Trocar provider = trocar variavel de ambiente (quando os providers estiverem implementados):

    FISCAL_PROVIDER=sefaz      # SefazProvider — stub, aguarda migracao gov_import.py (ADR-0028)
    FISCAL_PROVIDER=focusnfe   # FocusNFeProvider — stub, aguarda implementacao

## Rodar

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python app.py

Porta: 5002

## Endpoints

| Metodo | Endpoint | Status | Descricao |
|--------|----------|--------|-----------|
| GET | /fiscal/health | operacional | Health check, fase e flags |
| POST | /fiscal/documents/import | operacional | Parse XML/PDF/ZIP sem persistencia |
| POST | /fiscal/gov/fetch | operacional | Busca SEFAZ NF-e/CT-e (NFeDistDFeInteresse) por pagina, sem persistencia |
| POST | /fiscal/sync/{cnpj} | stub | Sync NF-e + CT-e — provider nao migrado |
| GET | /fiscal/nfe/{cnpj} | stub | Listar NF-es — provider nao migrado |
| GET | /fiscal/cte/{cnpj} | stub | Listar CT-es — provider nao migrado |
| POST | /fiscal/cte | bloqueado | Emissao CT-e nao liberada (501) |
| POST | /fiscal/mdfe | bloqueado | Emissao MDF-e nao liberada (501) |
| DELETE | /fiscal/cte/{chave} | bloqueado | Cancelamento nao liberado (501) |
| GET | /fiscal/status/{uf} | stub | Status SEFAZ — provider nao migrado |

## Documentos recebidos suportados (Fase 1)

- XML:
  - NF-e (`infNFe`) — layout nacional SEFAZ.
  - CT-e (`infCte`) — layout nacional SEFAZ; resolve `tomador` via toma3/toma4.
  - MDF-e (`infMDFe`) — chave e cabeçalho básico.
  - NFS-e padrão nacional (`sped.fazenda.gov.br/nfse`, `infNFSe`/`DPS`).
  - NFS-e ABRASF/municipal genérico (melhor esforço, `confianca=media`).
  - Eventos (`infEvento`, ex.: cancelamento tpEvento 110111).
- PDF (requer `pdfminer.six`):
  - NFS-e Prefeitura de São Paulo capital (Identificador Nacional como
    chave — pode ter mais de 44 dígitos e **não é truncado**).
  - Fallback genérico por regex — `confianca=baixa`, marca `extra.revisar=true`.
- ZIP: expandido internamente; aceita `.xml` e `.pdf` misturados.

Se a chave canônica não estiver disponível, o parser gera uma chave estável:

    nfse:{emit_cnpj}:{numero}:{dh_emi}:{valor_total}

## POST /fiscal/gov/fetch — contrato

Gateway puro (ADR-0035). Sem persistencia de NSU/XML/cooldown.

Payload:

    {
      "cnpj_tenant":       "07219398000109",
      "ambiente":          "homologacao",     // ou "producao"
      "tipo":              "nfe",             // ou "cte" ou "nfse"
      "ultimo_nsu":        "000000000000000",
      "cert_source":       "inline_base64",   // ou "env" (fallback homologacao)
      "cert_pfx_base64":   "<PFX em base64>",
      "cert_password":     "<senha do PFX>",
      "data_inicio":       "2026-07-01"       // opcional — metadado NFS-e (eco)
    }

**NFS-e Nacional (ADN)** — `tipo="nfse"` — ativa desde **2026-07-01**:

- Backend: `providers/nfse_nacional_provider.py` (GET mTLS por NSU)
- Producao: `https://adn.nfse.gov.br/contribuintes/DFe/{NSU}`
- Producao restrita: `https://adn.producaorestrita.nfse.gov.br/contribuintes/DFe/{NSU}`
- Envelope: mesmo formato de NF-e/CT-e (`documentos`, `resumos`, `erros`, `results`),
  porem `cstat`/`xMotivo` sao `null`; em lugar deles vem `status`
  (`DOCUMENTOS_LOCALIZADOS`/`SEM_DOCUMENTO`) e `status_processamento` do ADN.
- `data_inicio` (`YYYY-MM-DD`) — o FiscalOne so **ecoa** este campo; corte real
  por data e responsabilidade do MapOne quando persistir/filtrar eventos.
- Codigos de erro NFS-e: `NFSE_ADN_HTTP_ERRO`, `NFSE_ADN_TIMEOUT`,
  `NFSE_ADN_XML_INVALIDO`, `NFSE_ADN_AUTH_ERRO`,
  `NFSE_ADN_LOTE_SEM_ARQUIVO`, `NFSE_ADN_DECODE_FALHOU`.

Resposta (ok=true):

    {
      "ok": true, "trace_id": "fo-...",
      "cstat": "138", "xmotivo": "Documentos localizados",
      "ultimo_nsu": "000000000000123",
      "max_nsu": "000000000000200",
      "cooldown_recomendado_seg": 0,
      "documentos": [ /* COMPLETO: nfeProc/cteProc */
        {
          "status_xml": "COMPLETO",
          "doc_type": "nfe", "chave": "...", "numero": "...",
          "emit_cnpj": "...", "emit_nome": "...", "dest_cnpj": "...",
          "dh_emi": "2026-06-27", "valor_total": 1234.56,
          "xml_bruto": "<xml completo>",
          "xml_hash_sha256": "...",
          "parser_version": "fiscalone_xml_parser",
          "nsu": "...", "schema": "procNFe_v4.00"
        }
      ],
      "resumos": [ /* RESUMO: resNFe/resCTe/resEvento — XML fiscal completo NAO disponivel */
        {
          "status_xml": "RESUMO",
          "codigo": "RESUMO_DFE_RECEBIDO",
          "doc_type": "nfe", "chave": "...", "emit_cnpj": "...",
          "emit_nome": "...", "dh_emi": "...", "valor_total": 256.51,
          "cSitNFe": "1", "tpNF": "1",
          "nsu": "...", "schema": "resNFe_v1.01"
        }
      ],
      "erros": [ /* docZip nao classificavel: schema desconhecido ou decode falhou */
        {"nsu": "...", "schema": "...", "codigo": "PARSE_UNSUPPORTED_DFE",
         "erro": "..."}
      ],
      "results": [ /* view unificada (compat MapOne): documentos + resumos + erros */
        {"...campos...", "categoria": "COMPLETO"|"RESUMO"|"ERRO"}
      ],
      "cert_fonte": "inline_base64",
      "duracao_ms": 1250
    }

**RESUMO x COMPLETO** — a SEFAZ pode devolver, no mesmo lote:

- COMPLETO (`procNFe_v4.00`, `procCTe_v4.00`) — XML fiscal integral; entra em `documentos`.
- RESUMO (`resNFe_v1.01`, `resCTe_v1.00`, `resEvento_v1.01`) — apenas metadados
  (chave, emit, dhEmi, valor); entra em `resumos` com `codigo=RESUMO_DFE_RECEBIDO`.
  MapOne deve persistir como **pendencia operacional**, nao como documento fiscal.
  Quando o COMPLETO ficar disponivel, o mesmo NSU (ou posterior) virá com
  `procNFe`/`procCTe` — a vertical fecha a pendencia.

**RESUMO nao e erro de layout** — nao vira `PARSE_UNSUPPORTED`.

Erros controlados (nunca traceback HTML):

- 400 CERT_NAO_CONFIGURADO, CERT_BASE64_INVALIDO, CERT_ABERTURA_FALHOU,
      CERT_CNPJ_DIVERGENTE, CERT_SEM_CNPJ, CERT_INVALIDO, CERT_ENV_INVALIDO
- 400 CNPJ_INVALIDO, TIPO_NAO_SUPORTADO, PAYLOAD_INVALIDO
- 403 FISCALONE_PRODUCAO_BLOQUEADA (flags duplas)
- 502 SEFAZ_INDISPONIVEL, SEFAZ_HTTP_ERRO, SEFAZ_XML_INVALIDO, TLS_ERRO
- 500 ERRO_INTERNO

Cooldown recomendado (segundos): SEFAZ cStat 656 → 3900 · cStat 137 → 3600 · 138 → 0.
A vertical decide como persistir/repeitar.

## Certificado A1

Em transito, nunca em repouso. Fontes aceitas, em ordem:

1. `cert_pfx_base64` + `cert_password` no payload da requisicao (padrao producao)
2. `FISCALONE_CERT_PFX_BASE64` + `FISCALONE_CERT_PASSWORD` no env (teste controlado)
3. `FISCALONE_CERT_PFX_PATH` + `FISCALONE_CERT_PASSWORD` no env (teste controlado)
4. `GOV_CERT_PATH` + `GOV_CERT_PASSWORD` (compat legado)

Regras:

- Bundle descartado apos a chamada (`cert_provider.wipe`)
- PEM temporario: chmod 600, unlink imediato apos `ssl.load_cert_chain`
- CNPJ ICP-Brasil embutido no cert precisa bater com `cnpj_tenant`
- Nunca loga senha, base64, PFX, PEM, token ou xml_bruto

## Schemas por tipo (2026-07-12)

Contratos TypedDict em `schemas/`:
- `nfe_schema.NFeDoc`  — chNFe (44), CNPJ_emit/dest, vNF/vICMS/vIPI, cStat/xMotivo
- `cte_schema.CTeDoc`  — chCTe (44), vTPrest, modal (rodo/aereo/aqua/ferro/duto/multi)
- `nfse_schema.NFSeDoc` — numeroNfse (livre), CNPJ_prestador, valorServicos, `nsu` **sem zfill**
- `envelope_lote.EnvelopeLote` — `status_lote` + contadores (recebidos/processados/persistidos/duplicados/resumos/eventos/erros)

`status_xml`: COMPLETO · RESUMO · EVENTO · FALHA_PROCESSAMENTO · RECEBIDA
`import_origin`: fiscalone_gov_fetch · fiscalone_sefaz · fiscalone_upload · fiscalone_nfse_adn · fiscalone_email · fiscalone_reparse
`status_lote`: SUCESSO_TOTAL · SUCESSO_PARCIAL · FALHA_TOTAL · SEM_DOCUMENTO

## NSU por provider (definitivo)

`services/nsu_utils.normalizar_nsu(provider, doc_type, nsu)`:
- `sefaz` / `fiscalone_sefaz` → **zfill(15)**
- `adn_nfse` / `fiscalone_nfse_adn` → **string livre** (NUNCA zfill)
- desconhecido → `ValueError` (erro controlado por item; nao quebra lote)

## Testes

    pytest tests/          # 46/46 verde

## ADRs

- MAP-0017 — FiscalOne Gateway Gov (MapOne)
- ADR-0028 — Fronteira fiscal RLogix-wide (CtrlOne, a publicar)
- ADR-0034 — Gateway DFe (busca ativa)
- ADR-0035 — FiscalOne sem persistencia propria (gateway puro)
