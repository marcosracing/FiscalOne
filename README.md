# FiscalOne — Gateway Gov RLogix

Motor fiscal completo do ecossistema RLogix.
Comunica SEFAZ, parseia documentos, emite CT-e/MDF-e.

## Provider pattern

Trocar provider = trocar variavel de ambiente:

    FISCAL_PROVIDER=sefaz      # Comunicacao direta SEFAZ (padrao)
    FISCAL_PROVIDER=focusnfe   # Focus NF-e REST API

## Rodar

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    python app.py

Porta: 5002

## Endpoints

| Metodo | Endpoint | Descricao |
|--------|----------|-----------|
| GET | /fiscal/health | Health check |
| POST | /fiscal/sync/{cnpj} | Sync NF-e + CT-e |
| GET | /fiscal/nfe/{cnpj} | Listar NF-es |
| GET | /fiscal/cte/{cnpj} | Listar CT-es |
| POST | /fiscal/cte | Emitir CT-e |
| POST | /fiscal/mdfe | Emitir MDF-e |
| DELETE | /fiscal/cte/{chave} | Cancelar CT-e |

## ADRs

- MAP-0017 — FiscalOne Gateway Gov (MapOne)
- ADR-0028 — Fronteira fiscal RLogix-wide (CtrlOne, a publicar)

## Status

- SefazProvider: STUB — aguarda migracao gov_import.py (ADR-0028)
- FocusNFeProvider: STUB — implementar quando necessario
