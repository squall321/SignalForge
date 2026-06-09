# SignalForge Nginx — On-prem Deployment

Reverse-proxy + static SPA hosting for SignalForge dashboard.
Protected by **HTTP Basic Auth + IP whitelist** (P1~P3).

```
                          ┌─ /dashboard/  → /var/www/signalforge/dashboard/ (Vite build)
client (intranet)  ─►  Nginx ─┼─ /api/        → 127.0.0.1:8000 (FastAPI)
                          ├─ /ws/         → 127.0.0.1:8000 (WebSocket upgrade)
                          └─ /docs|/redoc|/openapi.json → 127.0.0.1:8000
```

## Files

| Path                                | Purpose                                         |
|-------------------------------------|-------------------------------------------------|
| `nginx/dashboard.conf`              | Site config — copy to `/etc/nginx/sites-available/` |
| `nginx/htpasswd-sf.example`         | Example Basic Auth file + htpasswd command crib |
| `.github/workflows/deploy.yml`      | CI: `npm run build` + `rsync` to server         |
| `nginx/nginx.conf`                  | (legacy) docker-compose nginx — unrelated       |

---

## 1. One-time server setup

```bash
# Web root
sudo mkdir -p /var/www/signalforge/dashboard
sudo chown -R www-data:www-data /var/www/signalforge

# Apache utils for htpasswd
sudo apt-get install -y apache2-utils

# Create FIRST Basic Auth user (creates the file)
sudo htpasswd -B -c /etc/nginx/htpasswd-sf signalforge_admin
# add more users WITHOUT -c
sudo htpasswd -B    /etc/nginx/htpasswd-sf analyst1

# Lock down the file
sudo chown root:www-data /etc/nginx/htpasswd-sf
sudo chmod 640           /etc/nginx/htpasswd-sf
```

## 2. Install the site config

```bash
sudo cp /home/koopark/claude/SignalForge/nginx/dashboard.conf \
        /etc/nginx/sites-available/signalforge.conf

sudo ln -sf /etc/nginx/sites-available/signalforge.conf \
            /etc/nginx/sites-enabled/signalforge.conf

# Edit IP whitelist + server_name before reload
sudo nano /etc/nginx/sites-available/signalforge.conf
#   uncomment allow 10.0.0.0/8; etc.
#   set $sf_acl ... / deny all;

sudo nginx -t                       # MUST be ok
sudo systemctl reload nginx
```

## 3. Verify locally

```bash
# 401 expected — proves auth is on
curl -sI http://signalforge.internal/dashboard/ | head -1

# 200 expected (with creds)
curl -su signalforge_admin:'<pw>' http://signalforge.internal/dashboard/ \
     | head -5

# API smoke
curl -su signalforge_admin:'<pw>' http://signalforge.internal/api/health
```

## 4. Hooking up GitHub Actions

In repo Settings → Secrets and variables → Actions, add:

| Secret            | Example value                                                   |
|-------------------|-----------------------------------------------------------------|
| `DEPLOY_HOST`     | `signalforge.internal` (or LAN IP)                              |
| `DEPLOY_USER`     | `deploy`                                                        |
| `SSH_KEY`         | full private key (PEM/OpenSSH), authorised on the server        |
| `SSH_KNOWN_HOSTS` | output of `ssh-keyscan signalforge.internal` (one or more lines)|

Server-side prep:

```bash
# Create deploy user with rsync rights to the web root
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG www-data deploy
sudo chown -R deploy:www-data /var/www/signalforge
sudo chmod -R g+rwX /var/www/signalforge

# Install the matching public key
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy bash -c 'cat >> /home/deploy/.ssh/authorized_keys' <<EOF
ssh-ed25519 AAAA... deploy@github
EOF
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

Pushing to `main` with changes under `frontend/**` will now:

1. `npm ci && npm run build -- --base=/dashboard/`
2. `rsync -avz --delete dist/ deploy@host:/var/www/signalforge/dashboard/`
3. HTTP probe → 200/401 acceptable.

## 5. Updating Basic Auth users

```bash
sudo htpasswd -B   /etc/nginx/htpasswd-sf <user>     # add or change pw
sudo htpasswd -D   /etc/nginx/htpasswd-sf <user>     # delete
sudo systemctl reload nginx                          # not required, but safe
```

## 6. Troubleshooting

| Symptom                            | Check                                                       |
|------------------------------------|-------------------------------------------------------------|
| 403 from inside the LAN            | IP not in `allow` list — edit `dashboard.conf`              |
| 401 loop in browser                | wrong creds / browser cached old user → `chrome://settings` |
| 502 on `/api/`                     | FastAPI not on `127.0.0.1:8000` — `ss -ltnp \| grep 8000`  |
| Static assets 404 under /dashboard | Build done without `--base=/dashboard/`                     |
| WS disconnects every 60 s          | `proxy_read_timeout` too low — already 3600s here           |
