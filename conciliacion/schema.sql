-- Esquema DuckDB del motor de conciliación.

-- Facturas de paquetería subidas (costo). Una fila = una línea de guía cobrada por DHL.
CREATE TABLE IF NOT EXISTS facturas_carrier (
    carrier        VARCHAR,
    guia           VARCHAR,
    cuenta         VARCHAR,
    referencia     VARCHAR,
    producto       VARCHAR,
    origen         VARCHAR,
    destino        VARCHAR,
    piezas         INTEGER,
    kilos          DOUBLE,
    fecha_envio    DATE,
    fecha_factura  DATE,
    no_factura     VARCHAR,
    flete          DOUBLE,
    seguro         DOUBLE,
    descuento      DOUBLE,
    recargos       DOUBLE,
    iva            DOUBLE,
    importe_neto   DOUBLE,
    moneda         VARCHAR,
    remitente      VARCHAR,
    destinatario   VARCHAR,
    es_retorno     BOOLEAN,
    archivo_origen VARCHAR,
    zona           VARCHAR
);

-- Facturas reales a CLIENTES de crédito (lo que se cobró). guia -> total facturado.
CREATE TABLE IF NOT EXISTS facturas_cliente (
    guia           VARCHAR,
    cliente        VARCHAR,   -- opcional, si el archivo lo trae
    total          DOUBLE,    -- monto facturado al cliente (con IVA)
    archivo_origen VARCHAR
);

-- Espejo de ClickHouse: una fila por guía (deduplicada con argMax).
CREATE TABLE IF NOT EXISTS ch_shipments (
    guia          VARCHAR,
    seller_id     BIGINT,
    seller_name   VARCHAR,
    payment_model VARCHAR,    -- 'Prepago' | 'Prepago sin saldo' | otros
    sale_price    DOUBLE,     -- precio base del sistema (puede ser NULL en internos)
    created_at    TIMESTAMP   -- fecha de consumo de la guía
);

-- Espejo de ClickHouse: sobrepeso cobrado en sistema por guía.
CREATE TABLE IF NOT EXISTS ch_sobrepeso (
    guia   VARCHAR,
    monto  DOUBLE
);

-- Espejo de ClickHouse: recargas de saldo por cliente (prepago).
CREATE TABLE IF NOT EXISTS ch_recargas (
    seller_id   BIGINT,
    seller_name VARCHAR,
    recargas    DOUBLE
);

-- Configuración de cobro por cliente de crédito (cada cliente tiene su regla).
CREATE TABLE IF NOT EXISTS config_credito (
    seller_id    BIGINT,
    cliente      VARCHAR,
    metodo       VARCHAR,   -- 'automatica' (precio sistema + extra) | 'manual' (tarifa zona/kilo)
    valor        DOUBLE,    -- (legado) % o precio fijo por guia
    nota         VARCHAR,
    margen       DOUBLE,    -- margen para el extra (ej. 0.14); piso de cobro = costo*(1+margen)
    dias_credito INTEGER    -- plazo de pago en días (default 30) para vencimiento/atraso
);

-- Tarifas FLAT por cliente: matriz (zona x rango de kilo) -> precio fijo, con vigencia.
-- Solo para metodo='flat'. Los demas metodos derivan del costo (costos_tarifa) + margen.
CREATE TABLE IF NOT EXISTS tarifas (
    seller_id      BIGINT,
    cliente        VARCHAR,
    carrier        VARCHAR,
    zona           VARCHAR,
    peso_min       DOUBLE,
    peso_max       DOUBLE,
    precio         DOUBLE,
    vigencia_desde DATE,
    vigencia_hasta DATE
);

-- COSTOS: rate card de la paqueteria (servicio x zona x rango de kilo -> costo). Vigencia GLOBAL.
-- servicio = el tipo de servicio de la paqueteria (ej. 'Express Saver', 'Standard', 'G'); vacio = tarifa general.
CREATE TABLE IF NOT EXISTS costos_tarifa (
    carrier        VARCHAR,
    zona           VARCHAR,
    peso_min       DOUBLE,
    peso_max       DOUBLE,
    costo          DOUBLE,
    vigencia_desde DATE,
    vigencia_hasta DATE,
    servicio       VARCHAR
);

-- Combustible (fuel) por paqueteria y periodo (semanal o mensual = rango de fechas). pct ej 0.16.
CREATE TABLE IF NOT EXISTS combustible (
    carrier        VARCHAR,
    vigencia_desde DATE,
    vigencia_hasta DATE,
    pct            DOUBLE
);

