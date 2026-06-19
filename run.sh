#!/usr/bin/env bash
# Levanta la UI web de conciliación. Abrir luego http://localhost:8770
cd "$(dirname "$0")"
exec .venv/bin/uvicorn conciliacion.api.main:app --port 8770
