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

    /schemas/                              Contratos TypedDict por tipo (nfe/cte/nfse) + envelope_lote
    /providers/__init__.py                 GovProvider ABC — gov_fetch + consultar_dfe_nsu abstractmethods
    /providers/sefaz_provider.py           GovProvider real (dispatcher): gov_fetch(payload, trace_id)
    /providers/nfe_provider.py             Wrapper NF-e: parse_nfe + normalizar_nsu SEFAZ
    /providers/cte_provider.py             Wrapper CT-e: parse_cte + normalizar_nsu SEFAZ
    /providers/nfse_provider.py            Wrapper NFS-e: parse_nfse + normalizar_nsu ADN
    /providers/nfse_nacional_provider.py   ADN NFS-e Nacional por NSU (GET mTLS)
    /providers/focusnfe_provider.py        Stub JSON estruturado — PROVIDER_NAO_IMPLEMENTADO
    /services/cert_provider.py             Resolve cert A1 por requisicao, em memoria
    /services/nsu_utils.py                 normalizar_nsu(provider, doc_type, nsu) — regra definitiva
    /services/dfe_fetch_service.py         Rotea: nfe/cte → SEFAZ SOAP · nfse → ADN
    /xml_parser.py                         parse_nfe/parse_cte/parse_nfse (doc_type explicito)
    /app.py                                HTTP: /fiscal/health, /documents/import, /gov/fetch
    /tests/                                Suite pytest (46 testes)

## 2b. Schemas por tipo (2026-07-12)

`schemas/nfe_schema.py`, `cte_schema.py`, `nfse_schema.py` sao **TypedDicts**
(verificacao estatica). O envelope runtime pode ter campos opcionais adicionais,
mas os campos declarados nos TypedDicts sao contrato.

Constantes compartilhadas em `schemas/__init__.py`:

**`status_xml`** (Literal):
- `COMPLETO` — XML fiscal integral parseado
- `RESUMO` — DFe resumido (resNFe/resCTe/resEvento)
- `EVENTO` — procEventoNFe/procEventoCTe
- `FALHA_PROCESSAMENTO` — erro de parser/layout
- `RECEBIDA` — DFe recebida ainda nao processada (reservado)

**`import_origin`** (Literal, 6 valores aceitos):
- `fiscalone_gov_fetch` — parse invocado pelo /fiscal/gov/fetch
- `fiscalone_sefaz` — persistido no MapOne com origem SEFAZ
- `fiscalone_upload` — POST /fiscal/documents/import
- `fiscalone_nfse_adn` — NFS-e Nacional via ADN
- `fiscalone_email` — captacao por email (MapOne)
- `fiscalone_reparse` — reprocessamento manual (MapOne)

**`status_lote`** (Literal):
- `SUCESSO_TOTAL` — todos processados sem erro
- `SUCESSO_PARCIAL` — >=1 sucesso + >=1 erro
- `FALHA_TOTAL` — 0 sucesso e >=1 tentativa
- `SEM_DOCUMENTO` — SEFAZ/ADN devolveu 0 documentos

## 2a. NFS-e Nacional via ADN (inicio operacional 2026-07-01)

Provider dedicado (`providers/nfse_nacional_provider.py`) — GET REST mTLS por NSU.

Endpoint:

    GET https://<HOST_ADN>/contribuintes/DFe/{NSU}
    Header: Accept: application/json

Hosts por ambiente:

    ambiente="producao"    → adn.nfse.gov.br                    (ambiente_adn: producao)
    ambiente="homologacao" → adn.producaorestrita.nfse.gov.br   (ambiente_adn: producao_restrita)

Resposta ADN esperada (JSON):

    { "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS" | "...",
      "UltimoNSU": "...", "MaxNSU": "...",
      "LoteDFe": [ { "NSU": "...", "ArquivoXml": "<gzip+base64>", ... } ] }

Trata HTTP:
- 200 c/ LoteDFe → documentos NFS-e completos (parseados via `xml_parser`)
- 200 c/ LoteDFe vazio, 204, 404 → `status="SEM_DOCUMENTO"` (cooldown 3600s)
- 403 → `NFSE_ADN_AUTH_ERRO` (cert nao habilitado, cooldown 3600s)
- outros → `NFSE_ADN_HTTP_ERRO` (cooldown 900s)

`data_inicio` no payload e apenas metadado operacional — o FiscalOne nao filtra
por data; ADN e por NSU. Corte real por data e responsabilidade do MapOne.

## 2c. Regra definitiva de NSU por provider (2026-07-12)

Toda normalizacao de NSU passa por `services/nsu_utils.normalizar_nsu`.
`zfill` cego foi eliminado.

    normalizar_nsu(provider, doc_type, nsu) -> str

