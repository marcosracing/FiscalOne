# Manual Tecnico — FiscalOne

**Versao:** 0.5.0 · **Data:** 2026-07-09
**Arquitetura:** ADR-0028, ADR-0034, ADR-0035

## 1. Papel do FiscalOne no ecossistema RLogix

FiscalOne e um **gateway tecnico fiscal**. Nao interpreta dados, nao decide regra
operacional, nao persiste nada.

    MapOne         → persiste e governa o dado operacional (fonte da verdade)
    CtrlOne        → interpreta os dados fornecidos pelas verticais
    FiscalOne      → gateway: assina, consulta, parseia e devolve o resultado

Toda persistencia (XML raw, NSU, cooldown, cert, protocolo, evento) e
responsabilidade da vertical. FiscalOne mantem o zero-persistencia da ADR-0035.

## 2. Modulos

    /providers/sefaz_provider.py     GovProvider real: gov_fetch(payload, trace_id)
    /providers/focusnfe_provider.py  Stub — implementar quando necessario
    /services/cert_provider.py       Resolve cert A1 por requisicao, em memoria
    /services/dfe_fetch_service.py   Uma consulta NFeDistDFeInteresse/CTeDistDFeInteresse
    /xml_parser.py                   Parseia NF-e/CT-e/MDF-e/NFS-e (recebidos)
    /app.py                          HTTP: /fiscal/health, /documents/import, /gov/fetch

## 3a. RESUMO x COMPLETO (Distribuicao DFe)

Cada `docZip` retornado pela SEFAZ na `NFeDistDFeInteresse` /
`CTeDistDFeInteresse` pode ser um de tres layouts:

| Categoria | Schema tipico            | Conteudo             | Alocado em      |
|-----------|--------------------------|----------------------|-----------------|
| COMPLETO  | procNFe_v4.00, procCTe_v4.00 | XML fiscal integral | `documentos[]`  |
| RESUMO    | resNFe_v1.01, resCTe_v1.00, resEvento_v1.01 | so metadados | `resumos[]`     |
| ERRO      | schema desconhecido / decode falhou | -                    | `erros[]`       |

**Regras:**

- RESUMO nao e erro de layout. NAO retornar `PARSE_UNSUPPORTED`.
- RESUMO retorna `ok:false, codigo:"RESUMO_DFE_RECEBIDO", status_xml:"RESUMO"`,
  com chave, emit_cnpj, emit_nome, dh_emi, valor_total e (quando presente)
  cSitNFe/cSitCTe, tpNF/tpCTe, digVal.
- Um COMPLETO retorna `ok:true, status_xml:"COMPLETO"`.
- Cada resposta traz `documentos + resumos + erros + results` (results unificado
  para compatibilidade com MapOne, sempre com `categoria` e `status_xml`).

**Persistencia (MapOne):**

- COMPLETO → `op_fiscal_xml` como documento fiscal oficial (xml_bruto + hash).
- RESUMO → tabela de pendencias operacionais (ex.: `op_dfe_resumo_pendente`)
  com chave + emit + valor. Nao alimenta `op_fiscal_xml` como documento
  completo. Quando o COMPLETO vier em NSU posterior, fecha a pendencia.
- ERRO → registrar em log/incidente; nao bloquear o drain do NSU.

## 3. POST /fiscal/gov/fetch — fluxo interno

    Vertical (MapOne)
        │  POST /fiscal/gov/fetch  (payload com cert em base64)
        ▼
    app.py :: gov_fetch
        ├─ _producao_bloqueada?  → 403 controlado
        ├─ valida payload (cnpj_tenant, tipo, ambiente)
        └─ SefazProvider.gov_fetch(payload, trace_id)
                ├─ cert_provider.resolve_cert
                │       ├─ carrega PFX (inline_base64 ou env)
                │       ├─ PFX → (cert_pem, key_pem) via pkcs12
                │       └─ valida CNPJ ICP-Brasil vs cnpj_tenant
                ├─ dfe_fetch_service.fetch_dfe
                │       ├─ TLS mTLS: temp PEM chmod 600, unlink imediato
                │       ├─ POST SOAP NFeDist/CTeDist (uma pagina)
                │       ├─ parse resposta (cStat/xMotivo/ultNSU/maxNSU)
                │       ├─ cStat 138: descompacta docZip → NF-e/CT-e XML
                │       └─ xml_parser.parse_xml para cada documento
                └─ cert_provider.wipe (zera bytes em memoria)
        ▼
    Envelope JSON: cstat, xmotivo, ultimo_nsu, max_nsu,
                   cooldown_recomendado_seg, documentos[]

