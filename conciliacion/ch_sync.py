"""Sincroniza ClickHouse -> DuckDB (espejo local por guía).

Migración jun 2026: cluster nuevo (mx-central-1, ReplicatedReplacingMergeTree).
  - Las tablas ya NO tienen `_synced_at`; el dedup se hace con `FINAL`
    (sorting key: `shipment_id`, `transaction_id`, `recharge_id`).
  - `seller_configuration_name` (= payment_model) ya NO está en `fct_shipments`.
    Se rellena con cadena vacía hasta que el equipo de DBA lo vuelva a exponer
    en `t1_envios.*`. **Impacto:** sin payment_model, la lógica Prepago vs
    Crédito de `reconcile.py` no puede bifurcar — todo cae al default. Avisar
    al usuario cuando se corre `sync-ch` para que lo escale a infra.
"""
from __future__ import annotations

import clickhouse_connect

from . import config


# Bandera para emitir el aviso una vez por proceso al primer sync
_PAYMENT_MODEL_WARNED = False


def _warn_payment_model_missing():
    global _PAYMENT_MODEL_WARNED
    if _PAYMENT_MODEL_WARNED:
        return
    _PAYMENT_MODEL_WARNED = True
    print(
        "[ch_sync] AVISO: payment_model viene vacío porque el nuevo schema de "
        "t1_envios.fct_shipments ya no expone `seller_configuration_name`. "
        "La bifurcación Prepago/Crédito en reconcile.py no funcionará hasta que "
        "el equipo DBA reponga ese campo (o lo ofrezca vía dim_seller / vista)."
    )


def _client():
    return clickhouse_connect.get_client(**config.ch_settings())


def sync_shipments(con, carrier: str | None = None, desde: str = "2025-09-01") -> int:
    """Trae una fila por guía (dedup FINAL + argMax) de TODAS las paqueterías
    soportadas a ch_shipments. El cruce en reconcile es por número de guía;
    FedEx usa 'Guia', Paquete Express usa 'Rastreo'."""
    _warn_payment_model_missing()
    carriers = sorted(set(config.CARRIER_CH.values()))  # DHL, FEDEX, PAQUETERIA EXPRESS (sin duplicar)
    cli = _client()
    # FINAL hace el dedup engine-level por shipment_id; argMax(field, created_at)
    # resuelve el caso (raro) de varios shipment_id compartiendo shipment_number.
    q = f"""
        SELECT shipment_number AS guia,
               argMax(seller_id, created_at)   AS seller_id,
               argMax(seller_name, created_at) AS seller_name,
               ''                               AS payment_model,
               argMax(sale_price, created_at)  AS sale_price,
               max(created_at)                 AS created_ts
        FROM t1_envios.fct_shipments FINAL
        WHERE carrier_name IN %(c)s AND created_at >= %(d)s
        GROUP BY shipment_number
    """
    con.execute("DELETE FROM ch_shipments")
    total = 0
    with cli.query_arrow_stream(q, parameters={"c": carriers, "d": desde}) as stream:
        for batch in stream:
            con.register("_a", batch)
            con.execute("INSERT INTO ch_shipments SELECT * FROM _a")
            con.unregister("_a")
            total += batch.num_rows
    _stamp(con, "ch_shipments", "multi", desde, total)
    return total


def sync_sobrepeso(con, desde: str = "2025-09-01") -> int:
    """Trae el sobrepeso cobrado en sistema por guía a ch_sobrepeso."""
    cli = _client()
    q = """
        SELECT reference AS guia, round(sum(amount), 2) AS monto
        FROM t1_envios.fct_wallet_transactions FINAL
        WHERE transaction_type_name = 'Cargo por sobrepeso'
          AND reference != ''
          AND reference IS NOT NULL
        GROUP BY reference
    """
    con.execute("DELETE FROM ch_sobrepeso")
    total = 0
    with cli.query_arrow_stream(q) as stream:
        for batch in stream:
            con.register("_a", batch)
            con.execute("INSERT INTO ch_sobrepeso SELECT * FROM _a")
            con.unregister("_a")
            total += batch.num_rows
    _stamp(con, "ch_sobrepeso", "dhl", desde, total)
    return total


def sync_recargas(con, desde: str = "2025-09-01") -> int:
    """Trae el total de recargas de saldo por cliente a ch_recargas."""
    cli = _client()
    q = """
        SELECT seller_id,
               any(seller_name)            AS seller_name,
               round(sum(amount), 2)       AS recargas
        FROM t1_envios.fct_wallet_recharges FINAL
        WHERE created_at >= %(d)s
        GROUP BY seller_id
    """
    con.execute("DELETE FROM ch_recargas")
    total = 0
    with cli.query_arrow_stream(q, parameters={"d": desde}) as stream:
        for batch in stream:
            con.register("_a", batch)
            con.execute("INSERT INTO ch_recargas SELECT * FROM _a")
            con.unregister("_a")
            total += batch.num_rows
    _stamp(con, "ch_recargas", "-", desde, total)
    return total


def _stamp(con, tabla, carrier, desde, filas):
    con.execute(
        "INSERT INTO sync_meta VALUES (?,?,?,?, now())",
        [tabla, carrier, desde, filas],
    )