| Provider (`provider`)                        | Regra          | Exemplo              |
|----------------------------------------------|----------------|----------------------|
| `sefaz` / `fiscalone_sefaz`                  | zfill(15)      | `"123"` → `"000000000000123"` |
| `adn_nfse` / `fiscalone_nfse_adn`            | string livre   | `"555"` → `"555"`, `""` → `"0"` |
| qualquer outro                               | `ValueError`   | (erro controlado por item; nao quebra lote)  |

Motivos:
- SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse exigem NSU 15 digitos
  zero-padded (spec XSD).
- ADN NFS-e Nacional aceita NSU como string livre (path REST
  `GET /contribuintes/DFe/{NSU}`); NUNCA aplicar zfill.

## 2d. GOV_TLS_INSECURE — comportamento

`GOV_TLS_INSECURE=1` desabilita verificacao de certificado servidor no
handshake TLS. Usado APENAS para diagnostico local.

Boot:
- `logger.warning` emitido: "GOV_TLS_INSECURE=1 ativo — verificacao TLS
  DESABILITADA. USO PROIBIDO EM PRODUCAO."

Health:
- `GET /fiscal/health` retorna:
  ```json
  { "tls_insecure": true,
    "tls_warning": "GOV_TLS_INSECURE ativo — uso proibido em producao." }
  ```

`FISCAL_PROVIDER=focusnfe`:
- `logger.warning` no boot: "stub. Todas as chamadas retornarao PROVIDER_NAO_IMPLEMENTADO."

## 2e. Envelope /fiscal/gov/fetch — `acao` e `nsu_avancou` (2026-07-13)

Contrato final para o MapOne decidir se atualiza NSU no CtrlOne.

### Tabela — cStat SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse

| cStat | Significado                              | acao          | nsu_avancou | cooldown_seg |
|-------|------------------------------------------|---------------|-------------|--------------|
| 138   | Documento(s) localizado(s)               | DOCUMENTOS    | true        | 0            |
| 137   | Nenhum documento localizado              | SEM_DOCUMENTO | true        | 3600         |
| 656   | Consumo Indevido (SEFAZ bloqueou)        | REJEITADO     | false       | 3900         |
| 589   | NSU consultado > maxNSU                  | REJEITADO     | false       | 1            |
| outros| qualquer outra rejeicao                  | REJEITADO     | false       | -            |

### Tabela — NFS-e Nacional (ADN, sem cStat)

| status                    | acao          | nsu_avancou |
|---------------------------|---------------|-------------|
| DOCUMENTOS_LOCALIZADOS    | DOCUMENTOS    | true        |
| SEM_DOCUMENTO             | SEM_DOCUMENTO | true        |
| NFSE_ADN_AUTH_ERRO        | ERRO          | false       |
| NFSE_ADN_HTTP_ERRO        | ERRO          | false       |
| NFSE_ADN_XML_INVALIDO     | ERRO          | false       |

### Tabela — Erros tecnicos FiscalOne

Qualquer codigo tecnico (CERT_*, SEFAZ_*, TLS_ERRO, PROVIDER_NAO_IMPLEMENTADO,
ERRO_INTERNO, PAYLOAD_INVALIDO, CNPJ_INVALIDO, TIPO_NAO_SUPORTADO) sempre
resulta em `acao=ERRO` + `nsu_avancou=false`.

### Regra do MapOne

    if envelope["nsu_avancou"]:
        # atualiza op_gov_cooldown.ultimo_nsu no CtrlOne
    else:
        # mantem ultimo_nsu anterior; respeita cooldown_recomendado_seg

**Nunca** avancar NSU quando `nsu_avancou=false`. Isso evita perda de janela
de retentativa em cstat=656 (consumo indevido).

### Campos adicionais no envelope

- `acao`: DOCUMENTOS | SEM_DOCUMENTO | REJEITADO | ERRO
- `nsu_avancou`: bool
- `ultimo_nsu_antes`: str (eco do payload — permite auditoria)
- `ultimo_nsu`: str (retornado pela SEFAZ/ADN)
- `max_nsu`: str
- `cstat` / `xmotivo` (SEFAZ) ou `status` / `status_processamento` (ADN)

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

## 9. Deploy Mac → VM

FiscalOne deve ser implantado como serviço independente na VM, escutando apenas
em `127.0.0.1:5002`. O MapOne chama o gateway por `FISCALONE_URL`, com padrão
`http://127.0.0.1:5002`.

Script versionado:

```bash
cd ~/Documents/FiscalOne
scripts/deploy_fiscalone_vm.sh
```

O deploy:

- exige worktree limpo;
- sincroniza o código para `/home/ubuntu/FiscalOne`;
- não copia `.env`, `.venv`, `.git`, logs ou segredos;
- recria/atualiza `.venv` e instala `requirements.txt`;
- cria `.env` operacional sem certificado;
- instala/reinicia `fiscalone.service`;
- valida `GET /fiscal/health`.

