# Handoff — 2026-07-09 · Gateway DFe migrado do CtrlOne

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## O que foi feito

Extraidas do `rlogix/gov_import.py` (CtrlOne) as dependencias tecnicas para o
FiscalOne executar busca ativa DFe (NFeDistDFeInteresse / CTeDistDFeInteresse)
como **gateway puro** — sem persistencia propria.

### Novos modulos

- `services/cert_provider.py` — resolve cert A1 em memoria por requisicao
- `services/dfe_fetch_service.py` — uma consulta SEFAZ + parse de docZip

### Modificados

- `providers/sefaz_provider.py` — implementa `gov_fetch(payload, trace_id)` real
- `app.py` — `POST /fiscal/gov/fetch` deixa de ser stub, retorna envelope JSON
- `.env.example` — comentarios sobre transito de cert e fallback env
- `README.md` — contrato do endpoint, capacidade atualizada
- `requirements.txt` — adiciona `certifi`

### Novos docs

- `docs/manual-tecnico-FiscalOne.md`
- `docs/adr/_handoff/2026-07-09-gateway-dfe-migrado-do-ctrlone.md` (este)

## O que **nao** foi migrado (proposital)

- Persistencia `data/nsu_state.json`
- Tabela `dfe_distribuicao_estado`
- Tabela `company_certificates` / `credential_crypto`
- Gravacao de XML em disco (`docs/sefaz/`)
- Consulta banco `companies`
- Import automatico via `import_docs`
- CLI standalone (`gov_import.py sefaz|baixar|nfse-nacional`)

## Regras arquiteturais preservadas

- **Zero persistencia** no FiscalOne (ADR-0035)
- Certificado A1 em **transito**, nunca em repouso — descartado ao fim da chamada
- PEM temporario `chmod 600`, unlink em `finally`
- **Nunca** loga cert, PFX, senha, PEM, token, ou xml_bruto
- **CNPJ do cert precisa bater com `cnpj_tenant`** — verificado por SAN OID 2.16.76.1.3.3
- Emissao, cancelamento, MDF-e, assinatura de eventos: **continuam bloqueados**
- Trava dupla de producao: `FISCALONE_ENABLE_PRODUCAO` + `MAPONE_FISCAL_PRODUCAO_READY`
- Erros sempre devolvem JSON controlado — nunca traceback HTML

## Validacoes executadas

- `python3 -m py_compile app.py providers/*.py services/*.py` — OK
- `GET /fiscal/health` — 200, ambiente=homologacao, producao_bloqueada=false
- `POST /fiscal/gov/fetch` sem cert → 400 `CERT_NAO_CONFIGURADO` (JSON controlado)
- `POST /fiscal/gov/fetch` payload vazio → 400 `PAYLOAD_INVALIDO`
- `POST /fiscal/gov/fetch` cnpj invalido → 400 `CNPJ_INVALIDO`
- `POST /fiscal/gov/fetch` tipo=mdfe → 400 `TIPO_NAO_SUPORTADO`
- `POST /fiscal/gov/fetch` base64 invalido → 400 `CERT_BASE64_INVALIDO`
- `POST /fiscal/gov/fetch` em ambiente=producao (padrao) → 403 `FISCALONE_PRODUCAO_BLOQUEADA`

**Nao houve chamada real a SEFAZ** — todos os testes cobrem apenas os caminhos
de validacao/erro. Consulta real depende de cert A1 valido + autorizacao do
operador + `FISCALONE_ENABLE_PRODUCAO=true`.

## Contrato do endpoint (resumo)

    POST /fiscal/gov/fetch
    Body:  cnpj_tenant, ambiente, tipo (nfe|cte), ultimo_nsu,
           cert_pfx_base64, cert_password, cert_source
    Resp:  ok, cstat, xmotivo, ultimo_nsu, max_nsu,
           cooldown_recomendado_seg, documentos[]

Documento devolvido:

    doc_type, chave, numero, emit_cnpj, emit_nome, dest_cnpj,
    dh_emi, valor_total, xml_bruto, xml_hash_sha256, parser_version,
    nsu, schema

## Pendencias — proxima fase

### Ligar MapOne ao FiscalOne

1. MapOne migra tabela `op_dfe_estado` (analoga a `dfe_distribuicao_estado`
   do CtrlOne) — por `company_id + tipo + ambiente`, com `ultimo_nsu`,
   `max_nsu`, `proxima_consulta_utc`, `ultimo_cstat`.
2. MapOne migra store de cert (`op_company_certificates` + `credential_crypto`
   equivalente), com decifra em memoria e chamada base64 ao FiscalOne.
3. MapOne cria orquestrador que chama `POST /fiscal/gov/fetch` recorrentemente
   ate `cstat != 138`, respeitando `cooldown_recomendado_seg`.
4. MapOne grava cada `documento` retornado em `op_fiscal_xml` (xml_bruto +
   hash + parser_version + nsu + schema).
5. Ligar Gerenciador Fiscal 50.1.1 ao MapOne (nao passa pelo FiscalOne).

### FiscalOne — futuro (Fase 2+)

- Adicionar tipo `nfse_nac` (ADN REST) — logica ja existente em `gov_import.py`
- Endpoint de status SEFAZ (`GET /fiscal/status/{uf}`) — atualmente stub
- Retentativas com backoff exponencial em falhas TLS/HTTP
- Testes unitarios com fixtures de resposta SEFAZ
- Assinatura de emissao (CT-e, MDF-e) — apos gates TMS

## Como testar (sem chamar SEFAZ)

    lsof -ti :5002 | xargs -r kill -9
    FISCALONE_AMBIENTE=homologacao python3 app.py &

    # health
    curl -s http://localhost:5002/fiscal/health | python3 -m json.tool

    # erros controlados
    curl -X POST http://localhost:5002/fiscal/gov/fetch \
      -H "Content-Type: application/json" \
      -d '{"cnpj_tenant":"07219398000109","ambiente":"homologacao","tipo":"nfe","ultimo_nsu":"0"}'
    # Esperado: 400 CERT_NAO_CONFIGURADO

## Como testar com SEFAZ (sob autorizacao explicita)

Precisa cert A1 valido:

    curl -X POST http://localhost:5002/fiscal/gov/fetch \
      -H "Content-Type: application/json" \
      -d @payload_com_cert_base64.json

Producao: liberar `FISCALONE_ENABLE_PRODUCAO=true` e
`MAPONE_FISCAL_PRODUCAO_READY=true`.
