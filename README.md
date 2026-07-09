# FiscalOne — Gateway Fiscal Técnico RLogix

Gateway fiscal técnico do ecossistema RLogix — **Fase 1 · DFe recebidos**.
Parseia NF-e/CT-e/MDF-e/NFS-e (XML e PDF) localmente. SEFAZ, emissao e busca ativa sao stubs.

## Capacidade atual (2026-07-09)

| Funcionalidade | Status | Observacao |
|---|---|---|
| `POST /fiscal/documents/import` — parse XML/PDF/ZIP | operacional | NF-e, CT-e, MDF-e, NFS-e (nacional/ABRASF), NFS-e PDF (Prefeitura de SP + generico); sem persistencia propria |
| `GET /fiscal/health` | operacional | Reporta fase, flags, capacidades e stubs ativos |
| Trava de producao (flags duplas) | operacional | Bloqueia toda operacao em prod sem flags |
| `POST /fiscal/gov/fetch` — busca SEFAZ/DFe ativo | stub | Aguarda Fase 2 / migracao gov_import.py |
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

## Trava de seguranca fiscal

O FiscalOne nasce em homologacao. Operacoes Gov.br, CT-e, MDF-e, cancelamento,
encerramento, inclusao de condutor e consultas SEFAZ ficam bloqueadas em producao
ate o MapOne estar exaustivamente testado.

Padrao obrigatorio:

    FISCALONE_AMBIENTE=homologacao
    FISCALONE_ENABLE_PRODUCAO=false
    MAPONE_FISCAL_PRODUCAO_READY=false

Para qualquer uso futuro em producao, as duas flags precisam ser liberadas
explicitamente e revisadas. MDF-e exige gates TMS antes de emissao: CIOT quando
aplicavel, VPO/Vale-Pedagio Obrigatorio, RNTRC/ANTT, seguro/averbacao, documentos
fiscais vinculados, veiculo e condutor validos.

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
| POST | /fiscal/gov/fetch | stub | Busca SEFAZ/DFe — nao implementada |
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

## ADRs

- MAP-0017 — FiscalOne Gateway Gov (MapOne)
- ADR-0028 — Fronteira fiscal RLogix-wide (CtrlOne, a publicar)
- ADR-0035 — FiscalOne sem persistencia propria (gateway puro)
