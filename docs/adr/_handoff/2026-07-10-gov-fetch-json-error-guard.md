# Handoff — FiscalOne `/fiscal/gov/fetch` sempre JSON

**Data:** 2026-07-10  
**Escopo:** correção defensiva para erro `HTTP 500` HTML no botão `Buscar SEFAZ / Nacional` do MapOne.

## Sintoma

MapOne recebia:

- `Resposta não-JSON do FiscalOne (HTTP 500)`

O FiscalOne estava vivo em `/fiscal/health`, mas `POST /fiscal/gov/fetch`
retornava HTML 500 em vez de envelope JSON controlado.

## Causa provável

O caminho de bloqueio/erro chama `_log_stdout()` antes de responder. Se o
stdout do processo estiver fechado/quebrado (terminal encerrado, daemon
transitório ou pipe inválido), o `print(..., flush=True)` pode gerar exceção e
derrubar a rota antes do `jsonify`.

## Correção

Arquivo alterado:

- `app.py`

Mudanças:

- `_log_stdout()` agora absorve `BrokenPipeError` e `OSError`;
- novo `@app.errorhandler(Exception)` devolve JSON `ERRO_INTERNO` em exceções
  não tratadas das rotas fiscais.

## Regras preservadas

- FiscalOne continua gateway sem persistência própria;
- emissão fiscal continua bloqueada por design;
- produção DFe recebidos continua exigindo as três flags:
  - `FISCALONE_ENABLE_PRODUCAO=1`
  - `MAPONE_FISCAL_PRODUCAO_READY=1`
  - `FISCALONE_DFE_RECEBIDO_ONLY=1`
- nenhum segredo é logado.

## Validação esperada

`POST /fiscal/gov/fetch` deve sempre retornar `Content-Type: application/json`
nos cenários:

- produção bloqueada;
- payload inválido;
- certificado ausente;
- exceção interna controlada.
