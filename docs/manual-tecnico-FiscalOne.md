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

---

## Fase 2 HTTP · FocusNFeProvider real (2026-07-16)

`FocusNFeProvider` sai do stub e passa a fazer HTTP real. Testes 100% mockados via `unittest.mock.patch("requests.get")` — zero chamada real disparada.

**API do provider:**
- `gov_fetch(payload, trace_id)` — `GET /v2/nfes_recebidas?cnpj=&versao=` com `Authorization: Basic base64(token:)`. Cursor `X-Max-Version` ou maior `versao` dos itens. Códigos HTTP explícitos (400/401/403/429/5xx/timeout/parse) mapeados para envelope canônico `FOCUS_*`.
- `consultar_dfe_nsu(...)` — delegação para `gov_fetch()` (Focus não usa mTLS).
- `baixar_danfe(chave)` — `GET /v2/nfes_recebidas/{chave}.pdf` com `allow_redirects=False`. Se 302, segundo GET **sem `Authorization`** (URL pré-assinada). Retorna `{bytes, sha256, mime, tamanho}`.

**Códigos de erro Focus** (mapeamento em `app.py::_status_para_codigo`):
- `FOCUS_TOKEN_AUSENTE`, `FOCUS_BAD_REQUEST`, `FOCUS_TIPO_NAO_SUPORTADO` → 400
- `FOCUS_AUTH_ERROR` → 401 · `FOCUS_FORBIDDEN` → 403 · `FOCUS_RATE_LIMIT` → 429
- `FOCUS_TIMEOUT`, `FOCUS_UNAVAILABLE`, `FOCUS_SERVER_ERROR`, `FOCUS_HTTP_ERROR`, `FOCUS_PARSE_ERROR`, `FOCUS_SCHEMA_ERROR` → 502

**Segurança:** token nunca em log/envelope/`raw_json_focus`; segundo GET DANFE nunca envia `Authorization`. Emissão bloqueada por `EmissaoProibida` em `emitir_*`.

**Item inválido no lote** (não-dict, sem chave, mapper lança) entra em `erros[]` com `FOCUS_ITEM_INVALIDO`; lote não é derrubado.

Detalhes: `docs/adr/_handoff/2026-07-16-fase2-http-focusnfe.md`.

---

## Fase D · provider e token FocusNFe por requisição (2026-07-17)

Base: ADR-0043 (`RLogix_shared/adr/ADR-0043-*`).

`/fiscal/gov/fetch` agora resolve provider por requisição. Contrato novo:

```json
{
  "provider": "sefaz|focusnfe",     // opcional; ausente = fallback env FISCAL_PROVIDER
  "focusnfe_token": "...",          // opcional; usado só se provider=focusnfe
  "ambiente": "homologacao|producao",
  "tipo": "nfe|cte|nfse",
  "cnpj_tenant": "...",
  "ultimo_nsu": "..."
  // + cert_pfx_base64/cert_password quando provider=sefaz
}
```

**Regras:**
- `provider` inválido → HTTP 400 `PROVIDER_INVALIDO`, sem fallback silencioso.
- `provider="focusnfe"` + `focusnfe_token` no payload: token injetado tem precedência sobre env `FOCUSNFE_TOKEN`. `focusnfe_token` é extraído via `payload.pop()` e nunca aparece em envelope/log/response.
- `provider="focusnfe"` + `cert_pfx_base64/cert_password` no payload: **removidos defensivamente** antes do provider ser instanciado (blindagem contra bug/legado do MapOne).
- SEFAZ 100% preservado — sem alteração em `SefazProvider` nem em `cert_provider`.

**Precedência do token FocusNFe** (dentro de `FocusNFeProvider.__init__(token=None)`): injetado > env `FOCUSNFE_TOKEN` > vazio (erro `FOCUS_TOKEN_AUSENTE`).

**Segurança:** testes validam que token, header `Authorization`, prefixo `Basic`, e campos de cert nunca aparecem em envelope, body HTTP ou stdout (`_log_stdout` tem schema JSON fixo). 183/183 testes passam. Zero HTTP real.

Handoff: `docs/adr/_handoff/2026-07-17-fase-d-fiscalone-provider-token-por-request.md`.

---

## Fase E1B · results = documentos fallback (2026-07-17)

