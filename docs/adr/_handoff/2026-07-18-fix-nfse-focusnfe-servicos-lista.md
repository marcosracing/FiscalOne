# Handoff Fix — FiscalOne: NFSe FocusNFe · `servicos` como lista ou dict

Data: 2026-07-18
Base: Fase E4c (`_mapear_nfse_focus`) · Commit anterior: `5b5ddc6`
Discovery: schema oficial `NfseRecebida` da FocusNFe admite `servicos`
como **lista de objetos**, não apenas dict.

## Bug

`providers/focusnfe_provider.py::_mapear_nfse_focus` tratava
`item["servicos"]` **exclusivamente como dict**:

```python
servicos = item.get("servicos") if isinstance(item.get("servicos"), dict) else {}
```

Quando FocusNFe entregava `servicos` como lista (contrato oficial), o
mapper caía no ramo `else {}`. Resultado: **todos os campos financeiros
e fiscais eram descartados silenciosamente**:

- `valor_servicos` → `""`
- `valor_iss` → `""`
- `valor_liquido` → `""`
- `iss_retido` → `""` (string vazia; consumidor lê como falsy)
- `discriminacao` → `""`
- `item_lista_servico` / `codigo_cnae` → nem existiam no doc

Além disso, o `iss_retido` legado (dict) era lido via `_get_str`, o que
transformava `False` em `"False"` (string truthy) — bug independente que
fica sanado no mesmo fix.

## Evidência do schema FocusNFe

Doc oficial `NfseRecebida` — `servicos` é campo composto com N itens:
cada item tem valores próprios (`valor_servicos`, `valor_iss`,
`valor_liquido`, `iss_retido`, `discriminacao`,
`item_lista_servico`, `codigo_cnae`). Uma nota pode ter 1..N serviços.

Empresas com uma única linha de serviço às vezes recebem como dict
(compat legado), mas o formato oficial é **lista**. Por isso o mapper
precisa aceitar os dois formatos.

## Regra de normalização

Dois helpers privados novos em `providers/focusnfe_provider.py`:

### `_normalizar_iss_retido_nfse(raw) -> bool` (linha 293)

- `bool` → valor direto.
- `int` / `float` → `True` se > 0.
- `str` → `"true"`, `"1"`, `"sim"`, `"s"` (case-insensitive) → `True`;
  senão tenta interpretar como número decimal (`Decimal(s) > 0`).
- `None` / outro tipo → `False`.

### `_normalizar_servicos_nfse(raw) -> dict` (linha 336)

- `dict`: retorna **cópia** (`dict(raw)`), sem mutar original — preserva
  comportamento legado.
- `list`:
  - ignora itens não-dict;
  - soma `valor_servicos`, `valor_iss`, `valor_liquido` com **`Decimal`**
    (nunca `float`) → evita drift binário;
  - saída formatada como string estável 2 casas
    (`_dec_str_estavel(total)` → `"2000.00"`), compatível com contrato
    atual do mapper;
  - `iss_retido` = **OR** entre itens (True se qualquer item indicar
    retenção, via `_normalizar_iss_retido_nfse`);
  - `discriminacao`: concatena descrições não vazias com `" | "`;
  - `item_lista_servico` / `codigo_cnae`: **primeiro valor não vazio**
    encontrado na lista;
  - lista vazia ou apenas itens inválidos → `{}`.
- `None` / outros tipos → `{}` (sem exceção).

## Decisões

### Decimal em vez de float

Somas de valores monetários sem `Decimal` acumulam erro binário (ex.:
`0.1 + 0.2 = 0.30000000000000004`). Como o campo alimenta persistência
fiscal downstream (MapOne), o risco de divergência de centavos é
inaceitável. Uso `Decimal(str(v))` na origem e `total.quantize(Decimal("0.01"))`
na saída para estabilizar o formato decimal.

### `iss_retido = True` se qualquer item reter