Flags da VM para DFe recebido em produção:

```env
FISCALONE_AMBIENTE=producao
FISCALONE_ENABLE_PRODUCAO=1
MAPONE_FISCAL_PRODUCAO_READY=1
FISCALONE_DFE_RECEBIDO_ONLY=1
FISCAL_PROVIDER=sefaz
```

Certificado A1 não mora no FiscalOne. O bundle vem em trânsito por payload do
MapOne, a partir do cofre da vertical, e é descartado ao fim da requisição.

## 10. Contrato `results[]` DFe

A partir de 2026-07-11, cada item em `results[]` traz `ok` explícito:

- `COMPLETO`: `ok=true`, `status_xml=COMPLETO`, pode persistir em `op_fiscal_xml`.
- `RESUMO`: `ok=true`, `status_xml=RESUMO`, `codigo=RESUMO_DFE_RECEBIDO`; é
  pendência de XML completo, não erro.
- `ERRO`: `ok=false`, `status_xml=ERRO`, deve ir para log/incidente.

Isso evita que consumidores tratem resumos ou documentos completos como erro
apenas porque envelopes antigos não traziam `ok` no item unificado.

## 13. NFS-e Nacional ADN — classificação de eventos (2026-07-11)

O provider `providers/nfse_nacional_provider.py` não deve forçar todo XML
baixado do ADN como `doc_type=nfse`.

Regra atual:

```text
doc_type = parsed.doc_type || parsed.type || nfse
type     = parsed.type || doc_type
```

Motivo: o ADN pode retornar XMLs de evento dentro do fluxo NFS-e. Esses XMLs
possuem raiz `<evento xmlns="http://www.sped.fazenda.gov.br/nfse">` e não são
documento fiscal recebido para acervo da vertical.

Com a correção:

- NFS-e completa continua retornando `doc_type=nfse`.
- Evento NFS-e retorna `doc_type=evento`.
- O MapOne registra evento em `op_dfe_evento` e não grava falso documento em
  `op_fiscal_xml`.
- FiscalOne continua sem persistência própria e sem emissão fiscal ativa.

## 14. Requisições simultâneas multiempresa (2026-07-11)

O FiscalOne deve atender chamadas de múltiplas verticais/tenants sem depender
de sessão de usuário e sem persistir estado próprio.

No ambiente local/VM simples, o `app.py` sobe Flask com `threaded=True` para
permitir que o MapOne execute ciclos multiempresa com paralelismo controlado.

Regras preservadas:

- cada requisição recebe certificado A1 em trânsito pelo payload;
- certificado, senha, base64 e XML completo não são gravados em log;
- emissão fiscal continua bloqueada por design;
- persistência de XML, eventos, NSU e cooldown continua sendo da vertical
  consumidora, hoje MapOne.

Para ambiente definitivo, o mesmo princípio deve ser mantido em WSGI/gunicorn:
mais de um worker/thread pode processar DFe recebido, mas sem criar estado fiscal
local no FiscalOne.

---

## Fase 2-prep · FocusNFeProvider (2026-07-17)

Preparação de infraestrutura para receber Focus NFe como provider de recebimento de documentos, sem ativar integração HTTP real nesta fase.

**Escopo:**
- `services/nsu_utils.py` — `normalizar_nsu("focusnfe" | "fiscalone_focusnfe", doc_type, versao)` preserva `versao` (int/string) sem zfill; `None`/vazio → `"0"`.
- `providers/focusnfe_provider.py` — `EmissaoProibida(RuntimeError)` bloqueia `emitir_cte`/`emitir_mdfe`; `_masked_token()` mascara para logs (nunca valor completo); `__init__` lê `FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL` (sem `/` final), `FOCUSNFE_TIMEOUT` (fallback 30). `_require_token()` é fail-fast local para Fase 2.
- `schemas/__init__.py` — `ImportOrigin` inclui `"fiscalone_focusnfe"`.
- `schemas/nfe_schema.py` — `NFeDocOpcional` inclui `versao`, `raw_json_focus`, `danfe_sha256`, `danfe_fonte`.
- `.env.example` — `FOCUSNFE_TOKEN`, `FOCUSNFE_BASE_URL`, `FOCUSNFE_AMBIENTE`, `FOCUSNFE_TIMEOUT`.

**Deliberadamente fora de escopo:** HTTP real, `gov_fetch()` real, DANFE download, persistência, ativação em produção. `gov_fetch` e `consultar_dfe_nsu` seguem retornando `_STUB` (`PROVIDER_NAO_IMPLEMENTADO`). Testes: 91/91 verdes (43 novos).

Detalhes em `docs/adr/_handoff/2026-07-17-fase2-prep-focusnfe.md`.
