# Acervo Digital — FiscalOne Operacional

## Identidade

- Produto: FiscalOne
- Papel: gateway fiscal técnico RLogix
- Porta local padrão: `127.0.0.1:5002`
- Persistência própria: não
- Certificado A1 em repouso: não

## Rotina operacional

- Desenvolvimento: `~/Documents/FiscalOne`
- VM: `/home/ubuntu/FiscalOne`
- Serviço: `fiscalone.service`
- Deploy: `scripts/deploy_fiscalone_vm.sh`
- Health: `GET /fiscal/health`

## Segurança

FiscalOne recebe o certificado A1 em trânsito por requisição, usa em memória e
descarta. Não deve gravar `.env` com certificado, PFX, PEM, senha, base64 ou XML
bruto em log.

## Documentação de suporte

- `README.md`
- `docs/manual-tecnico-FiscalOne.md`
- `docs/adr/_handoff/`

## Registro operacional — 2026-07-11

NFS-e Nacional ADN está habilitado como DFe recebido. O provider deve preservar
o tipo retornado pelo parser:

- documento NFS-e completo: `doc_type=nfse`;
- evento NFS-e: `doc_type=evento`.

Eventos não são persistidos no acervo fiscal da vertical como documentos. O
MapOne usa essa distinção para gravar apenas XML fiscal completo em
`op_fiscal_xml` e tratar eventos em `op_dfe_evento`.

## Registro operacional — 2026-07-11 b

FiscalOne foi ajustado para subir com `threaded=True` no servidor Flask
local/VM simples. O objetivo é aceitar chamadas simultâneas controladas do
agendador multiempresa do MapOne.

Limites preservados:

- sem persistência própria;
- sem certificado em repouso;
- sem emissão fiscal ativa;
- logs sem PFX, senha, base64 ou XML completo.
