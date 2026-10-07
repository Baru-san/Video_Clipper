#!/usr/bin/env bash
#
# Video Clipper VPS installer (Debian 12 / Ubuntu 22.04+).
# Idempotent: safe to re-run. Intended to be run as root from the machine's
# console (e.g. a NAT VPS VNC terminal).
#
# Usage:
#   sudo REPO_URL=https://github.com/Baru-san/Video_Clipper.git \
#        WEB_PORT=80 DOMAIN=example.com bash deploy/install.sh
#
# Env overrides:
#   REPO_URL   git clone URL            (default: the project repo)
#   APP_DIR    install root            (default: /srv/video-clipper)
#   WEB_PORT   nginx listen port       (default: 80)
#   DOMAIN     server_name / HTTPS name (default: _)

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Baru-san/Video_Clipper.git}"
APP_DIR="${APP_DIR:-/srv/video-clipper}"
WEB_PORT="${WEB_PORT:-80}"
DOMAIN="${DOMAIN:-_}"
SERVICE_USER="videoclip"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "run as root (sudo bash deploy/install.sh)"

log "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg nginx git rsync >/dev/null

command -v ffmpeg >/dev/null || die "ffmpeg installation failed"

log "Creating service user and directories"
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${APP_DIR}" \
    --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
mkdir -p "${APP_DIR}/data"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

log "Fetching code into ${APP_DIR}/src"
if [[ -d "${APP_DIR}/src/.git" ]]; then
  sudo -u "${SERVICE_USER}" git -C "${APP_DIR}/src" fetch --all --prune
  sudo -u "${SERVICE_USER}" git -C "${APP_DIR}/src" pull --ff-only || true
else
  sudo -u "${SERVICE_USER}" git clone "${REPO_URL}" "${APP_DIR}/src"
fi

log "Linking backend/ and frontend/"
ln -sfn "${APP_DIR}/src/backend" "${APP_DIR}/backend"
ln -sfn "${APP_DIR}/src/frontend" "${APP_DIR}/frontend"

log "Setting up Python virtualenv"
[[ -d "${APP_DIR}/.venv" ]] || sudo -u "${SERVICE_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install -q \
  -r "${APP_DIR}/backend/requirements.txt"

log "Building frontend"
if command -v npm >/dev/null 2>&1; then
  if [[ ! -d "${APP_DIR}/frontend/node_modules" ]]; then
    sudo -u "${SERVICE_USER}" bash -c "cd '${APP_DIR}/frontend' && npm install"
  fi
  sudo -u "${SERVICE_USER}" bash -c "cd '${APP_DIR}/frontend' && npm run build"
else
  printf '\033[1;33mWARN:\033[0m npm not found. Build elsewhere and rsync dist/:\n'
  printf '      rsync -az --delete frontend/dist/ user@host:%s/frontend/dist/\n' "${APP_DIR}"
fi

log "Installing systemd service"
install -m 0644 "${APP_DIR}/src/deploy/video-clipper.service" \
  /etc/systemd/system/video-clipper.service
systemctl daemon-reload
systemctl enable --now video-clipper
systemctl restart video-clipper

log "Installing nginx site (listen ${WEB_PORT}, server_name ${DOMAIN})"
NGINX_CONF=/etc/nginx/sites-available/video-clipper
sed -e "s/listen 80;/listen ${WEB_PORT};/" \
    -e "s/server_name example.com;/server_name ${DOMAIN};/" \
    "${APP_DIR}/src/deploy/nginx.conf" > "${NGINX_CONF}"
ln -sfn "${NGINX_CONF}" /etc/nginx/sites-enabled/video-clipper
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "Waiting for the API to come up"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
  log "API healthy. Open http://<server>:${WEB_PORT}/"
else
  log "API did not become healthy; check: journalctl -u video-clipper -n 50"
fi

log "Done."
