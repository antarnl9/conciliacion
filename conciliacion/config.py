"""Configuración del proyecto conciliación.

Credenciales de ClickHouse: se leen de conciliacion/.env.local si existe, y si no,
del data-platform/.env.local (fuente de verdad del warehouse). Así no duplicamos secretos.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]            # .../conciliacion
REPO_ROOT = ROOT.parent                                # .../DashboardT1
DATA_PLATFORM_ENV = REPO_ROOT / "data-platform" / ".env.local"
LOCAL_ENV = ROOT / ".env.local"

DATA_DIR = ROOT / "data"
EXPORTS_DIR = ROOT / "exports"
DUCKDB_PATH = DATA_DIR / "conciliacion.duckdb"

# --- ClickHouse ---
def _load_ch_env() -> dict:
    env: dict = {}
    if DATA_PLATFORM_ENV.exists():
        env.update({k: v for k, v in dotenv_values(DATA_PLATFORM_ENV).items() if v})
    if LOCAL_ENV.exists():
        env.update({k: v for k, v in dotenv_values(LOCAL_ENV).items() if v})
    env.update({k: v for k, v in os.environ.items() if k.startswith("CH_")})
    return env


def ch_settings() -> dict:
    e = _load_ch_env()
    host = e.get("CH_HOST")
    if not host:
        raise RuntimeError(
            "Falta CH_HOST. Pon las credenciales en conciliacion/.env.local "
            "o asegúrate de que data-platform/.env.local exista."
        )
    return dict(
        host=host,
        port=int(e.get("CH_PORT", "8443")),
        username=e.get("CH_USER", "default"),
        password=e.get("CH_PASSWORD", ""),
        secure=str(e.get("CH_SECURE", "true")).lower() == "true",
        database=e.get("CH_DATABASE", "t1_envios"),
    )

# --- Reglas de negocio ---
# Cuentas internas inter-empresa: el sistema guarda sale_price=0 y se cobran por Acre
# con un margen pactado. El ingreso real = costo * (1 + margen).
CUENTAS_INTERNAS_MARGEN = {
    "SN00449": 0.14,
    "SE00724": 0.14,
}
# Match por substring (case-insensitive) para variantes de nombre (ej. Inbursa Cuicuilco...).
INTERNAS_SUBSTRING_MARGEN = {
    "INBURSA": 0.14,
}

# Modelos de pago en ClickHouse (seller_configuration_name)
CONFIG_PREPAGO = "Prepago"               # cobra al día con saldo; ingreso = sale_price + sobrepeso
CONFIG_CREDITO = "Prepago sin saldo"     # cobra por Acre con rezago; ingreso = factura real subida

# Mapeo de carrier del archivo -> carrier_name en ClickHouse
CARRIER_CH = {
    "dhl": "DHL",
}
