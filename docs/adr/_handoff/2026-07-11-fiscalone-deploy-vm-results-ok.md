# Handoff — 2026-07-11 · FiscalOne VM e contrato results[]

## Escopo

Implantação do FiscalOne na VM e ajuste do contrato `results[]` para DFe.

## Problema

Na VM, o MapOne chamava `http://127.0.0.1:5002`, mas não havia FiscalOne rodando.
O frontend exibia `FiscalOne indisponível`.

No Mac, a busca DFe retornou `Importação concluída com pendências` com `Erros: 13`.
O problema não era necessariamente 13 erros fiscais: o envelope `results[]` do
FiscalOne não trazia `ok` nos itens `COMPLETO`/`RESUMO`, e o MapOne interpretava
ausência de `ok` como falha.

## Correção FiscalOne

- `services/dfe_fetch_service.py`: `item_base` agora inclui `ok=true` para
  resultados classificados como `COMPLETO` ou `RESUMO`.
- `scripts/deploy_fiscalone_vm.sh`: rotina de deploy Mac → VM com systemd,
  `.venv`, health check e sem copiar segredos.
- `docs/manual-tecnico-FiscalOne.md`: seções de deploy e contrato `results[]`.
- `docs/acervo/FISCALONE_OPERACIONAL.md`: registro do acervo digital técnico.

## Regras preservadas

- FiscalOne segue sem persistência própria.
- Certificado A1 não é gravado no FiscalOne.
- Emissão fiscal segue bloqueada por design.
- Produção liberada apenas para DFe recebido.

## Validação esperada

```bash
curl -fsS http://127.0.0.1:5002/fiscal/health
systemctl is-active fiscalone.service
```

MapOne deve parar de tratar `COMPLETO`/`RESUMO` como `erro` quando a busca DFe
retornar lote misto.
