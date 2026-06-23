#!/usr/bin/env bash
# dev.sh — levanta el túnel SSM + la API de conciliacion para trabajar local.
#
# Idempotente: si algo ya está vivo, lo deja vivo. Si está roto, lo reinicia.
# Logs:
#   /tmp/conciliacion-tunnel.log   (SSM port-forward)
#   /tmp/conciliacion-api.log      (uvicorn)
# PIDs:
#   /tmp/conciliacion-tunnel.pid
#   /tmp/conciliacion-api.pid
#
# Uso:
#   ./dev.sh           # arranca todo (o no hace nada si ya está)
#   ./dev.sh restart   # mata todo y arranca limpio
#
set -euo pipefail

cd "$(dirname "$0")"

# ----- Config -----
AWS_PROFILE_NAME="cortex-ch"
AWS_REGION="mx-central-1"
SSM_TARGET="i-0a2b6bd3136982281"
CH_HOST_FQDN="cortex-clickhouse-prod-nlb-1c811a5fd4264cda.elb.mx-central-1.amazonaws.com"
CH_PORT_REMOTE=8443
CH_PORT_LOCAL=8443
API_PORT=8770
VENV_PY=".venv/bin/python"
VENV_UVICORN=".venv/bin/uvicorn"

TUNNEL_LOG=/tmp/conciliacion-tunnel.log
API_LOG=/tmp/conciliacion-api.log
TUNNEL_PID=/tmp/conciliacion-tunnel.pid
API_PID=/tmp/conciliacion-api.pid

# ----- Helpers -----
log()  { printf "\033[36m[dev]\033[0m %s\n" "$*"; }
ok()   { printf "\033[32m  ✓\033[0m %s\n" "$*"; }
warn() { printf "\033[33m  ⚠\033[0m %s\n" "$*"; }
err()  { printf "\033[31m  ✗\033[0m %s\n" "$*" >&2; }

