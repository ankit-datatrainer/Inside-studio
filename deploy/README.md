# Deployment guides

Insight Studio is one Python process with local-disk storage for uploads,
cleaned data, charts and exports. There is no database, worker or external API
to configure.

| Where | Guide | Recommended approach |
|---|---|---|
| AWS | [AWS.md](AWS.md) | EC2 or Lightsail + Docker (or gunicorn + systemd) |
| Hostinger | [HOSTINGER.md](HOSTINGER.md) | Hostinger **VPS**; shared hosting is usually unsuitable |
| Any VPS | [VPS.md](VPS.md) | Ubuntu + gunicorn + systemd + nginx + Let's Encrypt |
| nginx | [nginx.conf](nginx.conf) | Reverse proxy template with upload-size and timeout settings |

Before going public:

1. Set a strong `SECRET_KEY`.
2. Serve over HTTPS and set `SESSION_COOKIE_SECURE=1`.
3. Keep the `workspaces/` directory on persistent storage.
4. Increase `WORKSPACE_TTL_HOURS` or clean old workspaces if disk is small.
5. Do not expose port 5000/8000 directly to the internet; use nginx.

Health check endpoint: `GET /healthz` returns `{"ok": true, ...}`.
