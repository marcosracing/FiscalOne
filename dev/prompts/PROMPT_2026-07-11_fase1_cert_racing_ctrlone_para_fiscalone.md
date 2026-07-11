# PROMPT — FiscalOne Fase 1 · Certificado Racing vindo do CtrlOne

## AMBIENTE

- FiscalOne: `~/Documents/FiscalOne`
- CtrlOne: `~/Documents/rlogix`
- MapOne: `~/Documents/MapOne`
- Data: 2026-07-11

## OBJETIVO

Preparar o FiscalOne para consultar **somente DFe recebido** em produção usando o certificado A1 da Racing que hoje está no ambiente do CtrlOne.

Esta fase é **FiscalOne-first**:

1. Validar o certificado A1 da Racing no CtrlOne sem expor segredo.
2. Configurar o FiscalOne para usar esse certificado.
3. Liberar produção apenas para `gov_fetch` de DFe recebido.
4. Garantir que qualquer emissão fiscal continue bloqueada.
5. Fazer smoke controlado, sem loop e sem imprimir XML completo.

## REGRAS ABSOLUTAS

- Não imprimir senha, PFX, PEM, base64, blob criptografado, `.env` completo ou XML completo.
- Não commitar `.env`, PFX, PEM, logs, wallet ou qualquer segredo.
- Não copiar certificado para dentro do repositório do FiscalOne se for possível apontar para o arquivo já existente no CtrlOne.
- Produção liberada **somente** para recepção/consulta DFe:
  - NF-e recebida;
  - CT-e recebido, quando habilitado;
  - nunca emissão.
- Rotas de emissão devem continuar bloqueadas mesmo com as flags de produção ligadas.

## DISCOVERY JÁ CONFIRMADO

Discovery read-only anterior confirmou:

- CtrlOne empresa Racing:
  - `company_id=1`
  - `cnpj=07219398000109`
  - `crt=3`
  - `regime_tributario=Lucro Real`
- No banco CtrlOne, `company_certificates` não possui certificado ativo para `company_id=1`.
- O CtrlOne possui configuração legada em `.env`:
  - `GOV_CERT_PATH=<masked>`
  - `GOV_CERT_PASS=<masked>`
- Existe arquivo `~/Documents/rlogix/certificado.pfx`.
- FiscalOne está ativo em `http://127.0.0.1:5002`.
- FiscalOne responde JSON em `/fiscal/gov/fetch`.
- FiscalOne está bloqueado por falta das 3 flags:
  - `FISCALONE_ENABLE_PRODUCAO`
  - `MAPONE_FISCAL_PRODUCAO_READY`
  - `FISCALONE_DFE_RECEBIDO_ONLY`
- MapOne possui NSU inicial:
  - `tenant_id=1`
  - `company_cnpj=07219398000109`
  - `doc_type=nfe`
  - `ambiente=producao`
  - `ultimo_nsu=125643`

## FASE 1.1 — Discovery Confirmatório Sem Segredo

No FiscalOne:

```bash
cd ~/Documents/FiscalOne
git status --short
git log -1 --oneline
curl -s http://127.0.0.1:5002/fiscal/health
```

No CtrlOne:

```bash
cd ~/Documents/rlogix
git status --short
git log -1 --oneline
```

Validar sem imprimir valores:

- `.env` do CtrlOne contém `GOV_CERT_PATH`?
- `.env` do CtrlOne contém `GOV_CERT_PASS`?
- o arquivo apontado por `GOV_CERT_PATH` existe?
- fallback: `~/Documents/rlogix/certificado.pfx` existe?

Retorno permitido:

```text
GOV_CERT_PATH presente: sim/não
GOV_CERT_PASS presente: sim/não
PFX existe: sim/não
```

Não imprimir o path completo se o operador considerar sensível. Pode imprimir apenas `basename`.

## FASE 1.2 — Validação do PFX da Racing

Criar um script temporário ou comando Python local que:

1. leia `GOV_CERT_PATH`/`GOV_CERT_PASS` do `.env` do CtrlOne;
2. se `GOV_CERT_PATH` estiver vazio, use `~/Documents/rlogix/certificado.pfx`;
3. abra o PFX com `cryptography`;
4. extraia o CNPJ do certificado;
5. compare com `07219398000109`;
6. imprima apenas:

```text
PFX abre: sim/não
CNPJ certificado: <14 dígitos>
CNPJ esperado: 07219398000109
Validade até: YYYY-MM-DD
Compatível com Racing: sim/não
```

Não imprimir subject completo se ele trouxer dados pessoais além do necessário.
Não imprimir senha, PEM, chave privada, PFX ou base64.

Se o CNPJ divergir, parar e retornar:

