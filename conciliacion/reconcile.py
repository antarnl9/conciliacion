"""Motor de conciliación: cruza costo (factura paquetería) vs cobro (sistema/facturas cliente).

Reglas (de la auditoría):
- Cliente real = seller_name de ClickHouse (resuelve revendedores; ignora 'Remitente' del Acre).
- Internos (SN00449/SE00724/Inbursa): ingreso = costo * 1.14 (sistema guarda 0).
- Crédito ('Prepago sin saldo'): ingreso = factura real subida del cliente; si no está -> falta cobrar.
- Prepago ('Prepago'): ingreso = sale_price + sobrepeso cobrado en sistema.
"""
from __future__ import annotations

from . import config


def _internal_condition(col: str = "seller_name") -> str:
    exact = ",".join("'%s'" % n.upper() for n in config.CUENTAS_INTERNAS_MARGEN)
    parts = [f"upper({col}) IN ({exact})"] if exact else []
    for sub in config.INTERNAS_SUBSTRING_MARGEN:
        parts.append(f"upper({col}) LIKE '%{sub.upper()}%'")
    return "(" + " OR ".join(parts) + ")"


def _internal_margin_case(col: str = "seller_name") -> str:
    whens = []
    for n, m in config.CUENTAS_INTERNAS_MARGEN.items():
        whens.append(f"WHEN upper({col}) = '{n.upper()}' THEN {1 + m}")
    for sub, m in config.INTERNAS_SUBSTRING_MARGEN.items():
        whens.append(f"WHEN upper({col}) LIKE '%{sub.upper()}%' THEN {1 + m}")
    return "CASE " + " ".join(whens) + " ELSE 1.0 END"


def build(con) -> int:
    internal = _internal_condition()
    margin = _internal_margin_case()
    sql = f"""
    CREATE OR REPLACE TABLE reconciliacion AS
    WITH carrier AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY guia ORDER BY fecha_factura NULLS LAST) AS rn
            FROM facturas_carrier
        ) WHERE rn = 1
    ),
    sob AS (SELECT guia, sum(monto) AS sobrepeso FROM ch_sobrepeso GROUP BY guia),
    cfg AS (SELECT seller_id, metodo AS cobro_tipo FROM config_credito),
    j AS (
        -- FULL OUTER: incluye guías con costo (carrier) Y guías cobradas en sistema sin costo aún (CH)
        SELECT
            COALESCE(c.guia, s.guia) AS guia,
            COALESCE(c.carrier, 'dhl') AS carrier,
            c.importe_neto AS costo,                       -- NULL si la paquetería aún no la factura
            (c.guia IS NOT NULL) AS has_cost,
            (s.guia IS NOT NULL) AS in_system,
            COALESCE(c.es_retorno, false) AS es_retorno,
            COALESCE(c.fecha_envio, CAST(s.created_at AS DATE)) AS fecha_envio,
            c.fecha_factura, c.remitente,
            s.guia AS s_guia, s.seller_id, s.seller_name, s.payment_model, s.sale_price,
            COALESCE(sob.sobrepeso, 0) AS sobrepeso_cobrado,
            -- precio de tarifa: cruza zona+kilo del archivo contra la matriz del cliente (vigente)
            (SELECT t.precio FROM tarifas t
               WHERE t.seller_id = s.seller_id AND t.carrier = c.carrier AND t.zona = c.zona
                 AND c.kilos >= COALESCE(t.peso_min, -1e12) AND c.kilos < COALESCE(t.peso_max, 1e12)
                 AND (t.vigencia_desde IS NULL OR c.fecha_envio >= t.vigencia_desde)
                 AND (t.vigencia_hasta IS NULL OR c.fecha_envio <= t.vigencia_hasta)
               ORDER BY t.peso_min LIMIT 1) AS tarifa_precio
        FROM carrier c
        FULL OUTER JOIN ch_shipments s ON s.guia = c.guia
        LEFT JOIN sob ON sob.guia = COALESCE(c.guia, s.guia)
    ),
    cat AS (
        SELECT j.*,
            COALESCE(cfg.cobro_tipo, 'automatica') AS cobro_tipo,
            CASE
                WHEN NOT in_system THEN 'sin_sistema'
                WHEN {internal} THEN 'interno'
                WHEN payment_model = '{config.CONFIG_CREDITO}' THEN 'credito'
                WHEN payment_model = '{config.CONFIG_PREPAGO}' THEN 'prepago'
                ELSE 'otro'
            END AS tipo,
            {margin} AS factor_interno
        FROM j LEFT JOIN cfg ON cfg.seller_id = j.seller_id
    ),
    val AS (
        SELECT *,
            CASE
                WHEN NOT has_cost THEN COALESCE(sale_price,0) + sobrepeso_cobrado  -- cobrado, costo pendiente
                WHEN tipo = 'interno' THEN costo * factor_interno
                WHEN tipo = 'credito' AND cobro_tipo = 'manual' THEN tarifa_precio
                WHEN tipo IN ('prepago','otro','credito') THEN COALESCE(sale_price,0) + sobrepeso_cobrado
                ELSE NULL
            END AS ingreso_raw
        FROM cat
    )
    SELECT
        guia, carrier, COALESCE(seller_name, remitente) AS cliente_real, seller_id,
        payment_model, tipo, cobro_tipo, es_retorno, has_cost,
        round(costo, 2) AS costo, sale_price,
        round(sobrepeso_cobrado, 2) AS sobrepeso_cobrado, tarifa_precio,
        round(ingreso_raw, 2) AS ingreso,
        round(ingreso_raw - costo, 2) AS margen,            -- NULL si no hay costo aún
        CASE
            WHEN NOT in_system THEN 'Sin guia en sistema'           -- costo sin guía en sistema
            WHEN NOT has_cost  THEN 'Cobrado, costo pendiente'      -- cobrado, paquetería no ha facturado
            WHEN tipo = 'interno' THEN 'Interno (14%)'
            WHEN ingreso_raw IS NULL THEN 'Falta cobrar (credito)'
            WHEN ingreso_raw >= costo THEN 'Cobrado OK'
            ELSE 'Sobrepeso pendiente'
        END AS estatus,
        strftime(fecha_envio,  '%Y-%m') AS mes_envio,
        strftime(fecha_factura,'%Y-%m') AS mes_factura
    FROM val
    """
    con.execute(sql)
    return con.execute("SELECT count(*) FROM reconciliacion").fetchone()[0]


def resumen(con) -> list[tuple]:
    return con.execute("""
        SELECT estatus, count(*) guias,
               round(sum(costo),2) costo,
               round(sum(ingreso),2) ingreso,
               round(sum(ingreso) - sum(costo),2) margen
        FROM reconciliacion GROUP BY estatus ORDER BY costo DESC
    """).fetchall()