-- Margen por ZONA por cliente (metodo='margen_zona').
CREATE TABLE IF NOT EXISTS margen_zona (
    seller_id BIGINT,
    zona      VARCHAR,
    margen    DOUBLE
);

-- Margen por RANGO DE KILO por cliente (metodo='margen_kilo').
CREATE TABLE IF NOT EXISTS margen_kilo (
    seller_id BIGINT,
    peso_min  DOUBLE,
    peso_max  DOUBLE,
    margen    DOUBLE
);

-- Mapeo de RECARGOS por paqueteria: liga un concepto (ej. 'Zona extendida') a la columna del Acre
-- donde viene su costo (ej. 'ODA' en FedEx). El costo del recargo se lee de esa columna por guia.
CREATE TABLE IF NOT EXISTS recargos_mapeo (
    carrier  VARCHAR,
    concepto VARCHAR,
    columna  VARCHAR
);

-- Costo de cada recargo por guia (capturado del Acre segun recargos_mapeo, al subir el archivo).
CREATE TABLE IF NOT EXISTS factura_recargos (
    carrier  VARCHAR,
    guia     VARCHAR,
    concepto VARCHAR,
    monto    DOUBLE
);

-- Cierres de mes (snapshot del periodo).
CREATE TABLE IF NOT EXISTS periodos (
    mes        VARCHAR,
    estatus    VARCHAR,   -- 'abierto' | 'cerrado'
    costo      DOUBLE,
    ingreso    DOUBLE,
    utilidad   DOUBLE,
    cerrado_en TIMESTAMP
);

-- Resultado de la conciliación (placeholder vacío; reconcile.build() lo REEMPLAZA al correr).
-- Existe desde el arranque para que la UI/API no truenen en una BD recién creada.
CREATE TABLE IF NOT EXISTS reconciliacion (
    guia              VARCHAR,
    carrier           VARCHAR,
    cliente_real      VARCHAR,
    seller_id         BIGINT,
    payment_model     VARCHAR,
    tipo              VARCHAR,
    cobro_tipo        VARCHAR,
    es_retorno        BOOLEAN,
    has_cost          BOOLEAN,
    costo             DOUBLE,
    sale_price        DOUBLE,
    sobrepeso_cobrado DOUBLE,
    tarifa_precio     DOUBLE,
    extra             DOUBLE,
    recargos          DOUBLE,
    ingreso           DOUBLE,
    margen            DOUBLE,
    estatus           VARCHAR,
    mes_envio         VARCHAR,
    mes_factura       VARCHAR
);

-- Cobro generado por cliente/mes — libro de cuentas por cobrar.
CREATE TABLE IF NOT EXISTS cobros (
    seller_id        BIGINT,
    cliente          VARCHAR,
    mes              VARCHAR,
    guias            BIGINT,
    monto            DOUBLE,     -- total a cobrar al cliente en el mes
    estatus          VARCHAR,    -- 'generado' | 'enviado' | 'parcial' | 'pagado'
    generado_en      TIMESTAMP,
    nota             VARCHAR,
    tipo             VARCHAR,    -- 'credito' | 'prepago'
    concepto         VARCHAR,    -- 'factura' (cobro completo) | 'extra' (solo sobrepeso/retornos/desfase)
    monto_pagado     DOUBLE,     -- suma de abonos (pagos parciales)
    fecha_enviada    DATE,       -- cuándo se envió la factura al cliente
    fecha_vencimiento DATE       -- fecha_enviada + dias_credito
);

-- Abonos / pagos parciales por cobro (cliente+mes). saldo = monto - sum(pagos).
CREATE TABLE IF NOT EXISTS pagos (
    seller_id     BIGINT,
    mes           VARCHAR,
    fecha         DATE,
    monto         DOUBLE,
    nota          VARCHAR,
    registrado_en TIMESTAMP
);

-- PDF de factura adjunto por cliente/mes (para tener todo junto).
CREATE TABLE IF NOT EXISTS cobro_adjuntos (
    seller_id BIGINT,
    mes       VARCHAR,
    filename  VARCHAR,
    path      VARCHAR,
    subido_en TIMESTAMP
);

-- Metadatos de cada corrida de sync de ClickHouse.
CREATE TABLE IF NOT EXISTS sync_meta (
    tabla      VARCHAR,
    carrier    VARCHAR,
    desde      VARCHAR,
    filas      BIGINT,
    corrido_en TIMESTAMP
);
