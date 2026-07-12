# Handoff — 2026-07-12 · Schemas por tipo, providers dedicados e NSU definitivo

**Autor:** Claude (Opus 4.7) · **Solicitante:** Marcos
**ADRs:** ADR-0034 (Gateway DFe) · ADR-0035 (Zero persistencia)

## Objetivo alcancado

Sanear schemas, providers e parser por tipo de documento. Separar NF-e, CT-e
e NFS-e em estruturas dedicadas. Formalizar contrato de saida para o MapOne.

Emissao fiscal permanece bloqueada por design.

## Frente 1 — Schemas separados por tipo

Novos modulos em `schemas/`:

- `schemas/__init__.py` — Literals compartilhados:
  - `StatusXml`: COMPLETO | RESUMO | EVENTO | FALHA_PROCESSAMENTO | RECEBIDA
  - `ImportOrigin`: 6 valores (fiscalone_gov_fetch, fiscalone_sefaz,
    fiscalone_upload, fiscalone_nfse_adn, fiscalone_email, fiscalone_reparse)
  - `StatusLote`: SUCESSO_TOTAL | SUCESSO_PARCIAL | FALHA_TOTAL | SEM_DOCUMENTO
- `schemas/nfe_schema.py` — `NFeDoc` TypedDict + `validar_chave_nfe`
- `schemas/cte_schema.py` — `CTeDoc` + `ModalCTe` + `MODAL_CTE_MAP`
- `schemas/nfse_schema.py` — `NFSeDoc` (NSU string livre)
- `schemas/envelope_lote.py` — `EnvelopeLote` com status_lote + contadores

## Frente 2 — Parser por tipo

`xml_parser.py` agora expõe:
- `parse_nfe(xml, ...)` — roteamento explicito por doc_type
- `parse_cte(xml, ...)` — idem
- `parse_nfse(xml, ...)` — idem

Regras:
- **doc_type e declarado pelo chamador** — nao inferido pelo XML
- Se o XML nao casar com o esperado → `DOC_TYPE_DIVERGENTE` +
  `status_xml=FALHA_PROCESSAMENTO`
- **Todo retorno inclui `status_xml` e `parser_version`** (ok:true E ok:false)
- Novo `PARSER_VERSION = "fiscalone_xml_parser@2026-07-12"`

Codigos:
- COMPLETO → parse ok e doc_type reconhecido
- RESUMO → resNFe/resCTe/resEvento
- EVENTO → procEventoNFe/procEventoCTe
- FALHA_PROCESSAMENTO → PARSE_ERROR / PARSE_UNSUPPORTED / DOC_TYPE_DIVERGENTE
- RECEBIDA → reservado (MapOne pode marcar antes de processar)

## Frente 3 — Providers dedicados por tipo + contrato ABC

`providers/__init__.py` — `GovProvider(ABC)`:
- `gov_fetch(payload, trace_id)` → `@abstractmethod`
- `consultar_dfe_nsu(cert_pem, key_pem, cnpj, nsu, ambiente, trace_id)` → `@abstractmethod`
- Metodos legados levantam `NotImplementedError` com prefixo `PROVIDER_NAO_IMPLEMENTADO`

Novos providers wrappers:
- `providers/nfe_provider.py` — `parse_nfe` + `normalizar_nsu_sefaz`
- `providers/cte_provider.py` — `parse_cte` + `normalizar_nsu_sefaz`
- `providers/nfse_provider.py` — `parse_nfse` + `normalizar_nsu_adn`

`providers/focusnfe_provider.py` refatorado:
- Todos os metodos retornam envelope JSON estruturado:
  ```json
  {"ok": false, "provider": "focusnfe",
   "codigo": "PROVIDER_NAO_IMPLEMENTADO",
   "erro": "Provider nao implementa busca DFe recebida."}
  ```
- Nenhum caminho silencioso. Nenhum vazamento de token/URL/segredo.

`providers/sefaz_provider.py` — implementa `consultar_dfe_nsu` para
satisfazer o ABC.

`app.py`:
- `hasattr(provider, "gov_fetch")` REMOVIDO
- Captura `NotImplementedError` → 501 `PROVIDER_NAO_IMPLEMENTADO`
- `_status_para_codigo` mapeia `PROVIDER_NAO_IMPLEMENTADO` → 501

## Frente 4 — Normalizacao explicita de NSU

`services/nsu_utils.py` — `normalizar_nsu(provider, doc_type, nsu)`:

| Provider                                     | Regra          |
|----------------------------------------------|----------------|
| `sefaz` / `fiscalone_sefaz`                  | `zfill(15)`    |
| `adn_nfse` / `fiscalone_nfse_adn`            | string livre   |
| desconhecido                                 | `ValueError`   |

`dfe_fetch_service.py` e `providers/nfse_nacional_provider.py` foram
atualizados para chamar `normalizar_nsu` — nao ha mais `.zfill(15)`
cego em runtime.

## Frente 5 — Envelope de lote + observabilidade

Envelope `/fiscal/gov/fetch` e `/fiscal/documents/import` agora contem:
- `status_lote`: SUCESSO_TOTAL | SUCESSO_PARCIAL | FALHA_TOTAL | SEM_DOCUMENTO
- `recebidos`, `processados`, `persistidos`, `duplicados`, `resumos`/`resumos_count`,
  `eventos`, `erros`/`erros_count`

`/fiscal/health` expoe:
- `tls_insecure: bool`
- `tls_warning: str | null`

Boot warnings (via `logger = logging.getLogger("fiscalone")`):
- `GOV_TLS_INSECURE=1` → WARNING "USO PROIBIDO EM PRODUCAO"
- `FISCAL_PROVIDER=focusnfe` → WARNING "stub. Todas as chamadas retornarao
  PROVIDER_NAO_IMPLEMENTADO"

