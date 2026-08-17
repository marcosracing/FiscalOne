#!/usr/bin/env bash
# deploy_fiscalone_vm.sh — deploy controlado Mac → VM teste/produção
#
# Uso:
#   scripts/deploy_fiscalone_vm.sh             # deploy interativo (prompt DEPLOY)
#   scripts/deploy_fiscalone_vm.sh --dry-run   # mostra plano, nao executa
#   DEPLOY_CONFIRM=DEPLOY scripts/deploy_fiscalone_vm.sh   # nao-interativo
#
# Regras:
# - Nao copia .env, .venv, .git, logs, wallets ou segredos.
# - FiscalOne permanece sem certificado em repouso; o A1 vem do MapOne.
# - Nao habilita emissao, nao ativa provider emissor, nao chama FocusNFe.
# - Nao executa migration nesta rodada (nao possui runner proprio).
# - Cria backup pre-deploy, valida integridade e retem apenas os 2 mais recentes.
# - Registra DEPLOY_BUILD (commit, subject, version se houver, timestamp UTC).
# - Nao imprime fingerprint, hash de segredo, valor de .env, token ou wallet.

set -euo pipefail

VM_USER="ubuntu"
VM_HOST="157.151.19.131"
VM_PATH="/home/ubuntu/FiscalOne"
VM_BACKUP_DIR="/home/ubuntu/backups"
VM_SSH_KEY="${HOME}/.ssh/oracle-vm.key"
VM_SERVICE="fiscalone.service"
VM_PORT="5002"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --dry-run|--status) DRY_RUN=true ;;
    *) echo "Argumento invalido: $arg" >&2; exit 2 ;;
  esac
done