## 4. Certificado A1

Fontes aceitas, em ordem:

  1. `cert_pfx_base64` + `cert_password` no payload  (padrao producao)
  2. `FISCALONE_CERT_PFX_BASE64` + `FISCALONE_CERT_PASSWORD` (env — teste controlado)
  3. `FISCALONE_CERT_PFX_PATH`   + `FISCALONE_CERT_PASSWORD` (env — teste controlado)
  4. `GOV_CERT_PATH` + `GOV_CERT_PASSWORD` (compat legado)

- **Nunca:** disco permanente, banco proprio, log de segredo.
- **Integridade:** CNPJ ICP-Brasil (SAN OID 2.16.76.1.3.3 ou CN "RAZAO:CNPJ")
  precisa bater com `cnpj_tenant`. Se divergir → CERT_CNPJ_DIVERGENTE.
- **PEM temporario:** gravado com chmod 600, apagado em `finally` logo apos
  `ssl.load_cert_chain` (chave privada nao permanece em disco durante a chamada de rede).
- **Wipe:** apos a chamada, bytes do bundle sao sobrescritos com `\x00`.

## 5. Cooldown SEFAZ

FiscalOne **recomenda** cooldown, nunca persiste:

    cStat 656 (consumo indevido) → cooldown_recomendado_seg = 3900  (1h05)
    cStat 137 (sem novos docs)   → cooldown_recomendado_seg = 3600  (1h)
    cStat 138 (docs encontrados) → cooldown_recomendado_seg = 0
    cStat 589 (NSU > maxNSU)     → cooldown_recomendado_seg = 1     (reset)

A vertical (MapOne) e quem grava `proxima_consulta_utc` na sua tabela
(analogo ao `dfe_distribuicao_estado` do CtrlOne).

## 6. Ambientes SEFAZ

    ambiente="producao"    → tpAmb=1 · www1.nfe.fazenda.gov.br / www1.cte.fazenda.gov.br
    ambiente="homologacao" → tpAmb=2 · hom1.nfe.fazenda.gov.br / hom1.cte.fazenda.gov.br

## 7. Trava de producao — 3 flags + emissao bloqueada por design

Producao liberada APENAS para DFe recebido. Requer as **tres** flags em `1`:

    FISCALONE_AMBIENTE=producao
    FISCALONE_ENABLE_PRODUCAO=1
    MAPONE_FISCAL_PRODUCAO_READY=1
    FISCALONE_DFE_RECEBIDO_ONLY=1

Enquanto qualquer flag estiver ausente/`0`, `/fiscal/gov/fetch` retorna
**403 FISCALONE_PRODUCAO_BLOQUEADA** com `flags_faltantes[]` no envelope.

**Emissao permanece bloqueada por design (`bloquear_emissao`)** mesmo com
as tres flags ligadas. As rotas abaixo sempre devolvem
**403 EMISSAO_BLOQUEADA**, independente do ambiente/flags:

    POST   /fiscal/nfe
    POST   /fiscal/cte
    POST   /fiscal/mdfe
    DELETE /fiscal/nfe/<chave>
    DELETE /fiscal/cte/<chave>
    POST   /fiscal/nfe/<chave>/inutilizar
    POST   /fiscal/nfe/<chave>/cce
    POST   /fiscal/mdfe/<chave>/encerrar
    POST   /fiscal/mdfe/<chave>/condutor

Envelope de bloqueio:

    {
      "ok": false,
      "codigo": "EMISSAO_BLOQUEADA",
      "erro": "FiscalOne liberado apenas para DFe recebido; emissao fiscal permanece bloqueada.",
      "escopo_liberado": "dfe_recebido_apenas",
      "trace_id": "fo-..."
    }

## 8. Contrato do envelope de erro

Nenhum caminho de erro devolve HTML/traceback. Sempre:

    {
      "ok": false, "trace_id": "fo-...",
      "codigo": "<codigo controlado>",
      "erro": "<mensagem curta, sem segredos>"
    }

