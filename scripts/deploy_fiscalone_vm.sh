#!/usr/bin/env bash
# deploy_fiscalone_vm.sh — deploy controlado Mac → VM teste/produção
#
# Uso:
#   scripts/deploy_fiscalone_vm.sh
#   scripts/deploy_fiscalone_vm.sh --dry-run
#
# Regras:
# - Nao copia .env, .venv, .git, logs nem segredos.
# - FiscalOne permanece sem certificado em repouso.
# - O certificado A1 vem do MapOne por requisicao.
set -euo pipefail

VM_USER="ubuntu"
VM_HOST="157.151.19.131"
VM_PATH="/home/ubuntu/FiscalOne"
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

info(){ echo "[INFO] $*"; }
abort(){ echo "[ABORT] $*" >&2; exit 1; }
ssh_vm(){ ssh -i "${VM_SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=15 "${VM_USER}@${VM_HOST}" "$@"; }

DIRTY=$(git status --porcelain 2>/dev/null) || abort "Nao e repo git."
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  abort "Worktree sujo. Commit/stash antes do deploy."
fi

LOCAL_COMMIT=$(git rev-parse --short HEAD)
LOCAL_SUBJECT=$(git log -1 --pretty=%s)
info "Commit local: ${LOCAL_COMMIT} ${LOCAL_SUBJECT}"
info "Destino: ${VM_USER}@${VM_HOST}:${VM_PATH}"

if $DRY_RUN; then
  info "Dry-run: nada sera alterado."
  exit 0
fi

read -rp "Digite DEPLOY para continuar: " CONFIRM
[ "$CONFIRM" = "DEPLOY" ] || abort "Deploy cancelado."

ssh_vm "mkdir -p ${VM_PATH}"
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  -e "ssh -i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=15" \
  ./ "${VM_USER}@${VM_HOST}:${VM_PATH}/"

ssh_vm "cd ${VM_PATH} && python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip >/dev/null && .venv/bin/python -m pip install -r requirements.txt"
ssh_vm "cat > ${VM_PATH}/.env <<'EENV'
FISCALONE_AMBIENTE=producao
FISCALONE_ENABLE_PRODUCAO=1
MAPONE_FISCAL_PRODUCAO_READY=1
FISCALONE_DFE_RECEBIDO_ONLY=1
FISCAL_PROVIDER=sefaz
EENV
sudo tee /etc/systemd/system/${VM_SERVICE} >/dev/null <<'ESVC'
[Unit]
Description=FiscalOne Gateway Fiscal RLogix
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/FiscalOne
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/FiscalOne/.venv/bin/python /home/ubuntu/FiscalOne/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
ESVC
sudo systemctl daemon-reload
sudo systemctl enable ${VM_SERVICE} >/dev/null
sudo systemctl restart ${VM_SERVICE}
sleep 3
systemctl is-active ${VM_SERVICE}
curl -fsS http://127.0.0.1:${VM_PORT}/fiscal/health
"

info "Deploy concluido: ${LOCAL_COMMIT}"