Handler `/fiscal/gov/fetch` normaliza o envelope para compat com MapOne (que consome `fo_resp["results"]`). Providers modernos como `FocusNFeProvider` preenchem `documentos[]` mas não `results[]`. Regra em `app.py:594-599`:

```python
results_arr = result.get("results") or docs_arr
```

- Provider preenche `documentos[]` → `results[]` espelha (mesma lista)
- Provider preenche `results[]` explícito → preservado
- Provider preenche ambos → `results[]` explícito ganha
- Ambos vazios → `results = []`

Zero alteração em providers. Zero HTTP real em testes.
Testes: `tests/test_fase_e1b_envelope_results.py` (6 casos). Suite completa 189/189. Detalhes: `docs/adr/_handoff/2026-07-17-fase-e1b-fiscalone-results-compat-mapone.md`.

---

## Fase E4a · mapper schema real Focus + XML por chave + fix cStat (2026-07-17)

Alinha `_mapear_nfe_focus` e `gov_fetch` (`providers/focusnfe_provider.py`) com a doc oficial `NfeRecebidaResumo`. Corrige três bugs:

1. **BUG FISCAL GRAVE — cStat=101 para resumo autorizado.** Antes: `"cStat": "100" if tem_xml else "101"` — como `xml` nunca vem no resumo, tudo virava `cStat=101`, que na tabela SEFAZ significa "Cancelamento homologado". Agora: cStat=100 para autorizada; cStat=101 SÓ quando `situacao="cancelada"`; cStat=110 para `denegada`.
2. **CNPJ_emit sempre vazio** — mapper buscava `cnpj_emitente`, real é `documento_emitente`. Corrigido com ordem `documento_emitente > cnpj_emitente > CNPJ_emit`.
3. **XML nunca baixado** — mapper procurava `xml` inline (não existe no resumo). Agora `gov_fetch` chama `baixar_xml_completo(chave)` para itens com `nfe_completa=True`.

**Novos campos no doc mapeado** (`NFeDocOpcional` — todos opcionais): `nfe_completa`, `tipo_nfe`, `manifestacao`, `situacao_focus`, `cancelado`, `xml_pending`, `data_cancelamento`, `justificativa_cancelamento`.

**Novo método:** `FocusNFeProvider.baixar_xml_completo(chave, ambiente)` — `GET /v2/nfes_recebidas/{chave}.xml`, `Accept: application/xml`, timeout `min(self._timeout, 5)`, sem redirect. Códigos: `FOCUS_XML_NAO_ENCONTRADO` (404), `FOCUS_XML_HTTP_ERROR`, `FOCUS_XML_TIMEOUT`, `FOCUS_XML_ERRO`, `FOCUS_XML_VAZIO`.

**Cap de batch XML:** `_XML_BATCH_CAP=25` (override via env `FOCUSNFE_XML_BATCH_CAP`). Excedentes viram RESUMO + `xml_pending=True`. Falha individual (timeout/404) não derruba o batch — item vira RESUMO+pending, batch prossegue. Envelope acrescido de `xmls_baixados`/`xmls_pendentes`.

**Situação `cancelada` não baixa XML** nesta fase — `data_cancelamento` e `justificativa_cancelamento` já vão no doc; XML/evento de cancelamento fica para **E4b**.

**Testes:** `tests/test_focusnfe_http.py` — 17 novos casos (`TestMapper` +5, `TestBaixarXmlCompleto` 7, `TestGovFetchComXml` 6). Suite completa **205/205**. Zero HTTP real. Zero token vazado.

Handoff: `docs/adr/_handoff/2026-07-17-fase-e4a-mapper-schema-real-focus.md`.

---

## Fase E4c · NFSe Nacional Recebidas via FocusNFe (2026-07-17)

Estende `FocusNFeProvider` para NFSe Nacional recebida (tenant = tomador). ADN NFSe **não é tocada**. NFSe emitida/receita fica fora.

**Rota + parâmetros** (`providers/focusnfe_provider.py:340-378`): `tipo="nfse"` habilitado; URL `/v2/nfses_recebidas`; `params["completa"]="1"` só para NFSe. Cursor `versao` reusado.