Codigos:

    PAYLOAD_INVALIDO, CNPJ_INVALIDO, TIPO_NAO_SUPORTADO
    CERT_NAO_CONFIGURADO, CERT_BASE64_INVALIDO, CERT_ABERTURA_FALHOU,
    CERT_INVALIDO, CERT_ENV_INVALIDO, CERT_CNPJ_DIVERGENTE,
    CERT_SEM_CNPJ, CERT_FONTE_NAO_SUPORTADA
    SEFAZ_INDISPONIVEL, SEFAZ_HTTP_ERRO, SEFAZ_XML_INVALIDO, TLS_ERRO
    FISCALONE_PRODUCAO_BLOQUEADA, ERRO_INTERNO

Blindagem operacional (2026-07-10):

- `app.py` possui `@app.errorhandler(Exception)` para transformar exceções
  não tratadas das rotas fiscais em JSON `ERRO_INTERNO`;
- `_log_stdout()` nunca pode derrubar a API. Se o stdout do processo estiver
  fechado/quebrado (ex.: terminal encerrado, daemon transitório ou pipe
  inválido), o erro de log é absorvido;
- `/fiscal/gov/fetch` deve continuar devolvendo JSON mesmo quando produção está
  bloqueada, payload está inválido ou falta certificado.

Liberação producao DFe recebido (2026-07-11):

- `.env` do FiscalOne agora carrega automaticamente via `python-dotenv`
  (importado no boot do `app.py`).
- 3 flags ligadas em producao (via `.env`):
  `FISCALONE_ENABLE_PRODUCAO=1`, `MAPONE_FISCAL_PRODUCAO_READY=1`,
  `FISCALONE_DFE_RECEBIDO_ONLY=1`.
- Certificado A1 continua vindo em memoria do MapOne no payload
  (`cert_pfx_base64`). `GOV_CERT_PATH/PASSWORD` permanecem no `.env`
  apenas por compat — VAZIOS, nunca acionados.
- Emissao/cancelamento/inutilizacao/CC-e/MDF-e continuam bloqueados
  por design (`bloquear_emissao`), independente das flags.

## 9. Logging

Log tecnico estruturado em **stdout** (ADR-0035). A vertical coleta o que
precisar persistir em sua tabela de log fiscal.

Campos: `ts, service, operacao, resultado, trace_id, source, cnpj, doc_type,
chave, duracao_ms, erro`. Segredos (PFX/PEM/senha/token) nunca sao logados.

O stdout é observabilidade, não dependência funcional. Falha de escrita no log
não pode mudar o HTTP de saída nem gerar HTML 500 para MapOne.

## 10. O que NAO foi migrado do CtrlOne (proposital)

- Persistencia global em `data/nsu_state.json`
- Tabela `dfe_distribuicao_estado` (por empresa/tipo/ambiente)
- Tabela `company_certificates` + `credential_crypto`
- Grava disco de XML raw
- Consulta banco `companies`
- Import automatico via `import_docs`
- Assinatura de eventos emitidos
- CLI standalone

Essas responsabilidades pertencem a MapOne / CtrlOne — o FiscalOne apenas
devolve os dados. Ver secao 11.

## 11. Integracao com MapOne — pendencias

O MapOne deve, apos ligar-se ao FiscalOne:

1. Recuperar cert A1 do seu store (`company_certificates` equivalente) e
   codificar em base64 antes de chamar `/fiscal/gov/fetch`.
2. Carregar `ultimo_nsu` de sua propria tabela `op_dfe_estado` (por
   `company_id + tipo + ambiente`).
3. Para cada `documento` retornado, gravar `op_fiscal_xml` (xml_bruto + hash).
4. Atualizar `ultimo_nsu`, `max_nsu`, `proxima_consulta_utc` = agora +
   `cooldown_recomendado_seg`, `ultimo_cstat` na sua tabela de estado.
5. Repetir a chamada ate `cstat != 138` (drain).

Enquanto MapOne nao estiver pronto, `FISCALONE_ENABLE_PRODUCAO` continua false.

## 12. Rodar e testar local

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    FISCALONE_AMBIENTE=homologacao python3 app.py

    curl -s http://localhost:5002/fiscal/health | python3 -m json.tool

    curl -X POST http://localhost:5002/fiscal/gov/fetch \
      -H "Content-Type: application/json" \
      -d '{"cnpj_tenant":"07219398000109","ambiente":"homologacao","tipo":"nfe","ultimo_nsu":"0","cert_source":"env"}'
