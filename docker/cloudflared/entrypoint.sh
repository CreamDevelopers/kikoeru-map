#!/bin/sh
set -eu
if [ -z "${TUNNEL_TOKEN:-}" ]; then
  echo '{"level":"warn","msg":"TUNNEL_TOKEN is not set; Cloudflare Tunnel is disabled (local development mode)"}'
  touch /tmp/tunnel-disabled
  exec sleep infinity
fi
exec cloudflared tunnel --no-autoupdate --metrics 0.0.0.0:2000 run
