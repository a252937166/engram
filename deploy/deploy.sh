#!/usr/bin/env bash
# ENGRAM one-shot deploy for a vanilla nginx + python3 host.
# Usage:  QWEN_API_KEY=sk-...  bash deploy/deploy.sh  [domain]
set -euo pipefail

DOMAIN="${1:-engram.hackthon.site}"
APP=/opt/engram
WEB=/var/www/engram
SRC="$(cd "$(dirname "$0")/.." && pwd)"

[ -n "${QWEN_API_KEY:-}" ] || { echo "QWEN_API_KEY is required" >&2; exit 1; }

id -u engram >/dev/null 2>&1 || useradd -r -s /sbin/nologin engram

mkdir -p "$APP" "$WEB" /var/lib/engram /etc/engram
cp -r "$SRC/backend" "$APP/"
cp "$SRC/frontend/index.html" "$WEB/index.html"
chown -R engram:engram /var/lib/engram

umask 077
cat > /etc/engram/engram.env <<EOF
QWEN_API_KEY=${QWEN_API_KEY}
ENGRAM_PORT=8788
ENGRAM_DAILY_LIMIT=400
EOF
chown root:engram /etc/engram/engram.env
chmod 640 /etc/engram/engram.env

cp "$SRC/deploy/engram.service" /etc/systemd/system/engram.service
cp "$SRC/deploy/nginx-engram.conf" /etc/nginx/conf.d/engram.conf
sed -i "s/engram\.hackthon\.site/${DOMAIN}/g" /etc/nginx/conf.d/engram.conf

systemctl daemon-reload
systemctl enable --now engram
nginx -t && systemctl reload nginx

echo "ENGRAM deployed: http://${DOMAIN}  (run certbot --nginx -d ${DOMAIN} for TLS)"
