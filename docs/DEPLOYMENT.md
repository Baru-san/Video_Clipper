# VPS Deployment Runbook

Target: **Debian 12** VPS, **1 vCPU / 1 GB RAM / 15 GB SSD**, serving a
React + FastAPI app with Nginx in front and systemd managing the API.

Assumed layout on the server:

```
/srv/video-clipper/
├── backend/            # this repo's backend/
├── frontend/dist/      # built React assets served by Nginx
├── data/               # VC_DATA_DIR: uploads, outputs, thumbs, tmp
└── .venv/              # Python virtualenv
```

The app is exposed as `https://<domain>/` (static) and `https://<domain>/api/` (FastAPI).

---

## Fast path (one script)

`deploy/install.sh` performs sections 1–8 in one idempotent run. Run it as root from the
server console:

```bash
# on your machine: copy the repo to the server, or clone it there, then:
sudo WEB_PORT=80 DOMAIN=example.com bash deploy/install.sh
```

It installs packages, creates the `videoclip` user, clones to `/srv/video-clipper/src`,
builds the venv + frontend, installs the systemd unit, configures Nginx, and health-checks
the API. Re-run it to update. The manual steps below are the same thing, spelled out.

### NAT VPS / VNC-only servers

If the VPS has no public IP (NAT VPS) and you only reach it through a web VNC console:

- You cannot SSH in from elsewhere; run everything in the console's terminal.
- The provider maps public ports to internal ones. Set `WEB_PORT` to the **internal** port
  that the provider forwards to (often `80`, sometimes a high port), then reach the app at
  `http://<provider-host>:<provider-public-port>/`.
- **Let's Encrypt (section 8) only works if** the provider forwards a public port 80 (and
  optionally 443) to this VM *and* a domain points at the provider's host. Otherwise skip
  HTTPS and use the mapped HTTP port, or terminate TLS at a proxy you control.
- Example for a provider mapping public `8080` → internal `80`:
  ```bash
  sudo WEB_PORT=80 DOMAIN=_ bash deploy/install.sh
  # browse to http://<provider-host>:8080/
  ```

### Recommended for NAT: Cloudflare Tunnel

On a NAT VPS the public IPv4 is **shared** — a DNS-only `A` record to that IP will not reach
your VM, because its port 80/443 belong to the provider's front host. The robust fix is a
**Cloudflare Tunnel**: `cloudflared` dials *outbound* to Cloudflare, so **no inbound port or
port-forward is needed**.

**Instant (no account, ephemeral URL):**

```bash
# install cloudflared, then:
sudo cp deploy/cloudflared-quick.service /etc/systemd/system/
sudo systemctl enable --now cloudflared-quick
journalctl -u cloudflared-quick | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```

You get a temporary `https://<random>.trycloudflare.com` URL. The URL **changes on restart**
— fine for testing, not for production.

**Permanent (your own hostname):**