**Mapper** (`_mapear_nfse_focus`, `providers/focusnfe_provider.py:284-402`): contrato dedicado ao schema `NfseRecebida`. **Sem cStat SEFAZ**, **sem validação DV DFe 44**. Emite `situacao_nfse ∈ {autorizada, cancelada, substituida}` a partir de `status ∈ {1,2,3}`. Prestador → `emit_*` (fornecedor); tomador → `dest_*` (tenant). `servicos.valor_servicos` → `valor_total`. `import_origin = "fiscalone_focusnfe_nfse"` (dedicado — distingue de NF-e). `status_sefaz = "focusnfe"`.

**XML via `url_xml`** (`baixar_xml_nfse`, `providers/focusnfe_provider.py:901-1024`): URL vem do próprio item (não é rota construída). `Authorization: Basic + Accept: application/xml`, timeout `min(self._timeout, 5)`. Se 3xx → segundo GET **sem Authorization** (URL pré-assinada — padrão análogo ao DANFE). Códigos: `FOCUS_XML_NAO_ENCONTRADO`, `FOCUS_XML_HTTP_ERROR`, `FOCUS_XML_NO_LOCATION`, `FOCUS_XML_TIMEOUT`, `FOCUS_XML_ERRO`, `FOCUS_XML_VAZIO`.

**Integração `gov_fetch`** (`providers/focusnfe_provider.py:604-676`): loop pós-mapper dispatcheia por `tipo`. NFSe com `url_xml` presente e status=1 baixa XML; status ∈ {2,3} (cancelada/substituida) **não** baixa. Cap `_XML_BATCH_CAP=25` compartilhado com NF-e. Falha individual não derruba batch.

**`empresa_nao_habilitada`** (`providers/focusnfe_provider.py:543-570`): detecta `{"codigo":"empresa_nao_habilitada"}` no 403 body e traduz para `FOCUS_NFSE_NAO_HABILITADA` (código canônico dedicado). Ação operacional: contato Focus, não retry.

**Testes:** `tests/test_focusnfe_nfse_e4c.py` (27 casos: mapper 7, gov_fetch tipo NFSe 4, url_xml 5, baixar_xml_nfse 7, empresa não habilitada 3, segurança 1). Suite completa **232/232**. Zero HTTP real. Zero token vazado.

Handoff: `docs/adr/_handoff/2026-07-17-fase-e4c-nfse-nacional-focusnfe.md`.

---

## Fix · NFSe FocusNFe · `servicos` como lista ou dict (2026-07-18)

Correção de bug silencioso no `_mapear_nfse_focus`: o campo `servicos`
do schema oficial `NfseRecebida` pode vir como **lista** (formato oficial
com N linhas de serviço) ou como **dict** (compat legado). O mapper
original só aceitava dict e caía em `{}` para lista, descartando valores
fiscais (`valor_servicos`, `valor_iss`, `valor_liquido`, `iss_retido`,
`discriminacao`).

**Helpers novos** (`providers/focusnfe_provider.py:284-408`):

- `_normalizar_iss_retido_nfse(raw) -> bool` — aceita `bool`, `int`,
  `float`, `str`. Strings `"true"/"1"/"sim"/"s"` (case-insensitive) e
  números > 0 → `True`. `None` / outros → `False`. Antes o mapper lia
  `iss_retido` via `_get_str`, o que transformava `False` em `"False"`
  (string truthy) — bug independente sanado no mesmo fix.
- `_normalizar_servicos_nfse(raw) -> dict` — dispatch por tipo:
  - `dict` → cópia (não muta original), preserva comportamento legado;
  - `list` → soma monetários com **`Decimal`** (nunca `float`, para
    evitar drift binário em campos fiscais), formatação estável 2 casas;
    `iss_retido` = OR entre itens (conservador: retenção falso-negativo
    gera passivo tributário); `discriminacao` concatenada com `" | "`;
    `item_lista_servico` / `codigo_cnae` = primeiro valor não vazio;
  - `None` / tipo estranho → `{}` (sem exceção).

**Mapper** (`providers/focusnfe_provider.py:451-491`):

- `servicos = _normalizar_servicos_nfse(item.get("servicos"))`.
- `iss_retido = _normalizar_iss_retido_nfse(servicos.get("iss_retido"))`
  (agora **bool** no doc, antes era string).
- Novos campos no doc final: `item_lista_servico`, `codigo_cnae`.

