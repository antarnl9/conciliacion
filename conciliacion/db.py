"""Conexión y carga a DuckDB (motor local de conciliación).

La carga masiva usa Apache Arrow (mucho más rápido que executemany para millones de filas).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb
import pyarrow as pa

from . import config
from .parsers.base import CANONICAL_COLUMNS

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"

# Esquema Arrow para facturas_carrier (orden = CANONICAL_COLUMNS).
_CARRIER_ARROW = pa.schema([
    ("carrier", pa.string()), ("guia", pa.string()), ("cuenta", pa.string()),
    ("referencia", pa.string()), ("producto", pa.string()), ("origen", pa.string()),
    ("destino", pa.string()), ("piezas", pa.int32()), ("kilos", pa.float64()),
    ("fecha_envio", pa.date32()), ("fecha_factura", pa.date32()), ("no_factura", pa.string()),
    ("flete", pa.float64()), ("seguro", pa.float64()), ("descuento", pa.float64()),
    ("recargos", pa.float64()), ("iva", pa.float64()), ("importe_neto", pa.float64()),
    ("moneda", pa.string()), ("remitente", pa.string()), ("destinatario", pa.string()),
    ("es_retorno", pa.bool_()), ("archivo_origen", pa.string()), ("zona", pa.string()),
])

_CLIENTE_ARROW = pa.schema([
    ("guia", pa.string()), ("cliente", pa.string()),
    ("total", pa.float64()), ("archivo_origen", pa.string()),
])


def connect() -> duckdb.DuckDBPyConnection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute(SCHEMA_SQL.read_text())
    # migraciones idempotentes sobre BDs ya existentes (el volumen)
    for ddl in (
        "ALTER TABLE facturas_carrier ADD COLUMN IF NOT EXISTS zona VARCHAR",
        "ALTER TABLE config_credito ADD COLUMN IF NOT EXISTS margen DOUBLE",
        "ALTER TABLE config_credito ADD COLUMN IF NOT EXISTS dias_credito INTEGER",
        "ALTER TABLE cobros ADD COLUMN IF NOT EXISTS tipo VARCHAR",
        "ALTER TABLE cobros ADD COLUMN IF NOT EXISTS concepto VARCHAR",
        "ALTER TABLE cobros ADD COLUMN IF NOT EXISTS monto_pagado DOUBLE",
        "ALTER TABLE cobros ADD COLUMN IF NOT EXISTS fecha_enviada DATE",
        "ALTER TABLE cobros ADD COLUMN IF NOT EXISTS fecha_vencimiento DATE",
    ):
        con.execute(ddl)
    return con


def _flush_arrow(con, table: str, schema: pa.Schema, chunk: list[tuple]) -> int:
    cols = list(zip(*chunk)) if chunk else [()] * len(schema)
    arrays = [pa.array(list(c), type=schema.field(i).type) for i, c in enumerate(cols)]
    tbl = pa.Table.from_arrays(arrays, schema=schema)
    con.register("_a", tbl)
    con.execute(f"INSERT INTO {table} SELECT * FROM _a")
    con.unregister("_a")
    return len(chunk)


def insert_facturas_carrier(con, lineas: Iterable, batch: int = 100_000) -> int:
    """Inserta objetos LineaFactura (con .as_row()) en facturas_carrier vía Arrow."""
    buf, total = [], 0
    for ln in lineas:
        buf.append(ln.as_row())
        if len(buf) >= batch:
            total += _flush_arrow(con, "facturas_carrier", _CARRIER_ARROW, buf)
            buf.clear()
    if buf:
        total += _flush_arrow(con, "facturas_carrier", _CARRIER_ARROW, buf)
    return total


def insert_facturas_cliente(con, rows: Iterable[tuple], batch: int = 100_000) -> int:
    """Inserta tuplas (guia, cliente, total, archivo) en facturas_cliente vía Arrow."""
    buf, total = [], 0
    for r in rows:
        buf.append(r)
        if len(buf) >= batch:
            total += _flush_arrow(con, "facturas_cliente", _CLIENTE_ARROW, buf)
            buf.clear()
    if buf:
        total += _flush_arrow(con, "facturas_cliente", _CLIENTE_ARROW, buf)
    return total


assert _CARRIER_ARROW.names == CANONICAL_COLUMNS, "esquema Arrow != CANONICAL_COLUMNS"
