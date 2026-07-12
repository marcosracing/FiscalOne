# Handoff — NFS-e Nacional ADN: preservar doc_type de evento · 2026-07-11

## Problema

Durante smoke real do MapOne contra o provider NFS-e Nacional ADN, o FiscalOne
retornou XMLs de evento com raiz:

```xml
<evento xmlns="http://www.sped.fazenda.gov.br/nfse">
```

O provider `nfse_nacional_provider.py` sobrescrevia todo retorno como
`doc_type='nfse'`. Isso fazia o MapOne tratar eventos como documentos fiscais
completos.

## Correção

`_normalizar_doc()` agora usa:

```text
parsed.doc_type || parsed.type || nfse
```

Também devolve `type` compatível:

```text
parsed.type || parsed_doc_type
```

## Efeito esperado

- NFS-e completa segue como `doc_type=nfse`.
- Evento NFS-e segue como `doc_type=evento`.
- Vertical consumidora não grava evento em acervo fiscal de documentos.
- FiscalOne continua sem persistência própria.

## Validação

- `python3 -m py_compile providers/nfse_nacional_provider.py services/dfe_fetch_service.py app.py`
- Smoke integrado via MapOne após restart: página ADN posterior ao NSU 50
  processada sem persistir falso documento NFS-e.
