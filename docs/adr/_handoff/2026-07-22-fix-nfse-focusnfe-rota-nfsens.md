# Handoff · Fix NFSe FocusNFe — rota nfsens_recebidas

**Data:** 2026-07-22  
**Repo:** FiscalOne  
**Escopo:** provider FocusNFe para NFS-e recebidas.

## Sintoma

O MapOne executava `dfe_sync.py --doc-type nfse` com provider
`focusnfe`, cursor `versao`, ambiente `producao` e tenant resolvido via
CtrlOne, mas o FiscalOne devolvia `FOCUS_HTTP_ERROR` com HTTP 404.

NF-e FocusNFe no mesmo fluxo funcionava, então o erro estava isolado no
endpoint NFSe do FiscalOne.

## Causa

`FocusNFeProvider.gov_fetch(tipo="nfse")` montava:

```text
/v2/nfses_recebidas
```

O endpoint correto para NFSe Nacional recebida no contrato atual é:

```text
/v2/nfsens_recebidas
```

## Correção

Arquivo alterado:

- `providers/focusnfe_provider.py`

Mudança:

- `tipo="nfse"` → `GET /v2/nfsens_recebidas`.
- `tipo="nfe"` segue em `GET /v2/nfes_recebidas`.
- Parâmetros preservados: `cnpj`, `versao`, `completa="1"`.

## Segurança

- Sem emissão fiscal.
- Sem POST real em teste local.
- Nenhum token, Authorization, certificado ou XML bruto em log.
- CT-e, MDF-e e NFSe emitida continuam fora do escopo.

## Testes

Focados:

```bash
pytest tests/test_focusnfe_nfse_e4c.py -q
pytest tests/test_provider_contract.py tests/test_fase_d_provider_por_request.py -q
```

Pós-deploy:

```bash
cd /home/ubuntu/MapOne
.venv/bin/python scripts/dfe_sync.py --tenant 1 --doc-type nfse --drain --drain-max-pages 1 --page-sleep-seconds 0
.venv/bin/python scripts/dfe_sync.py --tenant 2 --doc-type nfse --drain --drain-max-pages 1 --page-sleep-seconds 0
```

Esperado: ausência de `FOCUS_HTTP_ERROR 404`.