```text
BLOQUEADO: certificado encontrado no CtrlOne não pertence à Racing.
```

## FASE 1.3 — Configurar FiscalOne

Se e somente se o PFX abrir e o CNPJ bater com Racing:

1. Atualizar `~/Documents/FiscalOne/.env` com:

```env
FISCALONE_ENABLE_PRODUCAO=1
MAPONE_FISCAL_PRODUCAO_READY=1
FISCALONE_DFE_RECEBIDO_ONLY=1
FISCALONE_CERT_PFX_PATH=<mesmo caminho do PFX validado>
FISCALONE_CERT_PASSWORD=<mesma senha validada>
```

2. Preferir as variáveis `FISCALONE_CERT_*`.
3. Não remover compatibilidade com `GOV_CERT_*`.
4. Não commitar `.env`.
5. Não imprimir valores sensíveis.

## FASE 1.4 — Reiniciar FiscalOne

```bash
cd ~/Documents/FiscalOne
old=$(lsof -tiTCP:5002 -sTCP:LISTEN || true)
[ -n "$old" ] && kill $old
set -a; [ -f .env ] && . ./.env; set +a
nohup python3 app.py > /tmp/fiscalone_5002.log 2>&1 &
sleep 2
curl -s http://127.0.0.1:5002/fiscal/health
```

Health esperado:

- `ok=true`
- `ambiente=producao`
- `gov_fetch_dfe=true`
- `producao_bloqueada=false`
- `emissao_ativa=false`
- `emissao_bloqueada_por_design=true`
- flags de produção todas `true`

## FASE 1.5 — Smoke Controlado FiscalOne

Executar uma única chamada, sem loop:

```bash
curl -s -X POST http://127.0.0.1:5002/fiscal/gov/fetch \
  -H 'Content-Type: application/json' \
  -H 'X-Source-System: fiscalone-fase1-smoke' \
  --data '{
    "cnpj_tenant":"07219398000109",
    "company_id":1,
    "ambiente":"producao",
    "tipo":"nfe",
    "ultimo_nsu":"125643",
    "cert_source":"env_or_vault"
  }'
```

Registrar somente:

- HTTP status;
- `ok`;
- `codigo`;
- `cstat`;
- `xmotivo`;
- `ultimo_nsu`;
- `max_nsu`;
- quantidade de `documentos`;
- quantidade de `resumos`;
- quantidade de `erros`;
- `cooldown_recomendado_seg`;
- `cert_fonte`.

Não imprimir XML completo.

## FASE 1.6 — Confirmar Emissão Bloqueada

Mesmo com as flags ligadas, testar que emissão continua bloqueada:

```bash
curl -s -X POST http://127.0.0.1:5002/fiscal/cte \
  -H 'Content-Type: application/json' \
  --data '{}'
```

Resultado esperado:

```json
{
  "ok": false,
  "codigo": "EMISSAO_BLOQUEADA"
}
```

Se qualquer rota de emissão deixar de bloquear, reverter a alteração e parar.

## FASE 1.7 — Documentação e Commit

Se houver alteração em código ou documentação do FiscalOne:

1. Atualizar `docs/manual-tecnico-FiscalOne.md`.
2. Criar handoff:

```text
docs/adr/_handoff/2026-07-11-fiscalone-cert-racing-ctrlone-dfe-recebido.md
```

3. Commitar somente código/docs.
4. Não commitar `.env`.
5. Não commitar PFX/PEM/base64/logs.

Se houve apenas `.env` local e smoke, não precisa commit; criar/atualizar handoff se o time quiser registro operacional sem segredo.

## RETORNO ESPERADO

Responder com tabela curta:

| Item | Resultado |
|---|---|
| CtrlOne GOV_CERT_PATH | presente/ausente |
| CtrlOne GOV_CERT_PASS | presente/ausente |
| PFX validado | sim/não |
| CNPJ certificado | bate/não bate |
| Validade certificado | data |
| FiscalOne flags | liberadas/não |
| FiscalOne health | ok/não |
| Emissão bloqueada | sim/não |
| Smoke SEFAZ | cStat/xMotivo |
| Documentos completos | qtd |
| Resumos DFe | qtd |
| Erros | qtd |
| Cooldown recomendado | segundos |
| Manual técnico | atualizado/não aplicável |
| Handoff | criado/não aplicável |
| Commit | hash/não houve |

## CRITÉRIO DE PARADA

Parar imediatamente se:

- PFX não existe;
- senha não abre o PFX;
- CNPJ do certificado não é `07219398000109`;
- FiscalOne retorna HTML/500;
- SEFAZ retorna consumo indevido `cStat=656`;
- qualquer rota de emissão fiscal ficar ativa.

