# Deploy on Hostinger

## Which Hostinger product?

| Plan | Suitable? | Why |
|---|---|---|
| **VPS** (KVM 1/2/4, Ubuntu template) | **Yes — recommended** | Full root access, persistent disk, long-running Python/Docker process |
| **Shared / Web Hosting** | **Usually no** | Designed for PHP/WordPress; Flask needs a persistent WSGI process and pandas/matplotlib need memory that shared limits rarely allow |
| **Cloud / WordPress hosting** | Usually no | Same process/memory constraints |

This app is not a set of static files. It runs pandas and matplotlib in a Python
process, writes charts/PDFs to disk and keeps per-user workspaces. A VPS is the
right Hostinger product.

If you already have a Hostinger VPS, use this guide. For the generic
systemd/nginx version (without Docker), see [VPS.md](VPS.md).

---

## 1. Create the VPS

1. hPanel → **VPS** → **Create VPS**.
2. Choose **KVM 2** (2 vCPU / 2 GB RAM) or larger. KVM 1 works for light use with
   one worker, but 2 GB is safer for pandas + matplotlib.
3. Operating system: **Ubuntu 24.04 LTS**. If Hostinger offers an
   **Ubuntu 24.04 with Docker** template, choose it and skip step 3.
4. Choose a datacenter close to your users.
5. Set a root password or SSH key, then create the server.
6. Note the **public IPv4 address** shown in the VPS overview.

### Firewall

In hPanel → VPS → **Firewall** (or the Security tab), allow:

- `22/tcp` — SSH (ideally restricted to your IP)
- `80/tcp` — HTTP
- `443/tcp` — HTTPS

Do **not** open 5000 or 8000 publicly.

---

## 2. Connect

Use the **Browser terminal** in hPanel, or SSH from your computer:

```bash
ssh root@YOUR_VPS_IP
```

Create a non-root user (recommended):

```bash
adduser studio
usermod -aG sudo studio
su - studio
```

---

## 3. Install Docker (skip if the Docker template is preinstalled)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker studio
```

Log out/in, then verify:

```bash
docker run --rm hello-world
docker compose version
```

---

## 4. Upload the app

From your workstation:

```bash
scp -r insight-studio studio@YOUR_VPS_IP:/home/studio/
```

Or clone from your repository on the server:

```bash
git clone YOUR_REPO_URL /home/studio/insight-studio
```

Start it:

```bash
cd /home/studio/insight-studio

# Set a long random SECRET_KEY in docker-compose.yml
nano docker-compose.yml

docker compose up -d --build
curl http://127.0.0.1:8000/healthz
```

Generate a secret:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

---

## 5. Point your domain

In hPanel → **Domains** → your domain → **DNS / Nameservers**:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `@` | `YOUR_VPS_IP` | 3600 |
| A | `www` | `YOUR_VPS_IP` | 3600 |

If the domain is registered elsewhere, add the same A records at that
registrar. Wait for propagation:

```bash
nslookup yourdomain.com
```

---

## 6. nginx + HTTPS

```bash
cd /home/studio/insight-studio

sudo cp deploy/nginx.conf /etc/nginx/sites-available/insight-studio
sudo sed -i 's/example.com/yourdomain.com/g' /etc/nginx/sites-available/insight-studio
sudo sed -i 's#http://127.0.0.1:8001#http://127.0.0.1:8000#' \
        /etc/nginx/sites-available/insight-studio
sudo ln -sf /etc/nginx/sites-available/insight-studio /etc/nginx/sites-enabled/insight-studio
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Choose “redirect HTTP to HTTPS”. Then edit `docker-compose.yml`:

```yaml
SESSION_COOKIE_SECURE: "1"
```

and restart:

```bash
docker compose up -d
```

Open `https://yourdomain.com`.

---

## 7. Updates and backups

Update:

```bash
cd /home/studio/insight-studio
# upload/copy the new files
docker compose up -d --build
```

Back up:

- The Docker volume `studio-data` (uploaded files + generated exports).
- `docker-compose.yml` and your repository.
- Hostinger VPS snapshots/backups from hPanel for whole-server recovery.

Housekeeping:

```bash
docker compose logs -f --tail=100
docker volume inspect insight-studio_studio-data
du -sh /var/lib/docker/volumes/insight-studio_studio-data/_data
```

Workspaces older than `WORKSPACE_TTL_HOURS` (default 48) are removed
automatically.

---

## Shared hosting: why it is not recommended

A Flask app must stay running to answer requests. Shared hosting typically:

- runs PHP-FPM/Apache with per-request execution limits;
- does not allow a long-running gunicorn/waitress process;
- limits memory below what pandas + matplotlib comfortably need;
- may block native build/install of scientific Python packages.

Even if a plan advertises “Python support”, confirm that it provides:

1. SSH access,
2. Python 3.10+,
3. a WSGI/Passenger entry point with a persistent process,
4. at least ~1 GB RAM for the app.

Without all four, the app will fail or be killed during chart/PDF generation.
Upgrade to a Hostinger VPS and use the steps above.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` | Use the Docker Ubuntu template, or repeat step 3 and log in again |
| 502 Bad Gateway | `docker compose ps` / `docker compose logs`; check the container is on port 8000 |
| 413 on upload | Increase `MAX_UPLOAD_MB` and `client_max_body_size` together |
| Domain shows default page | DNS not propagated, or the default nginx site is still enabled |
| Certbot cannot validate | Port 80 blocked in the Hostinger firewall, or DNS points elsewhere |
| Container restarts/OOM | Upgrade to KVM 2+; check `docker stats` while exporting a PDF |
