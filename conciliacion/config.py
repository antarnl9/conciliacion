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
    """Devuelve kwargs para `clickhouse_connect.get_client(**ch_settings())`.

    Variables:
      CH_HOST, CH_PORT, CH_USER, CH_PASSWORD, CH_DATABASE, CH_SECURE — básicos.
      CH_CA_PATH                  — path al CA bundle si el cert es self-signed
                                    (ej. cluster privado de mx-central-1).
      CH_SERVER_HOSTNAME          — FQDN para SNI/cert-validation cuando
                                    CH_HOST=127.0.0.1 (caso del túnel SSM).
      CH_VERIFY                   — `false` para apagar verificación TLS
                                    (NO usar en prod).
    """
    e = _load_ch_env()
    host = e.get("CH_HOST")
    if not host:
        raise RuntimeError(
            "Falta CH_HOST. Pon las credenciales en conciliacion/.env.local "
            "o asegúrate de que data-platform/.env.local exista."
        )
    cfg = dict(
        host=host,
        port=int(e.get("CH_PORT", "8443")),
        username=e.get("CH_USER", "default"),
        password=e.get("CH_PASSWORD", ""),
        secure=str(e.get("CH_SECURE", "true")).lower() == "true",
        database=e.get("CH_DATABASE", "t1_envios"),
    )
    if cfg["secure"]:
        if e.get("CH_CA_PATH"):
            cfg["ca_cert"] = e["CH_CA_PATH"]
        if e.get("CH_SERVER_HOSTNAME"):
            cfg["server_host_name"] = e["CH_SERVER_HOSTNAME"]
        if e.get("CH_VERIFY") is not None:
            cfg["verify"] = str(e["CH_VERIFY"]).lower() == "true"
    return cfg

# --- Supabase (Auth + Storage) ---
def _load_env(prefix: str) -> dict:
    env: dict = {}
    if DATA_PLATFORM_ENV.exists():
        env.update({k: v for k, v in dotenv_values(DATA_PLATFORM_ENV).items() if v})
    if LOCAL_ENV.exists():
        env.update({k: v for k, v in dotenv_values(LOCAL_ENV).items() if v})
    env.update({k: v for k, v in os.environ.items() if k.startswith(prefix)})
    return env


def supabase_settings() -> dict:
    e = _load_env("SUPABASE_")
    url = (e.get("SUPABASE_URL") or "").rstrip("/")
    pub = e.get("SUPABASE_PUBLIC_KEY") or ""
    sec = e.get("SUPABASE_SECRET_KEY") or ""
    return {"url": url, "public": pub, "secret": sec,
            "bucket": e.get("SUPABASE_BUCKET", "facturas"),
            "enabled": bool(url and pub and sec)}


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

# Extras (sobrepeso/retornos/desfase) en prepago y crédito automático: se cobran a
# costo*(1+margen). Si el cliente no tiene margen configurado, se usa este default.
MARGEN_EXTRA_DEFAULT = 0.14
# Plazo de crédito por defecto (días) para vencimiento y días de atraso.
DIAS_CREDITO_DEFAULT = 30

# IVA aplicado a la tarifa del cliente (precio = base × (1+fuel) × (1+margen) × (1+IVA)).
IVA_DEFAULT = 0.16

# Modelos de pago en ClickHouse (seller_configuration_name)
CONFIG_PREPAGO = "Prepago"               # cobra al día con saldo; ingreso = sale_price + sobrepeso
CONFIG_CREDITO = "Prepago sin saldo"     # cobra por Acre con rezago; ingreso = factura real subida

# Mapeo de carrier del archivo -> carrier_name en ClickHouse (fct_shipments)
CARRIER_CH = {
    "dhl": "DHL",
    "fedex": "FEDEX",
    "paquete_express": "PAQUETERIA EXPRESS",
    "paquete_express_2": "PAQUETERIA EXPRESS",   # 2ª cuenta: mismo carrier en CH, archivo aparte
}
