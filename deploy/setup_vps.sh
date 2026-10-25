#!/usr/bin/env bash
# ==============================================================================
# Insight Studio - 1-Click Automated VPS Deployment Script
# Target Domain: insightstudio.peculiex.com
# Compatible with: Ubuntu 22.04 LTS, Ubuntu 24.04 LTS, Debian 11/12
# ==============================================================================

set -euo pipefail

DOMAIN="${1:-insightstudio.peculiex.com}"
API_KEY="${2:-${OPENROUTER_API_KEY:-}}"
APP_DIR="/opt/insight-studio"
REPO_URL="https://github.com/ankit-datatrainer/Inside-studio.git"
SERVICE_USER="studio"

echo "========================================================"
echo " Starting Insight Studio Production Deployment"
echo " Domain: ${DOMAIN}"
echo " App Directory: ${APP_DIR}"
echo "========================================================"

# 1. Verify Root/Sudo
if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run this script with sudo or as root: sudo bash $0"
  exit 1
fi

# 2. Update System & Install Core Dependencies
echo "[+] Step 1/7: Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  nginx \
  git \
  rsync \
  curl \
  build-essential \
  libfreetype6 \
  libpng16-16 \
  certbot \
  python3-certbot-nginx

# 3. Create Dedicated System Service User
echo "[+] Step 2/7: Creating dedicated system user (${SERVICE_USER})..."
if ! id "${SERVICE_USER}" &>/dev/null; then
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# 4. Clone or Update Application Repository
echo "[+] Step 3/7: Fetching application code to ${APP_DIR}..."
if [ ! -d "${APP_DIR}/.git" ]; then
  rm -rf "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull origin main
fi

# 5. Set Up Python Virtual Environment & Install Dependencies
echo "[+] Step 4/7: Installing Python dependencies into virtual environment..."
sudo -u "${SERVICE_USER}" bash -c "
  cd ${APP_DIR}
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
"

# 6. Create Storage & Configure Environment
echo "[+] Step 5/7: Configuring workspaces and production environment..."
mkdir -p /var/lib/insight-studio/workspaces
chown -R "${SERVICE_USER}:${SERVICE_USER}" /var/lib/insight-studio/workspaces
chmod 750 /var/lib/insight-studio/workspaces

# Generate secure random secret if not already set
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

if [ ! -f /etc/insight-studio.env ]; then
  cat > /etc/insight-studio.env <<EOF
SECRET_KEY=${SECRET_KEY}
WORKSPACE_DIR=/var/lib/insight-studio/workspaces
WORKSPACE_TTL_HOURS=48
MAX_UPLOAD_MB=100
SESSION_COOKIE_SECURE=1
HOST=127.0.0.1
PORT=8001
OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free
OPENROUTER_API_KEY=${API_KEY}
EOF
  chmod 600 /etc/insight-studio.env
fi

# Set local keyfile if API key was provided
if [ -n "${API_KEY}" ]; then
  echo "${API_KEY}" > "${APP_DIR}/.openrouter_key"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}/.openrouter_key"
  chmod 600 "${APP_DIR}/.openrouter_key"
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

# 7. Configure Systemd Service
echo "[+] Step 6/7: Configuring systemd service..."
cp "${APP_DIR}/deploy/insightstudio.service" /etc/systemd/system/insightstudio.service
systemctl daemon-reload
systemctl enable --now insightstudio
systemctl restart insightstudio

# 8. Configure Nginx Reverse Proxy
echo "[+] Step 7/7: Configuring Nginx reverse proxy..."
sed "s/insightstudio.peculiex.com/${DOMAIN}/g" "${APP_DIR}/deploy/insightstudio.peculiex.com.conf" > "/etc/nginx/sites-available/${DOMAIN}.conf"
ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/"
rm -f /etc/nginx/sites-enabled/default

# Test and reload Nginx
nginx -t
systemctl reload nginx

# 9. Verify Local Health
sleep 2
if curl -s "http://127.0.0.1:8001/healthz" | grep -q '"ok":true'; then
  echo "[+] Application backend is HEALTHY and running on 127.0.0.1:8001!"
else
  echo "[!] Warning: Backend health check returned unexpected response. Check 'journalctl -u insightstudio -n 50'"
fi

echo ""
echo "========================================================"
echo " Deployment Complete!"
echo " Domain: http://${DOMAIN}"
echo ""
echo " To enable HTTPS / SSL via Let's Encrypt, run:"
echo "   sudo certbot --nginx -d ${DOMAIN}"
echo "========================================================"