Retenção de ISS é característica **fiscal do prestador/serviço**, não
agregada por soma. Se qualquer linha da NFSe tiver ISS retido, o
tomador precisa reter na fonte. Falso-negativo aqui gera passivo
tributário. Escolha conservadora: OR.

### Emissão de novos campos no doc mapeado

O doc devolvido agora carrega `item_lista_servico` e `codigo_cnae`
(dependendo do payload). Antes não eram lidos. Consumo em MapOne é
opcional — se não usar, o campo fica no envelope sem efeito colateral.

## Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `providers/focusnfe_provider.py:28` | import `Decimal`, `InvalidOperation` |
| `providers/focusnfe_provider.py:284-408` | helpers `_normalizar_iss_retido_nfse`, `_dec_str_estavel`, `_normalizar_servicos_nfse` |
| `providers/focusnfe_provider.py:451` | `servicos = _normalizar_servicos_nfse(item.get("servicos"))` |
| `providers/focusnfe_provider.py:488` | `iss_retido = _normalizar_iss_retido_nfse(...)` (bool no doc) |
| `providers/focusnfe_provider.py:490-491` | leitura `item_lista_servico` / `codigo_cnae` |
| `providers/focusnfe_provider.py:524-525` | campos no doc final |
| `tests/test_focusnfe_nfse_e4c.py` | +21 testes (T1–T11 + variantes de normalização) |

## Testes

Novos testes em `tests/test_focusnfe_nfse_e4c.py`:

| Suite | Casos | Cobre |
|---|---|---|
| `TestServicosListaOuDict` | T1..T4 | lista com 2 itens, dict legado, lista vazia, None |
| `TestIssRetidoNormalizacao` | T5..T8 + variantes | bool, string numérica, zero int, false, "true"/"sim"/"s", negativo |
| `TestIssRetidoAgregadoLista` | T9 + all-false | OR entre itens |
| `TestMapperCompletoPayloadRealista` | T10 | mapper completo com lista, contrato NFSe preservado |
| `TestRegressaoNfe` | T11 | `_mapear_nfe_focus` sem alteração comportamental |
| `TestNormalizadorHelperIsolado` | helper puro | dict retorna cópia, lista vazia, tipos estranhos |

Nenhum teste faz HTTP real. Todos os testes anteriores continuam verdes.

## Validações executadas

```
python3 -m py_compile providers/focusnfe_provider.py     # OK
.venv/bin/pytest tests/test_focusnfe_nfse_e4c.py -v      # 48 passed
.venv/bin/pytest tests/test_focusnfe_http.py -v          # 80 passed
.venv/bin/pytest                                          # 253 passed
git diff --check                                          # OK
```

Anterior: 232 (E4c). Agora: **253** (+21 novos, 0 removidos).
Zero HTTP real. Zero token vazado. Zero regressão.

## Fora de escopo

- MapOne — consumo dos novos campos `item_lista_servico` e `codigo_cnae`
  é opcional; qualquer alteração fica para fase de consumo dedicado.
- ADN NFSe / `nfse_nacional_provider.py` — intocado.
- `_mapear_nfe_focus` — intocado (validado por T11).

## Push / Deploy

- Push: **NÃO executado** — aguardando autorização.
- Deploy: **NÃO executado** — aguardando autorização.
- Ordem de deploy quando aprovar: FiscalOne único componente
  alterado. MapOne não precisa migration para este fix.

## Go / No-Go — todos verdes

- [x] `_normalizar_servicos_nfse` aceita `list` / `dict` / `None`.
- [x] `_normalizar_iss_retido_nfse` cobre `bool` / `str` / número.
- [x] T1 e T2 verdes.
- [x] T9 verde.
- [x] `_mapear_nfe_focus` sem alteração comportamental.
- [x] Regressão completa verde (253 passed).
- [x] `git diff --check` limpo.
- [x] Handoff + acervo/manual atualizados.
- [x] Sem push / deploy.
