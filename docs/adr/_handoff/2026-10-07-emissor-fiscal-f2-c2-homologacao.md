# Handoff — F2 (EMISSOR-FISCAL-F2-20261006): C2 CT-e/NFS-e Nacional só em homologação

**Data:** 2026-10-07 · **Executor:** Claude Code Sonnet (Harness2, worktree
`f2e-t1`) · **Pai:** `EMISSOR-FISCAL-20261006` · **Revisor independente:**
Codex · **Autorização:** herdada do despacho do coordenador (Marcos, 05–06/10,
registrada no Gateway no fluxo principal).

## Estado encontrado ao abrir o worktree

A implementação do contrato C2 (rotas `_c2_rota` em `app.py`, métodos
`c2_emitir`/`c2_consultar`/`c2_cancelar`/`_c2_baixar_arquivo` em
`providers/focusnfe_provider.py`, host fixo `_c2_host_homologacao`, health
`emissao_homologacao`/`emissao_producao`) **já estava presente** no worktree
quando esta sessão começou — inclusive os testes legados atualizados
(`test_emissao_bloqueada.py`, `test_focusnfe_preparacao.py`,
`test_focusnfe_http.py`, `test_manifesto_ciencia.py`) e uma rodada de
correção do Codex já embutida no aceite travado (`test_emissao_homologacao.py`,
seção "Rodada de correção (revisão de código do F2, Codex, 07/10)").

Rodando o aceite completo nesse estado: **312 passaram, 4 falharam** — as 4
falhas eram exatamente os dois casos novos do Codex (parametrizados em
`cte`/`nfsen`).

## O que esta sessão entregou

Dois ajustes pontuais para fechar os achados do Codex, sem alterar o arquivo
travado por hash:

| Achado do Codex | Causa | Correção | Arquivo |
|---|---|---|---|
| `focusnfe_token` continuava no dict devolvido por `request.get_json()` mesmo depois do handler terminar | `corpo.get("focusnfe_token")` lê sem remover a chave do dicionário (o mesmo objeto que o Flask cacheia) | trocado para `corpo.pop("focusnfe_token", None)`, no padrão já usado em `_espelho_pdf`/`xml_por_chave` | `app.py` (`_c2_rota`) |
| `status` desconhecido devolvido pela Focus (`"status_inventado_pela_focus"`) era repassado como se fosse um dos 6 valores do contrato C2 | `_c2_mapear_resposta` confiava em qualquer string não vazia em `corpo["status"]` | novo conjunto `_C2_STATUS_VALIDOS`; status fora dele vira erro contratual (`erro_autorizacao`/`erro_cancelamento` conforme a ação, `ok: False`, `codigo: FOCUS_STATUS_DESCONHECIDO`) — nunca repassa o valor inventado | `providers/focusnfe_provider.py` (`_c2_mapear_resposta`) |

## Testes

Comando da config:
```
python3 -m pytest -q -p no:cacheprovider tests/test_emissao_homologacao.py \
  tests/test_emissao_bloqueada.py tests/test_focusnfe_preparacao.py \
  tests/test_focusnfe_http.py tests/test_manifesto_ciencia.py
```
Resultado: **316 passed** (era 312 passed, 4 failed antes dos dois ajustes).

Suíte completa do FiscalOne (`tests/`, não é a suíte do MapOne — não toca
ATP): **741 passed**, sem regressão fora do escopo do F2.

### Desvio do comando literal — registrado, não escondido

O comando do prompt prefixa `sandbox-exec -p '(version 1)(allow default)(deny
network*)'`. Essa chamada falhou aqui com `sandbox_apply: Operation not
permitted` — esta sessão já roda dentro de uma cerca (bwrap/sandbox do
Harness) que recusa aplicar um segundo perfil `sandbox-exec` aninhado no
macOS. Verifiquei de forma independente que a rede já está bloqueada nessa
camada externa (tentativa de `socket.create_connection(("8.8.8.8", 53))`
devolveu `PermissionError: Operation not permitted`), e os dois arquivos de
teste que exercitam a Focus (`test_emissao_homologacao.py`,
`test_focusnfe_http.py`) dublam `requests` — nenhuma chamada real sairia de
qualquer forma. Os testes rodaram com `python3 -m pytest` direto, sem o
prefixo `sandbox-exec`.

## Arquivos alterados nesta sessão

- `app.py` — 1 linha (`_c2_rota`: `pop` em vez de `get` do `focusnfe_token`).
- `providers/focusnfe_provider.py` — novo conjunto `_C2_STATUS_VALIDOS` +
  branch de status desconhecido em `_c2_mapear_resposta` (~10 linhas).

Nenhum outro arquivo foi tocado. `pytest.ini` não foi lido como editável e
continua intacto (WIP alheio, fora da allowlist, conforme o prompt).

## Pendências

- `git status`/`git diff` não puderam ser inspecionados nesta sessão: o
  arquivo `.git` do worktree devolve `Operation not permitted` sob a cerca
  do Harness — comportamento esperado da cerca de git mutável do Graph2, não
  uma falha desta implementação. Nenhum comando Git foi tentado.
- Registro de autorização (`tools/record_authorization.py`), evidência
  `EXECUTOR_STOP_FINAL`, reconciliação do acervo digital (`ingest_docs.py`) e
  a integração do diff deste worktree de volta ao checkout canônico do Mac
  são passos do coordenador (Claude Code/Opus) e de Marcos — fora do escopo
  desta fração F2.
- STOP em VERIFYING conforme o prompt: sem commit, push ou deploy.
