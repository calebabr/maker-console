# Optional VM path (any Ubuntu 22.04/24.04 VM)

The main deployment is **Render free** (`render.yaml`). This folder is for running the same app on a VM
later (Google e2-micro, Oracle Always Free, a home server...). Same code, same env vars
(`deploy/env.example`), same Atlas database. On a VM the service never sleeps, so the cron-job.org pinger
is optional; the runner's own timer still drives passes.

**Cost note:** a Google Cloud e2-micro VM is in the Always Free tier (US regions), but its **external IPv4
address is billed (~$3.65/month)** once the trial credit ends. That is why the main plan uses Render.

Never run two deployments (Render + VM) against the same Atlas database at the same time: that is two
runners placing orders. Suspend the Render service first.

## Files
| File | Goes to | Purpose |
|---|---|---|
| `kalshi-control.service` | `/etc/systemd/system/` | uvicorn on 127.0.0.1:8000 as user `kalshi`, `Restart=always`, sandboxed |
| `Caddyfile` | `/etc/caddy/Caddyfile` | HTTPS (automatic Let's Encrypt) → 127.0.0.1:8000 |

## Steps

Needs a domain (or a free `*.duckdns.org` name) with an A record pointing at the VM's public IP, and the
firewall open for TCP 80 + 443 only (SSH as your provider recommends).

```bash
# 1. Packages
sudo apt update && sudo apt install -y python3.11 python3.11-venv git   # 3.11: use deadsnakes PPA if missing
# Caddy: follow https://caddyserver.com/docs/install#debian-ubuntu-raspbian

# 2. Unprivileged service user + code
sudo useradd --system --home /opt/kalshi-control --shell /usr/sbin/nologin kalshi
sudo git clone https://github.com/<you>/<repo>.git /opt/kalshi-control
sudo python3.11 -m venv /opt/kalshi-control/.venv
sudo /opt/kalshi-control/.venv/bin/pip install --require-hashes --only-binary :all: --no-deps \
  -r /opt/kalshi-control/control_api/requirements.txt   # hash-pinned lock
sudo chown -R root:root /opt/kalshi-control
sudo mkdir -p /opt/kalshi-control/kalshi_bot/logs
sudo chown -R kalshi:kalshi /opt/kalshi-control/kalshi_bot   # the only writable dir

# 3. Secrets (never in git)
sudo mkdir -p /etc/kalshi
sudo cp /opt/kalshi-control/deploy/env.example /etc/kalshi/bot.env
sudo nano /etc/kalshi/bot.env          # fill in real values; PUBLIC_URL=https://<your-domain>
sudo chown root:kalshi /etc/kalshi/bot.env
sudo chmod 640 /etc/kalshi/bot.env

# 4. Service
sudo cp /opt/kalshi-control/deploy/vm/kalshi-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-control
curl -s http://127.0.0.1:8000/healthz   # {"ok":true}

# 5. HTTPS
sudo cp /opt/kalshi-control/deploy/vm/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/api.example.com/<your-domain>/' /etc/caddy/Caddyfile
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo systemctl reload caddy
curl -s https://<your-domain>/healthz
```

Then:
- Vercel → project → Environment Variables → `VITE_API_BASE_URL=https://<your-domain>`.
- `control_app/vercel.json` → `connect-src`: replace the API origin with `https://<your-domain>` (exact origin,
  see `deploy/vercel.md`), commit, push → Vercel redeploys.
- Atlas → Network Access: add the VM's static IP and remove `0.0.0.0/0` (if nothing else needs it).
- cron-job.org job (if kept): point it at `https://<your-domain>/api/tick` (the app's Server page → Sync now does this when `PUBLIC_URL` is set).

## Docker alternative
```bash
sudo docker build -t kalshi-control /opt/kalshi-control
sudo docker run -d --name kalshi-control --restart=always -p 127.0.0.1:8000:8000 \
  --env-file /etc/kalshi/bot.env kalshi-control
```
(`--env-file` takes `KEY=value` lines literally: keep the PEM on one line with `\n`.)

## Operations
```bash
sudo systemctl status kalshi-control
sudo journalctl -u kalshi-control -f                # logs (secrets are redacted by the app)
cd /opt/kalshi-control && sudo git pull && sudo /opt/kalshi-control/.venv/bin/pip install --require-hashes --only-binary :all: --no-deps -r control_api/requirements.txt \
  && sudo chown -R kalshi:kalshi kalshi_bot && sudo systemctl restart kalshi-control   # update
```
Emergency stop without the app: `sudo -u kalshi touch /opt/kalshi-control/kalshi_bot/MAKER_KILL_SWITCH`
(then `sudo systemctl stop kalshi-control` if needed).
