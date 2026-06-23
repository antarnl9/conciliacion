#!/usr/bin/env bash
# dev-stop.sh — mata limpio el túnel SSM y la API.
set -euo pipefail
cd "$(dirname "$0")"

TUNNEL_PID=/tmp/conciliacion-tunnel.pid
API_PID=/tmp/conciliacion-api.pid

log()  { printf "\033[36m[dev]\033[0m %s\n" "$*"; }
ok()   { printf "\033[32m  ✓\033[0m %s\n" "$*"; }

stop() {
  local pidfile="$1" label="$2"
  if [[ -f "$pidfile" ]]; then
    local pid; pid=$(cat "$pidfile" 2>/dev/null || echo "")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      log "Deteniendo $label (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
      ok "$label detenido"
    else
      ok "$label ya no corría"
    fi
    rm -f "$pidfile"
  else
    ok "$label sin pidfile (ya estaba apagado)"
  fi
}

stop "$API_PID"     "API uvicorn"
stop "$TUNNEL_PID"  "túnel SSM"

# Garbage collection — procesos huérfanos sin pidfile
pkill -f "uvicorn conciliacion" 2>/dev/null && ok "Limpieza extra uvicorn" || true
pkill -f "session-manager-plugin" 2>/dev/null && ok "Limpieza extra SSM" || true

log "Listo."
