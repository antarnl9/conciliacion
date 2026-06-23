#!/usr/bin/env bash
# dev-status.sh — quién está vivo, quién no.
set -uo pipefail
cd "$(dirname "$0")"

TUNNEL_PID=/tmp/conciliacion-tunnel.pid
API_PID=/tmp/conciliacion-api.pid

green() { printf "\033[32m%s\033[0m" "$*"; }
red()   { printf "\033[31m%s\033[0m" "$*"; }
yellow(){ printf "\033[33m%s\033[0m" "$*"; }

check() {
  local pidfile="$1" port="$2" label="$3"
  local pid_ok=0 port_ok=0
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    pid_ok=1
  fi
  if nc -z localhost "$port" 2>/dev/null; then
    port_ok=1
  fi
  printf "  %-18s pid=" "$label"
  if (( pid_ok )); then green "$(cat $pidfile)"; else red "down"; fi
  printf "  puerto=%s " "$port"
  if (( port_ok )); then green "open"; else red "closed"; fi
  if (( pid_ok && port_ok )); then
    printf "  → "; green "UP"
  elif (( !pid_ok && !port_ok )); then
    printf "  → "; red "DOWN"
  else
    printf "  → "; yellow "ZOMBIE (revisar)"
  fi
  echo
}

echo "Estado conciliacion:"
check "$TUNNEL_PID" 8443 "Túnel SSM"
check "$API_PID"    8770 "API uvicorn"
echo
echo "Logs:"
echo "  tail -f /tmp/conciliacion-tunnel.log"
echo "  tail -f /tmp/conciliacion-api.log"