1. Own a domain and add it to Cloudflare (change its nameservers to Cloudflare's). *A domain
   you don't control, or one parked elsewhere, will not work.*
2. Zero Trust → Networks → Tunnels → create a tunnel → copy the **token**.
3. On the VM:
   ```bash
   sudo cloudflared service install <TOKEN>
   sudo systemctl enable --now cloudflared
   ```
4. In the tunnel's **Public Hostname** settings, map your hostname to service
   `http://localhost:80`.
5. Done — `https://your-domain/` serves the app with Cloudflare TLS, no open ports.

Verify from anywhere:

```bash
curl -s https://<hostname>/api/health     # {"status":"ok"}
```

---

## 0. Prerequisites

- A Debian 12 (or Ubuntu 22.04+) VPS with root/sudo.
- A domain (or subdomain) whose A/AAAA record points at the VPS IP — needed for HTTPS.
- SSH access.

> **Swap:** on a 1 GB box, add 1–2 GB of swap. It prevents the OOM killer from killing
> installs or FFmpeg spikes at the cost of some disk.
> ```bash
> fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
> echo '/swapfile none swap sw 0 0' >> /etc/fstab
> ```

---

## 1. Base packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg nginx git
```

Notes:
- `ffmpeg` from Debian is **GPL**-licensed. Review §11 of `docs/TECHNICAL_GUIDE.md` before
  commercial distribution.
- Verify: `ffmpeg -version` and `ffprobe -version`.

---

## 2. Service user and directories

```bash
sudo useradd --system --create-home --home-dir /srv/video-clipper \
  --shell /usr/sbin/nologin videoclip
sudo mkdir -p /srv/video-clipper
sudo chown -R videoclip:videoclip /srv/video-clipper
```

---

## 3. Get the code

As `videoclip`:

```bash
sudo -u videoclip git clone https://github.com/Baru-san/Video_Clipper.git /srv/video-clipper/src
```

This puts the repo at `/srv/video-clipper/src`. The systemd unit expects `backend/` and
`frontend/` directly under `/srv/video-clipper`. Choose one layout and stay consistent:

**Option A (symlinks, keeps git history clean):**
```bash
sudo -u videoclip ln -s /srv/video-clipper/src/backend  /srv/video-clipper/backend
sudo -u videoclip ln -s /srv/video-clipper/src/frontend /srv/video-clipper/frontend
```

**Option B (deploy only the needed dirs):** copy `backend/` and `frontend/` up a level and
drop the clone. Simpler for manual deploys, harder to update from git.

The commands below assume Option A.

---

## 4. Backend: venv and dependencies

```bash
sudo -u videoclip python3 -m venv /srv/video-clipper/.venv
sudo -u videoclip /srv/video-clipper/.venv/bin/pip install --upgrade pip
sudo -u videoclip /srv/video-clipper/.venv/bin/pip install -r /srv/video-clipper/backend/requirements.txt
```

Runtime config is passed by systemd (see `deploy/video-clipper.service`), not by a `.env`
file. Key knobs:

| Env var | Default | Meaning |
| --- | --- | --- |
| `VC_DATA_DIR` | `/srv/video-clipper/data` | runtime media storage |
| `VC_MAX_UPLOAD_BYTES` | `524288000` (500 MB) | hard upload cap |
| `VC_RETENTION_HOURS` | `24` | TTL before cleanup deletes files |
| `VC_MIN_FREE_BYTES` | `1073741824` (1 GB) | reject uploads below this free disk |
| `VC_MAX_QUEUE_SIZE` | `20` | max pending jobs (429 beyond) |

---

## 5. Frontend: build

**Preferred — build in CI or locally, ship only `dist/`:**

```bash
# on your machine, from frontend/
pnpm install
pnpm build
rsync -az --delete frontend/dist/ videoclip@<vps>:/srv/video-clipper/frontend/dist/
```

This avoids installing Node on a 1 GB VPS.

**Alternative — build on the server** (needs Node 18+; can be memory-tight):

```bash
sudo apt install -y nodejs npm
cd /srv/video-clipper/frontend && npm install && npm run build
```

After building, `dist/` contains `index.html`, `assets/*.js`, `assets/*.css`.

---

## 6. systemd service

```bash
sudo cp /srv/video-clipper/src/deploy/video-clipper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now video-clipper
sudo systemctl status video-clipper
```

The unit already:
- runs `uvicorn --workers 1` (correct for 1 vCPU),
- caps memory at `MemoryMax=700M` / `MemoryHigh=600M` and CPU at `CPUQuota=90%`,
- hardens with `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, and only
  `ReadWritePaths=/srv/video-clipper/data`.

Check startup logs — you should see the ffmpeg/ffprobe versions logged:

```bash
journalctl -u video-clipper -n 40 --no-pager
```

Direct API test (bypassing Nginx):

```bash
curl -s http://127.0.0.1:8000/api/health      # {"status":"ok"}
```

---

## 7. Nginx

```bash
sudo cp /srv/video-clipper/src/deploy/nginx.conf /etc/nginx/sites-available/video-clipper
sudo ln -s /etc/nginx/sites-available/video-clipper /etc/nginx/sites-enabled/video-clipper
sudo rm -f /etc/nginx/sites-enabled/default
# edit server_name to your domain first:
sudo nano /etc/nginx/sites-available/video-clipper
sudo nginx -t
sudo systemctl reload nginx
```

Critical bits already in the config:
- `client_max_body_size 500m` — must be ≥ `VC_MAX_UPLOAD_BYTES`.
- `proxy_buffering off` — **required** or SSE progress will not stream.
- `proxy_read_timeout 3600s` — long encodes.
- `location /` serves `frontend/dist`; `location /api/` proxies to FastAPI.

---

## 8. HTTPS (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <your-domain>
```

Certbot rewrites the Nginx server block for TLS and installs a renewal timer. Test renewal:

```bash
sudo certbot renew --dry-run
```

---

## 9. End-to-end smoke test

```bash
curl -s https://<your-domain>/api/health
curl -sI https://<your-domain>/                    # 200 text/html (React app)
```

Then in a browser: upload a short MP4, set in/out, pick **Lossless**, create clip, watch the
progress bar, and download the result. Repeat with **Precise** (re-encode) — slower on 1 vCPU.

---

## 10. Updates and rollback

```bash
cd /srv/video-clipper/src
sudo -u videoclip git fetch --tags
sudo -u videoclip git checkout <tag-or-commit>
# backend deps changed?
sudo -u videoclip /srv/video-clipper/.venv/bin/pip install -r backend/requirements.txt
# frontend changed?
rsync -az --delete <built>/dist/ /srv/video-clipper/frontend/dist/
sudo systemctl restart video-clipper
```

Rollback = check out the previous commit/tag and restart. Keep the last known-good tag noted.

---

## 11. Operations

- **Logs:** `journalctl -u video-clipper -f`; Nginx logs in
  `/var/log/nginx/video-clipper.*.log`.
- **Disk (15 GB):** the app TTL-sweeps `data/` hourly and refuses uploads under
  `VC_MIN_FREE_BYTES`. Watch with `df -h /` and `du -sh /srv/video-clipper/data/*`.
- **Log growth:** configure logrotate for the Nginx logs; unbounded logs can fill the disk.
- **Memory:** `systemctl status video-clipper` shows cgroup usage against the 700 MB cap;
  `systemd-cgtop` for live view.
- **Certbot renewal:** `systemctl list-timers | grep certbot`.

---

## 12. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Nginx won't start: `bind() to 0.0.0.0:80 failed (98: Address already in use)` | Apache (or another server) owns port 80 — the installer disables `apache2`; verify with `ss -tlnp \| grep :80` |
| Journal spam: `Failed to set 'cpu.cfs_quota_us' ... Invalid argument` | `CPUQuota` is unsupported in OpenVZ/Virtuozzo containers; the unit no longer sets it |
| Service won't start, "ffmpeg not found" | `ffmpeg` not installed or PATH not visible to systemd — install it; log shows the resolved path |
| `502 Bad Gateway` | API not running: `systemctl status video-clipper`, check `journalctl` |
| Upload fails at `client_max_body_size` | Nginx cap lower than app cap — raise `client_max_body_size` |
| Progress bar never updates | `proxy_buffering` not `off`, or a proxy in front of Nginx buffering SSE |
| `429` on clip creation | Queue full (`VC_MAX_QUEUE_SIZE`) or an encode is stuck |
| Job `failed`, opaque error | FFmpeg stderr is captured — inspect `journalctl`; error is surfaced in `job.error` |
| OOM kill during re-encode | Expected behavior above 700 MB; use Lossless, downscale, or raise the cap only if RAM allows |
| Disk fills up | Lower `VC_RETENTION_HOURS`, raise `VC_MIN_FREE_BYTES`, check for stuck jobs |

---

## 13. Security checklist

- [ ] Nginx serves TLS; HTTP redirects to HTTPS (certbot default).
- [ ] API bound to `127.0.0.1` only (never `0.0.0.0`).
- [ ] `videoclip` is a system user with `nologin`.
- [ ] systemd hardening flags active (`ProtectSystem=strict`, `NoNewPrivileges`, etc.).
- [ ] No secrets in the repo; runtime config via systemd `Environment=`.
- [ ] Upload caps enforced at both Nginx and the app.
- [ ] TTL cleanup + disk guard active.
- [ ] FFmpeg licensing reviewed (Debian build is GPL).
- [ ] Consider rate limiting (`limit_req`) if exposed publicly.

Optional Nginx rate limiting for the API:

```nginx
limit_req_zone $binary_remote_addr zone=vc_api:10m rate=30r/m;
location /api/ {
    limit_req zone=vc_api burst=10 nodelay;
    # ... proxy settings ...
}
```
