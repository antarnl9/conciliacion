"""Sincroniza ClickHouse -> DuckDB (espejo local por guía)."""
from __future__ import annotations

import clickhouse_connect

from . import config


def _client():
    return clickhouse_connect.get_client(**config.ch_settings())


def sync_shipments(con, carrier: str = "dhl", desde: str = "2025-09-01") -> int:
    """Trae una fila por guía (dedup argMax) del carrier indicado a ch_shipments."""
    carrier_ch = config.CARRIER_CH[carrier]
    cli = _client()
    q = f"""
        SELECT shipment_number AS guia,
               argMax(seller_id, _synced_at)                 AS seller_id,
               argMax(seller_name, _synced_at)               AS seller_name,
               argMax(seller_configuration_name, _synced_at) AS payment_model,
               argMax(sale_price, _synced_at)                AS sale_price,
               argMax(created_at, _synced_at)                AS created_ts
        FROM t1_envios.fct_shipments
        WHERE carrier_name = %(c)s AND created_at >= %(d)s
        GROUP BY shipment_number
    """
    con.execute("DELETE FROM ch_shipments")
    total = 0
    with cli.query_arrow_stream(q, parameters={"c": carrier_ch, "d": desde}) as stream:
        for batch in stream:
            con.register("_a", batch)
            con.execute("INSERT INTO ch_shipments SELECT * FROM _a")
            con.unregister("_a")
            total += batch.num_rows
    _stamp(con, "ch_shipments", carrier, desde, total)
    return total


def sync_sobrepeso(con, desde: str = "2025-09-01") -> int:
    """Trae el sobrepeso cobrado en sistema por guía a ch_sobrepeso."""
    cli = _client()
    q = """
        SELECT reference AS guia, round(sum(amount), 2) AS monto
        FROM (
            SELECT transaction_id,
                   argMax(amount, _synced_at)    AS amount,
                   argMax(reference, _synced_at) AS reference
            FROM t1_envios.fct_wallet_transactions
            WHERE transaction_type_name = 'Cargo por sobrepeso'
            GROUP BY transaction_id
        )
        WHERE reference != '' AND reference IS NOT NULL
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
        SELECT seller_id, any(seller_name) AS seller_name,
               round(sum(amount), 2) AS recargas
        FROM (
            SELECT recharge_id,
                   argMax(seller_id, _synced_at)   AS seller_id,
                   argMax(seller_name, _synced_at) AS seller_name,
                   argMax(amount, _synced_at)       AS amount,
                   argMax(created_at, _synced_at)   AS ca
            FROM t1_envios.fct_wallet_recharges
            GROUP BY recharge_id
        )
        WHERE ca >= %(d)s
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
