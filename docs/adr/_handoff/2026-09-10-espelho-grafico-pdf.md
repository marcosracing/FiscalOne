# Handoff — ESPELHO-GRAFICO (DANFE e DACTE em PDF)

**Data:** 2026-09-10 · **Executor:** Claude Code · **Autorização:** Marcos no
chat (marker `espelho-grafico-autorizacao-v1`).

## Entregue

- `_baixar_pdf_por_chave` endurecido (regex, redirect sem Authorization,
  10 MiB, MIME e bytes mágicos `%PDF-`); `baixar_danfe` reescrito sobre ele;
  `baixar_dacte` novo.
- `_redirect_pdf_seguro`: regra própria do redirect do PDF, porque o storage
  da Focus é outro domínio e a allowlist do XML recusaria toda abertura.
- Rotas `POST /fiscal/nfe/recebida/danfe` e `POST /fiscal/cte/recebida/dacte`.
- 17 testes novos; 2 testes de contrato atualizados; suíte 577 verde.

## Decisões registradas

- **Sem custódia**: o PDF é buscado na Focus a cada abertura. Se um gate
  futuro precisar guardar, a regra de Marcos é: pasta própria do sistema e,
  a cada 60 dias, expurgo dos 30 dias mais antigos.
- **CT-e de receita não tem DACTE**: só existe em `ctes_recebidas` o CT-e
  emitido contra o CNPJ.

## Pendências

- `pytest.ini` tem WIP alheio (`-p no:cacheprovider`), preservado sem commit.
- Primeira abertura real revela o host do storage da Focus; se quisermos
  travar por host, basta popular `FISCALONE_XML_REDIRECT_HOSTS`.