info(){  echo "[INFO]  $*"; }
warn(){  echo "[WARN]  $*"; }
abort(){ echo "[ABORT] $*" >&2; exit 1; }
ssh_vm(){ ssh -i "${VM_SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=15 "${VM_USER}@${VM_HOST}" "$@"; }

# ── FASE 1: Worktree limpo ───────────────────────────────────────────────────
DIRTY=$(git status --porcelain 2>/dev/null) || abort "Nao e repo git."
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  abort "Worktree sujo. Commit/stash antes do deploy."
fi

# ── FASE 2: Estado local ─────────────────────────────────────────────────────
LOCAL_COMMIT=$(git rev-parse --short HEAD)
LOCAL_SUBJECT=$(git log -1 --pretty=%s)
LOCAL_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")

info "Commit local : ${LOCAL_COMMIT}  ${LOCAL_SUBJECT}"
info "VERSION      : ${LOCAL_VERSION}"
info "Destino      : ${VM_USER}@${VM_HOST}:${VM_PATH}"

# ── FASE 3: VM alcancavel ────────────────────────────────────────────────────
info "Testando conexao SSH..."
ssh_vm "echo OK" >/dev/null 2>&1 || abort "Nao foi possivel conectar via SSH."

# ── FASE 4: Plano ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  PLANO DE DEPLOY (FiscalOne)"
echo "  Commit  : ${LOCAL_COMMIT}  ${LOCAL_SUBJECT}"
echo "  VERSION : ${LOCAL_VERSION}"
echo "  Destino : ${VM_USER}@${VM_HOST}:${VM_PATH}"
echo "  Backup  : ${VM_BACKUP_DIR}/FiscalOne_pre_deploy_YYYYMMDD_HHMMSS.tar.gz"
echo "  Migrate : (sem runner proprio nesta rodada)"
echo "══════════════════════════════════════════════════════"
echo ""

if $DRY_RUN; then
  info "--dry-run: nenhuma alteracao executada."
  exit 0
fi

# ── FASE 5: Confirmacao explicita ────────────────────────────────────────────
# DEPLOY_CONFIRM=DEPLOY pula o prompt; sem a env var, prompt interativo.
CONFIRM="${DEPLOY_CONFIRM:-}"
if [ -z "$CONFIRM" ]; then
  read -rp "Digite DEPLOY para continuar (qualquer outra entrada cancela): " CONFIRM
fi
[ "$CONFIRM" = "DEPLOY" ] || abort "Deploy cancelado pelo operador."

# ── FASE 6: Backup pre-deploy na VM ──────────────────────────────────────────
info "Criando backup pre-deploy na VM..."
TS=$(ssh_vm "date +%Y%m%d_%H%M%S")
BACKUP_FILE="${VM_BACKUP_DIR}/FiscalOne_pre_deploy_${TS}.tar.gz"
ssh_vm "mkdir -p ${VM_BACKUP_DIR} && \
  tar --exclude=.venv --exclude=logs --exclude=run --exclude=__pycache__ \
      --exclude=.env --exclude=.env.* --exclude=wallet \
      -czf ${BACKUP_FILE} -C /home/ubuntu FiscalOne"
info "Backup criado: ${BACKUP_FILE}"

# ── FASE 6.1: Validacao + retencao 2 ─────────────────────────────────────────
info "Validando integridade do novo backup..."
if ! ssh_vm "tar -tzf ${BACKUP_FILE} >/dev/null 2>&1"; then
  abort "Backup ${BACKUP_FILE} corrompido — backups anteriores preservados."
fi
info "Novo backup integro."

info "Retencao: mantendo os 2 backups FiscalOne_pre_deploy_*.tar.gz mais recentes..."
# Fail-closed: se algo falhar (permissao, disco, ssh, listing), aborta e
# preserva os arquivos anteriores. Nenhuma perda de rollback silenciosa.
if ! ssh_vm "set -e; cd ${VM_BACKUP_DIR} && \
    ls -1t FiscalOne_pre_deploy_*.tar.gz 2>/dev/null | tail -n +3 | \
    while IFS= read -r f; do rm -v -- \"\$f\" || exit 1; done"; then
  abort "Retencao de backups falhou — sincronia interrompida, arquivos anteriores intactos."
fi

# ── FASE 7: rsync ────────────────────────────────────────────────────────────
info "Executando rsync..."
REQ_HASH_BEFORE=$(ssh_vm "sha256sum ${VM_PATH}/requirements.txt 2>/dev/null | awk '{print \$1}'" || true)
ssh_vm "mkdir -p ${VM_PATH}"
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'wallet/' \
  --exclude 'logs/' \
  --exclude 'run/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  -e "ssh -i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=15" \
  ./ "${VM_USER}@${VM_HOST}:${VM_PATH}/"
info "rsync concluido."

# ── FASE 7.1: Sincronizar dependencias Python ────────────────────────────────
REQ_HASH_AFTER=$(ssh_vm "sha256sum ${VM_PATH}/requirements.txt 2>/dev/null | awk '{print \$1}'" || true)
if [ -n "$REQ_HASH_BEFORE" ] && [ "$REQ_HASH_BEFORE" = "$REQ_HASH_AFTER" ] && ssh_vm "test -d ${VM_PATH}/.venv"; then
  info "requirements.txt inalterado; validando o venv existente..."
  ssh_vm "cd ${VM_PATH} && .venv/bin/pip check" >/dev/null
  info "Dependencias preservadas e integras."
else
  info "requirements.txt alterado ou venv ausente; sincronizando..."
  ssh_vm "cd ${VM_PATH} && python3 -m venv .venv && \
    .venv/bin/python -m pip install --upgrade pip >/dev/null && \
    .venv/bin/python -m pip install -r requirements.txt --quiet && \
    .venv/bin/pip check" >/dev/null
  info "Dependencias sincronizadas e integras."
fi

# ── FASE 7.2: .env preservado, so valida presenca das chaves obrigatorias ────
# Nao imprime fingerprint, hash ou conteudo de segredo.
info "Validando .env preservado (sem exibir valores)..."
REQUIRED_KEYS="FISCALONE_AMBIENTE FISCALONE_M2M_TOKEN"
MISSING=$(ssh_vm "if [ ! -f ${VM_PATH}/.env ]; then echo 'ENV_AUSENTE'; exit 0; fi; for k in ${REQUIRED_KEYS}; do grep -q \"^\${k}=\" ${VM_PATH}/.env || echo \$k; done" | tr -d '\r')
if [ -n "$MISSING" ]; then
  abort ".env invalido na VM. Chave(s) ausente(s) ou arquivo faltando: $MISSING"
fi
ssh_vm "chmod 600 ${VM_PATH}/.env"
info ".env presente e com permissao 600."

# ── FASE 8: DEPLOY_BUILD (rastreabilidade) ───────────────────────────────────
info "Gravando marcador DEPLOY_BUILD na VM..."
DEPLOYED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ssh_vm "cat > ${VM_PATH}/DEPLOY_BUILD <<EOF
commit=${LOCAL_COMMIT}
subject=${LOCAL_SUBJECT}
version=${LOCAL_VERSION}
deployed_at_utc=${DEPLOYED_AT_UTC}
deployed_from=dev-mac
EOF"
info "DEPLOY_BUILD gravado."

# ── FASE 9: Unit systemd + restart ───────────────────────────────────────────
ssh_vm "sudo tee /etc/systemd/system/${VM_SERVICE} >/dev/null <<'ESVC'
[Unit]
Description=FiscalOne Gateway Fiscal RLogix
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/FiscalOne
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/ubuntu/FiscalOne/.env
ExecStart=/home/ubuntu/FiscalOne/.venv/bin/python /home/ubuntu/FiscalOne/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
ESVC
sudo systemctl daemon-reload
sudo systemctl enable ${VM_SERVICE} >/dev/null
sudo systemctl restart ${VM_SERVICE}"
sleep 3
ssh_vm "systemctl is-active ${VM_SERVICE}" | grep -q "^active$" \
  || abort "Servico nao ficou active apos restart."
info "Servico ativo."

# ── FASE 10: Health + journal (redigido) ─────────────────────────────────────
info "Health check /fiscal/health..."
HEALTH=$(ssh_vm "curl -fsS http://127.0.0.1:${VM_PORT}/fiscal/health" 2>&1 || true)
if [ -z "$HEALTH" ]; then
  warn "Health endpoint nao respondeu — verificar logs manualmente."
else
  info "Health: ${HEALTH}"
fi

info "Journal (ultimas 20 linhas):"
ssh_vm "sudo journalctl -u ${VM_SERVICE} -n 20 --no-pager" || true

echo ""
echo "══════════════════════════════════════════════════════"
echo "  DEPLOY CONCLUIDO"
echo "  Commit  : ${LOCAL_COMMIT}"
echo "  VERSION : ${LOCAL_VERSION}"
echo "  Backup  : ${BACKUP_FILE}"
echo "══════════════════════════════════════════════════════"