Log de `parse_xml` deixa de mascarar falhas 90% — reflete `status_lote` real:
- SUCESSO_TOTAL → `resultado=ok`
- SUCESSO_PARCIAL → `resultado=parcial`
- demais → `resultado=erro`

## Frente 6 — Suite pytest

Novos arquivos em `tests/` (46 testes, todos verdes):

- `test_nsu_utils.py` — SEFAZ zfill(15) / ADN preservado / provider desconhecido ValueError
- `test_provider_contract.py` — ABC nao instanciavel, FocusNFe estruturado, nao vaza token
- `test_parsers.py` — NF-e/CT-e/NFS-e COMPLETO, RESUMO, XML invalido, DOC_TYPE_DIVERGENTE, status_xml sempre presente
- `test_envelope_lote.py` — `_classificar_status_lote` para os 4 cenarios
- `test_health_e_boot.py` — tls_insecure aparece no /health, focusnfe → 501
- `test_emissao_bloqueada.py` — 9 rotas emissoras + health, mesmo com 3 flags de producao ligadas

`pytest.ini` + `tests/conftest.py` incluidos.

## Contrato de saida para o MapOne (definitivo)

### `POST /fiscal/gov/fetch` — envelope

```json
{
  "ok": true,
  "trace_id": "fo-...",
  "status_lote": "SUCESSO_TOTAL",
  "recebidos": 10, "processados": 10, "persistidos": 10,
  "duplicados": 0, "resumos_count": 0, "eventos": 0, "erros_count": 0,
  "provider": "sefaz",  // ou "nfse_nacional"
  "ambiente_adn": null,
  "status": null,
  "status_processamento": null,
  "cstat": "138", "xmotivo": "Documentos localizados",
  "ultimo_nsu": "000000000000123", "max_nsu": "000000000000200",
  "cooldown_recomendado_seg": 0,
  "documentos": [ /* NFeDoc | CTeDoc | NFSeDoc */ ],
  "resumos":    [ /* status_xml=RESUMO */ ],
  "erros":      [ /* status_xml=FALHA_PROCESSAMENTO */ ],
  "results":    [ /* unificado com "categoria" */ ],
  "data_inicio": "2026-07-01",  // eco NFS-e
  "duracao_ms": 1234
}
```

### Erros controlados por codigo:

- 400: `PAYLOAD_INVALIDO`, `CNPJ_INVALIDO`, `TIPO_NAO_SUPORTADO`,
  `CERT_NAO_CONFIGURADO`, `CERT_BASE64_INVALIDO`, `CERT_ABERTURA_FALHOU`,
  `CERT_CNPJ_DIVERGENTE`, `CERT_ENV_INVALIDO`, `CERT_SEM_CNPJ`,
  `CERT_INVALIDO`, `CERT_FONTE_NAO_SUPORTADA`
- 403: `FISCALONE_PRODUCAO_BLOQUEADA`, `EMISSAO_BLOQUEADA`
- 501: `PROVIDER_NAO_IMPLEMENTADO`
- 502: `SEFAZ_INDISPONIVEL`, `SEFAZ_HTTP_ERRO`, `SEFAZ_XML_INVALIDO`,
  `TLS_ERRO`, `NFSE_ADN_HTTP_ERRO`, `NFSE_ADN_TIMEOUT`,
  `NFSE_ADN_XML_INVALIDO`, `NFSE_ADN_AUTH_ERRO`
- 500: `ERRO_INTERNO`

Nenhuma resposta e HTML/traceback.

## Validacoes executadas

- `py_compile` em app.py, xml_parser.py, providers/*.py, services/*.py, schemas/*.py — OK
- `pytest tests/` — **46/46 verde** (0.55s)
- Grep por `PFX|BEGIN|password|senha|base64|xml_bruto` nos logs de smoke local — **vazio**
- **Nenhuma chamada real a SEFAZ/ADN.**

## Escopo NAO tocado (permanece)

- Rotas emissoras (nfe/cte/mdfe/cancelamento/inutilizar/cce/encerrar/condutor) → `bloquear_emissao` (403 EMISSAO_BLOQUEADA)
- ADR-0035 — zero persistencia
- Cert em transito, PEM chmod 600 unlink em finally
- `_producao_bloqueada` — 3 flags obrigatorias inalteradas

## Confirmacoes finais

- Emissao fiscal continua bloqueada (test_emissao_bloqueada.py — 9 rotas + /health)
- Nenhum PFX/senha/base64/xml_bruto vazou em log (grep vazio)
- Nenhuma chamada real a SEFAZ/ADN
- `hasattr(provider, "gov_fetch")` removido de app.py

## Nao aplicavel a este repo

- **`gov_import.py RuntimeError intacto`** — `gov_import.py` **nao existe no FiscalOne**.
  Esse arquivo pertence ao `rlogix/CtrlOne` (fora do escopo desta sessao).
- **`POST /api/gov/fetch continua 410 Gone`** — essa rota **nao existe no FiscalOne**.
  O FiscalOne usa `POST /fiscal/gov/fetch`. Presumo que a rota `/api/gov/fetch`
  seja de outro servico (MapOne ou CtrlOne).

## Pendencias para o MapOne

1. Consumir os novos campos `status_lote`, contadores e `status_xml` por doc.
2. Aceitar os 6 valores de `import_origin` (idealmente com CHECK constraint em
   `op_fiscal_xml.import_origin`).
3. Adotar `data_inicio="2026-07-01"` como corte NFS-e antes do Gerenciador
   Fiscal 50.1.1.
4. Consumir `duplicados` (hoje sempre 0; MapOne detecta duplicidade por chave).
