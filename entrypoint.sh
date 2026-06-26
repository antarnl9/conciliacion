#!/usr/bin/env bash
# Entrypoint del contenedor en Railway:
#   1. Arranca el túnel SSM en background (supervisor con reintentos).
#   2. Espera hasta 60s a que localhost:8443 responda.
#   3. Arranca uvicorn (si el túnel no levantó, la app igual arranca: los
#      endpoints que leen DuckDB siguen sirviendo; sync-ch fallará con error
#      claro hasta que el túnel quede arriba).
set -u

# Si no hay credenciales AWS, no intentamos abrir túnel — sería ruido infinito.
if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  /usr/local/bin/start-tunnel.sh > /tmp/tunnel.log 2>&1 &
  echo "[entrypoint] túnel SSM lanzado en background (PID=$!)"
  echo "[entrypoint] esperando localhost:8443 …"
  for i in $(seq 1 30); do
    if nc -z localhost 8443 2>/dev/null; then
      echo "[entrypoint] túnel arriba ✓"
      break
    fi
    sleep 2
  done
  if ! nc -z localhost 8443 2>/dev/null; then
    echo "[entrypoint] WARN: túnel NO respondió en 60s — la app arranca igual"
    echo "[entrypoint] últimos 20 renglones del log del túnel:"
    tail -20 /tmp/tunnel.log 2>/dev/null || true
  fi
else
  echo "[entrypoint] AWS_* no configurado — SKIP túnel SSM (sync-ch fallará)"
fi

echo "[entrypoint] arrancando uvicorn en :${PORT:-8770}"
exec uvicorn conciliacion.api.main:app --host 0.0.0.0 --port "${PORT:-8770}"