**Testes:** `tests/test_focusnfe_nfse_e4c.py` — 21 novos casos (T1..T11 +
variantes de normalização). Suite completa **253/253**.
`_mapear_nfe_focus` intocado (T11). Zero HTTP real. Zero regressão.

Handoff: `docs/adr/_handoff/2026-07-18-fix-nfse-focusnfe-servicos-lista.md`.

---

## E4b-1A · Ciência NF-e recebida via FocusNFe (2026-07-19)

Endpoint específico **`POST /fiscal/nfe/recebida/manifesto`** para
manifestação de **Ciência da Operação** (evento SEFAZ **210210**) de NF-e
recebida via FocusNFe. Fase FiscalOne-only e stateless — MapOne receberá
ajuste em E4b-1B.

**Provider** (`providers/focusnfe_provider.py`) — método novo
`manifestar_nfe_recebida(chave, tipo="ciencia", ambiente, trace_id)`:

- `POST /v2/nfes_recebidas/{chave}/manifesto` com body `{"tipo":"ciencia"}`,
  `Authorization: Basic base64(token:)`, `allow_redirects=False`.
- Travas duras:
  - `tipo != "ciencia"` → `FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO` (sem POST).
  - `chave` != 44 dígitos numéricos → `FOCUS_MANIFESTO_CHAVE_INVALIDA`.
  - Token ausente → `FOCUS_TOKEN_AUSENTE`.
- Mapa HTTP:
  - `200/201/202` → `MANIFESTO_OK` (envelope com `evento=210210`, `cstat`,
    `xmotivo`, `protocolo`, `http_status`).
  - `400` → `FOCUS_MANIFESTO_INVALIDO`.
  - `401` → `FOCUS_AUTH_ERROR`.
  - `403` → `FOCUS_FORBIDDEN`.
  - `404` → `FOCUS_MANIFESTO_NAO_ENCONTRADO`.
  - `409` / `422` → `FOCUS_MANIFESTO_CONFLITO` (evento já registrado, regra
    SEFAZ, etc).
  - `429` → `FOCUS_RATE_LIMIT`.
  - `5xx` ou `RequestException` → `FOCUS_MANIFESTO_HTTP_ERROR`.
- Log INFO só com **chave mascarada** (`352606***1231`). Nunca token,
  Authorization, XML, payload bruto ou body Focus.

**Rota** (`app.py`):

- Só aceita `provider="focusnfe"` (default e único). Qualquer outro →
  `FOCUS_MANIFESTO_PROVIDER_INVALIDO` (400).
- **POP** de campos sensíveis do payload antes de qualquer log/envelope:
  `focusnfe_token`, `cert_pfx_base64`, `cert_password`, `cert_cnpj`,
  `cert_valid_until`, `Authorization` / `authorization`.
- Emissão NF-e/CT-e/NFS-e/MDF-e permanece bloqueada por design (rotas
  separadas retornam 403 `EMISSAO_BLOQUEADA`).

**Codigos novos em `_status_para_codigo` (`app.py`)**:

| Código | HTTP |
|---|---|
| `FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO` | 400 |
| `FOCUS_MANIFESTO_CHAVE_INVALIDA` | 400 |
| `FOCUS_MANIFESTO_PROVIDER_INVALIDO` | 400 |
| `FOCUS_MANIFESTO_INVALIDO` | 400 |
| `FOCUS_MANIFESTO_NAO_ENCONTRADO` | 404 |
| `FOCUS_MANIFESTO_CONFLITO` | 409 |
| `FOCUS_MANIFESTO_HTTP_ERROR` | 502 |

**Bloqueado nesta fase (SEFAZ mantém definição, FiscalOne não expõe):**

- Confirmação (210200), Desconhecimento (210220), Não realizada (210240).

**Fase / escopo:**

- FiscalOne continua stateless — sem banco, sem migration, sem dependência
  PG/Oracle/ATP.
- MapOne fará dry-run/auditoria/execução controlada em E4b-1B.

**Testes:** `tests/test_manifesto_ciencia.py` — 41 novos casos (travas,
mapeamento HTTP, sanitização de payload, regressão de emissão bloqueada,
não vaza token/Authorization/XML). Suite completa **294/294** verde. Zero
POST real ao FocusNFe.

Handoff: `docs/adr/_handoff/2026-07-19-e4b1a-fiscalone-manifesto-ciencia.md`.
