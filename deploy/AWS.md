# Deploy on AWS

The simplest reliable home for Insight Studio on AWS is a single **EC2 instance**
(or **Lightsail instance**) running Docker or gunicorn, with nginx and HTTPS in
front.

The app writes uploads, cleaned datasets, charts and exports to local disk, so a
normal EC2/Lightsail instance with an EBS volume is a perfect fit. Avoid
platforms with an ephemeral filesystem (App Runner, ECS Fargate without EFS) if
users need their workspace to survive restarts.

**Recommended:** `t3.small` (2 vCPU / 2 GB) or `t3.medium` for heavier files,
Ubuntu 24.04 LTS, 20+ GB gp3 disk.

---

## Option A — EC2 + Docker + nginx (recommended)

### 1. Launch the instance

1. EC2 → **Launch instance**.
2. Name: `insight-studio`.
3. AMI: **Ubuntu Server 24.04 LTS**.
4. Instance type: `t3.small`.
5. Key pair: create/download one (needed for SSH).
6. Storage: 20 GB gp3 (increase if users upload large files).
7. Security group — inbound rules:

   | Port | Source | Purpose |
   |---|---|---|
   | 22 | Your IP | SSH |
   | 80 | 0.0.0.0/0, ::/0 | HTTP (redirects to HTTPS) |
   | 443 | 0.0.0.0/0, ::/0 | HTTPS |

   Do **not** expose 5000/8000 to the internet.
8. Launch, then associate an **Elastic IP** so the address survives restarts.

### 2. Point a domain at the instance

In your DNS provider, create:

```
A     insight.example.com     ->  ELASTIC_IP
```

Wait for DNS to resolve (`nslookup insight.example.com`).

### 3. Connect and install Docker

```bash
ssh -i your-key.pem ubuntu@ELASTIC_IP

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
sudo usermod -aG docker ubuntu
```

Log out and back in (or `newgrp docker`) so the group change applies.

### 4. Upload and start the app

From your workstation:

```bash
scp -i your-key.pem -r insight-studio ubuntu@ELASTIC_IP:/home/ubuntu/
```

On the server:

```bash
cd /home/ubuntu/insight-studio
cp docker-compose.yml docker-compose.yml.bak

# Set a strong secret in docker-compose.yml (edit the SECRET_KEY line)
nano docker-compose.yml

docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/healthz
```

Generate the secret with:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

The workspace lives in the `studio-data` Docker volume. It survives container
restarts and rebuilds.

### 5. nginx reverse proxy

```bash
sudo cp /home/ubuntu/insight-studio/deploy/nginx.conf \
        /etc/nginx/sites-available/insight-studio
sudo sed -i 's/example.com/insight.example.com/g' \
        /etc/nginx/sites-available/insight-studio
sudo sed -i 's#http://127.0.0.1:8001#http://127.0.0.1:8000#' \
        /etc/nginx/sites-available/insight-studio
sudo ln -sf /etc/nginx/sites-available/insight-studio \
            /etc/nginx/sites-enabled/insight-studio
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

The template has the optional `/static/` block commented out, which is correct
for Docker.

### 6. HTTPS

```bash
sudo certbot --nginx -d insight.example.com
```

Choose the redirect option. Test auto-renewal:

```bash
sudo certbot renew --dry-run
```

Set `SESSION_COOKIE_SECURE=1` in `docker-compose.yml`, then restart:

```bash
docker compose up -d
```

### 7. Updates

```bash
cd /home/ubuntu/insight-studio
# upload/copy the new code over the old files
docker compose up -d --build
```

---

## Option B — EC2 + native gunicorn + systemd

Use the generic VPS guide with the same instance and security-group setup:

→ [VPS.md](VPS.md)

This avoids Docker but requires Python, gunicorn and a systemd unit on the host.
Use `/opt/insight-studio` and the provided unit file from the VPS guide.

---

## Option C — Lightsail

Lightsail is the lowest-friction AWS option:

1. Create an Ubuntu 24.04 instance (2 GB plan recommended).
2. Attach a **static IP**.
3. Networking → open ports **80** and **443** (SSH 22 is open by default);
   leave 8000 closed.
4. Point your domain's A record at the static IP.
5. Follow Option A from step 3 onward (Docker + nginx + certbot).

Lightsail snapshots can back up the whole instance and its workspace volume.

---

## App Runner / ECS Fargate caveat

The provided Docker image runs on these services, but their default filesystem
is **ephemeral**. Each deploy/restart loses `workspaces/` unless you:

- mount an **EFS** access point at `/data/workspaces`, and
- run a single task (or add sticky sessions) because the profile cache and
  workspace files are local.

For a simple internal tool, EC2 or Lightsail is usually cheaper and easier.

---

## Backups and operations

- **Workspace data:** Docker volume `studio-data` (or
  `/var/lib/insight-studio/workspaces` in a native install). It contains
  uploaded files and exports; back up if you need retention beyond
  `WORKSPACE_TTL_HOURS`.
- **Code/config:** keep the repository and `docker-compose.yml` in git or a
  private bucket. Secrets belong in AWS Secrets Manager / SSM Parameter Store
  if you automate deployments.
- **Disk alarms:** create a CloudWatch alarm on EBS free space, especially if
  uploads are large.
- **Instance protection:** disable “Delete on termination” for the EBS volume
  if the instance is ephemeral, or take regular EBS snapshots.
- **Access:** restrict SSH to your IP; keep 80/443 as the only public ports.

## Troubleshooting

| Symptom | Fix |
|---|---|
| 502 from nginx | `docker compose ps` and `docker compose logs -f`; is port 8000 up? |
| 413 upload | `client_max_body_size` in nginx and `MAX_UPLOAD_MB` in compose |
| Data disappeared after redeploy | workspace volume missing/misconfigured; check `docker volume ls` |
| Certbot fails | DNS must point at the Elastic IP and port 80 must be open |
| Out of memory | use a larger instance, or reduce gunicorn workers in the Dockerfile command |
