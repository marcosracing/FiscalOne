# Handoff — 2026-07-11 · FiscalOne produção liberada apenas para DFe recebido

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## Contexto

Cadeia validada:

- CtrlOne = cadastro mestre + metadados
- MapOne = cofre operacional do certificado A1 (Racing carregado)
- FiscalOne = gateway stateless (aceita cert em memoria por payload)
- Emissao fiscal permanece bloqueada por design

Smoke tecnico anterior chegou ao FiscalOne mas parou por 3 flags de
producao desligadas. Este handoff registra a liberacao apenas para DFe
recebido.

## Alteracoes

### `app.py`

- Boot passa a chamar `load_dotenv()` (import via `python-dotenv`, ja em
  `requirements.txt`). Assim o `.env` do FiscalOne e efetivo sem exigir
  export manual das vars.

### `.env` (nao commitado)

- Ligadas as tres flags:
  - `FISCALONE_ENABLE_PRODUCAO=1`
  - `MAPONE_FISCAL_PRODUCAO_READY=1`
  - `FISCALONE_DFE_RECEBIDO_ONLY=1`
- **Nao** foram adicionados `FISCALONE_CERT_PFX_PATH`,
  `FISCALONE_CERT_PFX_BASE64`, `GOV_CERT_PATH` nem `GOV_CERT_PASS`.
  Certificado continua vindo do MapOne por payload (`cert_pfx_base64`).

### `docs/manual-tecnico-FiscalOne.md`

- Nota "Liberacao producao DFe recebido (2026-07-11)" no capitulo 8.

## Smoke de seguranca — FiscalOne standalone (executado)

Reiniciado o FiscalOne local (porta 5002) apos load_dotenv + flags ligadas.

| Cenario | Esperado | Observado |
|---|---|---|
| `GET /fiscal/health` | producao_bloqueada=false, flags=3xtrue, emissao_bloqueada_por_design=true, gov_fetch_dfe=true, emissao_ativa=false | OK |
| `POST /fiscal/cte` | 403 EMISSAO_BLOQUEADA | OK |
| `POST /fiscal/gov/fetch` sem cert | 400 CERT_NAO_CONFIGURADO (JSON) | OK |
| `POST /fiscal/gov/fetch` tipo=mdfe | 400 TIPO_NAO_SUPORTADO (JSON) | OK |
| `POST /fiscal/gov/fetch` payload vazio | 400 PAYLOAD_INVALIDO (JSON) | OK |
| Log stdout | sem PFX/PEM/senha/base64/token | OK (grep vazio) |
| `GOV_CERT_*` acionado | nao — `.env` mantem vazio, cert vem do MapOne | OK |

Nenhum HTML 500. Nenhum segredo em log. `GOV_CERT_*` inativo.

## Fase 5 (smoke integrado com MapOne) — pendente de execucao pelo operador

Nao foi executado nesta sessao porque exige o MapOne rodando + rota
`POST /fiscal/api/buscar-dfe` do MapOne. Sugestao:

    curl -s -X POST http://<mapone>/fiscal/api/buscar-dfe \
      -H 'Content-Type: application/json' \
      -d '{"company_id": <racing_id>, "tipo": "nfe", "ambiente": "producao"}'

Cenarios aceitaveis:
- SEFAZ retorna documentos completos.
- SEFAZ retorna somente resumos.
- SEFAZ retorna sem novos documentos (cStat 137, cooldown ~3600s).
- SEFAZ retorna cooldown/recomendacao de espera (cStat 656, ~3900s).

Nao aceitaveis (falha de gate):
- FISCALONE_PRODUCAO_BLOQUEADA — indica que uma das 3 flags nao foi lida
- CERT_NAO_CONFIGURADO — indica MapOne nao enviou `cert_pfx_base64`
- Uso de `GOV_CERT_*` — nunca deveria acontecer com cert por payload
- Emissao desbloqueada — impossivel pelo guard central
- HTML 500 — impossivel pelo `errorhandler(Exception)`
- Segredo em log — verificar `grep -E 'PFX|PEM|password|senha|base64|BEGIN'` no stdout

## Fase 6 (validacoes pos-smoke em tabelas MapOne) — a executar

Do lado MapOne, verificar apos o smoke:

| Tabela | Verificar |
|---|---|
| `op_gov_cooldown` | `ultimo_nsu`, `proxima_consulta`, `atualizado_em` |
| `op_fiscal_xml` | total antes/depois; docs com `import_origin='fiscalone_sefaz'` |
| `op_fiscal_log` | resumos DFe pendentes |

Do lado FiscalOne (ja verificado nesta sessao):
- logs sem PFX/senha/base64 — OK
- nenhum certificado persistido — OK (`op_fiscal_xml` inexistente no FiscalOne)

## Riscos

- `.env` local mantem `GOV_CERT_*` vazios apenas por compat. Recomendo
  remover em phase futura para evitar sinalizar suporte a fallback.
- `python-dotenv` e opcional (import em try/except). Se removido do
  requirements, o .env deixaria de ser lido silenciosamente. Manter
  no requirements como dependencia obrigatoria.

## Pendencias

- Fase 5 — smoke integrado com MapOne (operador).
- Fase 6 — auditoria das tabelas MapOne pos-smoke.
- Cofre KMS/Vault para cert no MapOne (pendencia arquitetural anterior).
