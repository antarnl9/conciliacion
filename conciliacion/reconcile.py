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
    md = config.MARGEN_EXTRA_DEFAULT
    sql = f"""
    CREATE OR REPLACE TABLE reconciliacion AS
    WITH carrier AS (
        SELECT * FROM (
            SELECT *,
                -- guía original de un retorno (RT + 10 dígitos) para atribuirlo a su cliente
                CASE WHEN es_retorno THEN regexp_extract(referencia, '[0-9]{{10}}') END AS orig_guia,
                row_number() OVER (PARTITION BY guia ORDER BY fecha_factura NULLS LAST) AS rn
            FROM facturas_carrier
        ) WHERE rn = 1
    ),
    sob AS (SELECT guia, sum(monto) AS sobrepeso FROM ch_sobrepeso GROUP BY guia),
    cfg AS (SELECT seller_id, metodo AS cobro_tipo, margen AS cfg_margen, dias_credito FROM config_credito),
    base AS (
        -- FULL OUTER: guías con costo (carrier) Y guías cobradas en sistema sin costo aún (CH).
        -- rs = guía original del retorno (resuelve el cliente real de los RT).
        SELECT
            COALESCE(c.guia, s.guia) AS guia,
            COALESCE(c.carrier, 'dhl') AS carrier,
            c.importe_neto AS costo,
            (c.guia IS NOT NULL) AS has_cost,
            COALESCE(c.es_retorno, false) AS es_retorno,
            c.zona, c.kilos,
            COALESCE(c.fecha_envio, CAST(s.created_at AS DATE)) AS fecha_envio,
            c.fecha_factura, c.remitente,
            COALESCE(s.seller_id,   CASE WHEN COALESCE(c.es_retorno,false) THEN rs.seller_id END)     AS seller_id,
            COALESCE(s.seller_name, CASE WHEN COALESCE(c.es_retorno,false) THEN rs.seller_name END)   AS seller_name,
            COALESCE(s.payment_model, CASE WHEN COALESCE(c.es_retorno,false) THEN rs.payment_model END) AS payment_model,
            -- el retorno no se cobró en sistema: ya_cobrado = 0 → se cobra completo a costo+margen
            CASE WHEN COALESCE(c.es_retorno,false) THEN 0 ELSE s.sale_price END AS sale_price,
            ((s.guia IS NOT NULL) OR (COALESCE(c.es_retorno,false) AND rs.guia IS NOT NULL)) AS in_system,
            COALESCE(sob.sobrepeso, 0) AS sobrepeso_cobrado
        FROM carrier c
        FULL OUTER JOIN ch_shipments s ON s.guia = c.guia
        LEFT JOIN ch_shipments rs ON rs.guia = c.orig_guia
        LEFT JOIN sob ON sob.guia = COALESCE(c.guia, s.guia)
    ),
    cat AS (
        SELECT base.*,
            COALESCE(cfg.cobro_tipo, 'automatica') AS cobro_tipo,
            cfg.dias_credito,
            CASE
                WHEN NOT in_system THEN 'sin_sistema'
                WHEN {internal} THEN 'interno'
                WHEN payment_model = '{config.CONFIG_CREDITO}' THEN 'credito'
                WHEN payment_model = '{config.CONFIG_PREPAGO}' THEN 'prepago'
                ELSE 'otro'
            END AS tipo,
            -- factor de cobro (1+margen): interno usa su pacto; resto, config del cliente o default
            CASE WHEN {internal} THEN {margin}
                 ELSE 1 + COALESCE(cfg.cfg_margen, {md}) END AS factor,
            -- tarifa por zona/kilo (crédito manual), con el seller efectivo (incluye retornos)
            (SELECT t.precio FROM tarifas t
               WHERE t.seller_id = base.seller_id AND t.carrier = base.carrier AND t.zona = base.zona
                 AND base.kilos >= COALESCE(t.peso_min, -1e12) AND base.kilos < COALESCE(t.peso_max, 1e12)
                 AND (t.vigencia_desde IS NULL OR base.fecha_envio >= t.vigencia_desde)
                 AND (t.vigencia_hasta IS NULL OR base.fecha_envio <= t.vigencia_hasta)
               ORDER BY t.peso_min LIMIT 1) AS tarifa_precio
        FROM base LEFT JOIN cfg ON cfg.seller_id = base.seller_id
    ),
    val AS (
        SELECT *,
            (COALESCE(sale_price,0) + sobrepeso_cobrado) AS ya_cobrado,
            -- extra a cobrar (sobrepeso/retorno/desfase) = piso costo+margen menos lo ya cobrado.
            -- crédito manual no usa extra: la tarifa por guía ya cubre re-pesos y retornos.
            CASE
                WHEN NOT has_cost THEN 0
                WHEN tipo = 'credito' AND cobro_tipo = 'manual' THEN 0
                ELSE GREATEST(0, costo * factor - (COALESCE(sale_price,0) + sobrepeso_cobrado))
            END AS extra_raw,
            -- ingreso devengado (lo que se debe cobrar en total por la guía)
            CASE
                WHEN NOT has_cost THEN COALESCE(sale_price,0) + sobrepeso_cobrado
                WHEN tipo = 'interno' THEN costo * factor
                WHEN tipo = 'credito' AND cobro_tipo = 'manual' THEN tarifa_precio
                WHEN tipo IN ('credito','prepago','otro') THEN GREATEST(COALESCE(sale_price,0) + sobrepeso_cobrado, costo * factor)
                ELSE NULL
            END AS ingreso_raw
        FROM cat
    )
    SELECT
        guia, carrier, COALESCE(seller_name, remitente) AS cliente_real, seller_id,
        payment_model, tipo, cobro_tipo, es_retorno, has_cost,
        round(costo, 2) AS costo, sale_price,
        round(sobrepeso_cobrado, 2) AS sobrepeso_cobrado, tarifa_precio,
        round(extra_raw, 2) AS extra,
        round(ingreso_raw, 2) AS ingreso,
        round(ingreso_raw - costo, 2) AS margen,
        CASE
            WHEN NOT in_system THEN 'Sin guia en sistema'
            WHEN NOT has_cost  THEN 'Cobrado, costo pendiente'
            WHEN tipo = 'interno' THEN 'Interno (14%)'
            WHEN tipo = 'credito' AND cobro_tipo = 'manual' AND tarifa_precio IS NULL THEN 'Falta cobrar (credito)'
            WHEN extra_raw > 0.5 THEN 'Extra por cobrar'
            ELSE 'Cobrado OK'
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
