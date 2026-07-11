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