is_running() { [[ -f "$1" ]] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

port_open() {
  nc -z localhost "$1" 2>/dev/null
}

find_ssm_plugin() {
  if command -v session-manager-plugin >/dev/null 2>&1; then
    echo "session-manager-plugin"
    return 0
  fi
  for p in /tmp/sessionmanager-bundle/bin/session-manager-plugin \
           /usr/local/sessionmanagerplugin/bin/session-manager-plugin \
           /opt/homebrew/bin/session-manager-plugin; do
    if [[ -x "$p" ]]; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

kill_pidfile() {
  local pidfile="$1" label="$2"
  if is_running "$pidfile"; then
    local pid; pid=$(cat "$pidfile")
    log "Matando $label (pid $pid)..."
    kill "$pid" 2>/dev/null || true
    # Espera hasta 5s a que muera
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pidfile"
    ok "$label detenido"
  else
    rm -f "$pidfile" 2>/dev/null || true
  fi
}

# ----- Restart shortcut -----
if [[ "${1:-}" == "restart" ]]; then
  log "Modo restart: deteniendo todo primero"
  kill_pidfile "$API_PID" "API"
  kill_pidfile "$TUNNEL_PID" "túnel SSM"
fi

# ----- 1) Pre-requisitos -----
log "Verificando prerrequisitos..."
[[ -x "$VENV_PY" ]] || { err "$VENV_PY no existe — corre 'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'"; exit 1; }
[[ -f .env.local ]] || { err ".env.local no existe — copia desde el ejemplo y rellena creds"; exit 1; }
SSM_PLUGIN=$(find_ssm_plugin) || { err "session-manager-plugin no encontrado"; exit 1; }
ok "venv, .env.local y SSM plugin OK"

# ----- 2) Túnel SSM -----
if is_running "$TUNNEL_PID" && port_open "$CH_PORT_LOCAL"; then
  ok "Túnel SSM ya activo (pid $(cat $TUNNEL_PID), puerto $CH_PORT_LOCAL)"
else
  # Limpieza si hay puerto huérfano
  if port_open "$CH_PORT_LOCAL" && ! is_running "$TUNNEL_PID"; then
    warn "Puerto $CH_PORT_LOCAL ocupado por proceso fantasma — matando session-manager-plugin huérfanos"
    pkill -f "session-manager-plugin" 2>/dev/null || true
    sleep 2
  fi
  log "Arrancando túnel SSM ($CH_HOST_FQDN:$CH_PORT_REMOTE → localhost:$CH_PORT_LOCAL)..."
  # PATH para que aws lo encuentre
  export PATH="$(dirname "$SSM_PLUGIN"):$PATH"
  nohup aws ssm start-session \
    --profile "$AWS_PROFILE_NAME" \
    --region "$AWS_REGION" \
    --target "$SSM_TARGET" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"$CH_HOST_FQDN\"],\"portNumber\":[\"$CH_PORT_REMOTE\"],\"localPortNumber\":[\"$CH_PORT_LOCAL\"]}" \
    > "$TUNNEL_LOG" 2>&1 &
  echo $! > "$TUNNEL_PID"
  # Esperar hasta 15s a que abra el puerto
  for i in $(seq 1 15); do
    if port_open "$CH_PORT_LOCAL"; then
      ok "Túnel listo (pid $(cat $TUNNEL_PID))"
      break
    fi
    sleep 1
    if [[ $i == 15 ]]; then
      err "Túnel no abrió el puerto en 15s. Ver $TUNNEL_LOG"
      tail -10 "$TUNNEL_LOG" >&2
      exit 1
    fi
  done
fi

# ----- 3) Health-check ClickHouse -----
log "Health-check ClickHouse..."
CH_OK=$("$VENV_PY" - <<PY
import sys
sys.path.insert(0, ".")
try:
    from conciliacion import config
    import clickhouse_connect
    cli = clickhouse_connect.get_client(**config.ch_settings())
    v = cli.query("SELECT version()").result_rows[0][0]
    max_ts = cli.query("SELECT max(created_at) FROM t1_envios.fct_shipments").result_rows[0][0]
    print(f"ok|{v}|{max_ts}")
except Exception as e:
    print(f"err|{e}")
PY
)
case "$CH_OK" in
  ok\|*)
    IFS="|" read -r _ ver maxts <<< "$CH_OK"
    ok "CH respondió — v$ver, data al ${maxts}"
    ;;
  *)
    err "CH no responde: $CH_OK"
    err "Tip: revisa $TUNNEL_LOG"
    exit 1
    ;;
esac

# ----- 4) API uvicorn -----
if is_running "$API_PID" && port_open "$API_PORT"; then
  ok "API ya corriendo (pid $(cat $API_PID), puerto $API_PORT)"
else
  if port_open "$API_PORT" && ! is_running "$API_PID"; then
    warn "Puerto $API_PORT ocupado por proceso fantasma — matando uvicorn huérfanos"
    pkill -f "uvicorn conciliacion" 2>/dev/null || true
    sleep 2
  fi
  log "Arrancando uvicorn en puerto $API_PORT..."
  nohup "$VENV_UVICORN" conciliacion.api.main:app --host 127.0.0.1 --port "$API_PORT" \
    > "$API_LOG" 2>&1 &
  echo $! > "$API_PID"
  for i in $(seq 1 10); do
    if port_open "$API_PORT"; then
      ok "API arriba (pid $(cat $API_PID))"
      break
    fi
    sleep 1
    if [[ $i == 10 ]]; then
      err "API no arrancó en 10s. Ver $API_LOG"
      tail -15 "$API_LOG" >&2
      exit 1
    fi
  done
fi

# ----- 5) Resumen -----
echo
log "Todo arriba. Endpoints:"
echo "    UI       → http://localhost:$API_PORT"
echo "    API docs → http://localhost:$API_PORT/docs"
echo "    Logs     → tail -f $API_LOG  |  tail -f $TUNNEL_LOG"
echo "    Stop     → ./dev-stop.sh"
echo "    Status   → ./dev-status.sh"
