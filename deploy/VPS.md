# Deploy Insight Studio on VPS

**Domain:** `insightstudio.peculiex.com`  
**Repository:** `https://github.com/ankit-datatrainer/Inside-studio`  
**Platform:** Ubuntu 22.04 / 24.04 LTS or Debian 11/12

---

## Method 1: Automated 1-Click Setup (Recommended)

SSH into your VPS as `root` (or a sudo user) and execute:

```bash
# 1. Clone repository
git clone https://github.com/ankit-datatrainer/Inside-studio.git /tmp/Inside-studio

# 2. Run automated setup script
sudo bash /tmp/Inside-studio/deploy/setup_vps.sh insightstudio.peculiex.com

# 3. Enable free SSL Certificate (HTTPS)
sudo certbot --nginx -d insightstudio.peculiex.com
```

That's it! Your application is now live at **https://insightstudio.peculiex.com**.

---

## Method 2: Step-by-Step Manual Setup

If you prefer to run each command step-by-step:

### Step 1: Point your DNS Record to VPS
Log in to your DNS provider (Cloudflare, GoDaddy, Namecheap, Hostinger, etc.):
- **Type**: `A`
- **Name**: `insightstudio`
- **IPv4 Address**: `YOUR_VPS_IP`
- **TTL**: Auto / 300 seconds

---

### Step 2: Install System Packages on VPS
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx git curl \
                    build-essential libfreetype6 libpng16-16 certbot python3-certbot-nginx
```

---

### Step 3: Create Service User & Clone Repository
```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin studio || true
sudo git clone https://github.com/ankit-datatrainer/Inside-studio.git /opt/insight-studio
sudo chown -R studio:studio /opt/insight-studio
```

---

### Step 4: Set Up Virtual Environment & Dependencies
```bash
sudo -u studio bash -c '
  cd /opt/insight-studio
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
'
```

---

### Step 5: Configure Storage & Production Environment
```bash
# Create persistent workspaces folder
sudo mkdir -p /var/lib/insight-studio/workspaces
sudo chown -R studio:studio /var/lib/insight-studio/workspaces
sudo chmod 750 /var/lib/insight-studio/workspaces

# Create environment configuration
sudo tee /etc/insight-studio.env > /dev/null <<'EOF'
SECRET_KEY=e8391b4a89df2c4e1279a0b3f81e6492d5c3170a48e291bf05273d4a6c8e9f1a
WORKSPACE_DIR=/var/lib/insight-studio/workspaces
WORKSPACE_TTL_HOURS=48
MAX_UPLOAD_MB=100
SESSION_COOKIE_SECURE=1
HOST=127.0.0.1
PORT=8001
OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
EOF

sudo chmod 600 /etc/insight-studio.env
```

---

### Step 6: Create & Start Systemd Service
```bash
sudo cp /opt/insight-studio/deploy/insightstudio.service /etc/systemd/system/insightstudio.service
sudo systemctl daemon-reload
sudo systemctl enable --now insightstudio
sudo systemctl status insightstudio --no-pager
```

Check health locally on VPS:
```bash
curl http://127.0.0.1:8001/healthz
# Expected output: {"app":"Insight Studio","ok":true,...}
```

---

### Step 7: Configure Nginx & Open Firewall
```bash
sudo cp /opt/insight-studio/deploy/insightstudio.peculiex.com.conf /etc/nginx/sites-available/insightstudio.peculiex.com.conf
sudo ln -sf /etc/nginx/sites-available/insightstudio.peculiex.com.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# Allow ports in UFW firewall
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
```

---

### Step 8: Issue Free SSL Certificate (HTTPS)
```bash
sudo certbot --nginx -d insightstudio.peculiex.com
```
Follow the prompt (enter your email, choose `Y` to agree, and select automatic HTTPS redirect).

---

## Helpful Commands

- **Check Application Logs**:
  ```bash
  sudo journalctl -u insightstudio -f
  ```
- **Restart Application**:
  ```bash
  sudo systemctl restart insightstudio
  ```
- **Update to Latest Code from GitHub**:
  ```bash
  sudo -u studio git -C /opt/insight-studio pull origin main
  sudo -u studio /opt/insight-studio/.venv/bin/pip install -r /opt/insight-studio/requirements.txt
  sudo systemctl restart insightstudio
  ```
