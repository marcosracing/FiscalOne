# Handoff — 2026-07-09 · Producao FiscalOne liberada APENAS para DFe recebido

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## Escopo desta liberacao

Producao autorizada apenas para **busca/recepcao de XML DFe recebidos**
(NFeDistDFeInteresse / CTeDistDFeInteresse). Toda emissao fiscal permanece
bloqueada por design nesta fase.

## Flags obrigatorias (todas em `1` para liberar producao)

    FISCALONE_ENABLE_PRODUCAO=1     # liberacao geral do FiscalOne
    MAPONE_FISCAL_PRODUCAO_READY=1  # MapOne testado e pronto
    FISCALONE_DFE_RECEBIDO_ONLY=1   # reafirma escopo restrito

Ausencia de qualquer flag em producao → **403 FISCALONE_PRODUCAO_BLOQUEADA**,
com `flags_faltantes[]` no envelope.

## Bloqueio absoluto de emissao (independe das flags)

Guard central `bloquear_emissao(operacao, trace_id, source_system)` em app.py
sempre retorna **403 EMISSAO_BLOQUEADA**. Aplicado em:

    POST   /fiscal/nfe                    (defensivo)
    POST   /fiscal/cte
    POST   /fiscal/mdfe
    DELETE /fiscal/nfe/<chave>            (defensivo)
    DELETE /fiscal/cte/<chave>
    POST   /fiscal/nfe/<chave>/inutilizar (defensivo)
    POST   /fiscal/nfe/<chave>/cce        (defensivo)
    POST   /fiscal/mdfe/<chave>/encerrar
    POST   /fiscal/mdfe/<chave>/condutor

Nota: as quatro rotas marcadas "defensivo" foram adicionadas para bloquear
tentativas futuras de emissao NF-e caso alguem introduza handler sem checar
o guard central.

## Certificado A1 — fontes aceitas

Prioridade decrescente:

  1. `cert_pfx_base64` + `cert_password` no payload da requisicao
  2. `FISCALONE_CERT_PFX_BASE64` + `FISCALONE_CERT_PASSWORD` (env)
  3. `FISCALONE_CERT_PFX_PATH`   + `FISCALONE_CERT_PASSWORD` (env)
  4. `GOV_CERT_PATH` + `GOV_CERT_PASSWORD` (compat legado)

Sem cert → **400 CERT_NAO_CONFIGURADO**.
PFX invalido → **400 CERT_ABERTURA_FALHOU**.
CNPJ cert ≠ tenant → **400 CERT_CNPJ_DIVERGENTE**.
`FISCALONE_CERT_PFX_PATH` apontando para arquivo inexistente → **400 CERT_ENV_INVALIDO**.

Regras:
- Nunca loga senha, base64, PFX, PEM, token ou xml_bruto
- PEM temporario `chmod 600`, unlink em `finally`
- CNPJ ICP-Brasil no cert precisa bater com `cnpj_tenant`

## Envelope /fiscal/health

Agora expoe estado observavel das flags:

    {
      "escopo_liberado": "dfe_recebido_apenas",
      "flags_producao": {
        "FISCALONE_ENABLE_PRODUCAO":    true|false,
        "MAPONE_FISCAL_PRODUCAO_READY": true|false,
        "FISCALONE_DFE_RECEBIDO_ONLY":  true|false
      },
      "flags_producao_faltantes": [...],
      "producao_bloqueada": true|false,
      "emissao_bloqueada_por_design": true,
      "capacidade": { "gov_fetch_dfe": true, "emitir_*": false, ... }
    }

## Procedimento de teste controlado

    lsof -ti :5002 | xargs -r kill -9
    FISCALONE_AMBIENTE=producao \
    FISCALONE_ENABLE_PRODUCAO=1 \
    MAPONE_FISCAL_PRODUCAO_READY=1 \
    FISCALONE_DFE_RECEBIDO_ONLY=1 \
    FISCALONE_CERT_PFX_PATH=/caminho/e-cnpj.pfx \
    FISCALONE_CERT_PASSWORD=<senha> \
    python3 app.py

    curl -s http://localhost:5002/fiscal/health | python3 -m json.tool

    curl -X POST http://localhost:5002/fiscal/gov/fetch \
      -H "Content-Type: application/json" \
      -d '{"cnpj_tenant":"<14 digitos>","ambiente":"producao","tipo":"nfe","ultimo_nsu":"0","cert_source":"env"}'

Consulta real a SEFAZ so deve ser executada com autorizacao explicita do
operador. Sem autorizacao, testar apenas cenarios de erro controlado.

## Validacoes executadas

- `py_compile` em app.py, xml_parser.py, providers/*, services/* → OK
- **A. producao SEM flags:**
  - `/fiscal/gov/fetch` → 403 `FISCALONE_PRODUCAO_BLOQUEADA` (3 flags faltantes)
  - `POST /fiscal/cte` → 403 `EMISSAO_BLOQUEADA`
  - `POST /fiscal/mdfe` → 403 `EMISSAO_BLOQUEADA`
  - `DELETE /fiscal/cte/<chave>` → 403 `EMISSAO_BLOQUEADA`
  - `POST /fiscal/nfe/<chave>/inutilizar` → 403 `EMISSAO_BLOQUEADA`
  - `POST /fiscal/mdfe/<chave>/encerrar` → 403 `EMISSAO_BLOQUEADA`
- **B. producao COM 3 flags, sem cert:**
  - `/fiscal/health` → producao_bloqueada=false, escopo=dfe_recebido_apenas
  - `/fiscal/gov/fetch` → 400 `CERT_NAO_CONFIGURADO`
  - `/fiscal/gov/fetch` tipo=mdfe → 400 `TIPO_NAO_SUPORTADO`
  - `POST /fiscal/cte` → 403 `EMISSAO_BLOQUEADA` (mesmo com flags)
- **C. producao + FISCALONE_CERT_PFX_PATH invalido:**
  - `/fiscal/gov/fetch` → 400 `CERT_ENV_INVALIDO`

**Nao houve chamada real a SEFAZ.**

## Riscos

- `.env` local com PFX ainda depende de disciplina operacional. Cofre
  definitivo continua pendente.
- `GOV_TLS_INSECURE=1` existe para diagnostico; JAMAIS ligar em producao.
- Rotas defensivas de NF-e (`/fiscal/nfe`, DELETE, inutilizar, cce) foram
  adicionadas para prevenir emissao acidental futura — devem permanecer no
  guard central mesmo quando alguem adicionar um handler real.
- Emissao **nao esta implementada**, apenas bloqueada. Nao existe caminho de
  bypass no codigo.

## Pendencias

- Cofre definitivo para cert (KMS/Vault) — hoje o env local e teste controlado.
- MapOne precisa migrar seu store de cert equivalente para chamar o FiscalOne
  com `cert_pfx_base64` (padrao producao) e nunca via env.
- Endpoint chave-a-chave (NFeConsultaProtocolo) — futura Fase 2.
