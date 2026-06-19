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
    seller_id BIGINT,
    cliente   VARCHAR,
    metodo    VARCHAR,   -- 'margen_pct' | 'precio_fijo' | 'tarifa_zona'
    valor     DOUBLE,    -- % (0.14) o precio fijo por guia
    nota      VARCHAR
);

-- Tarifas por cliente: matriz (zona x rango de kilo) -> precio, con vigencia.
-- El archivo de la paqueteria ya trae zona y kilo por guia; aqui solo está el precio a cobrar.
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

-- Cierres de mes (snapshot del periodo).
CREATE TABLE IF NOT EXISTS periodos (
    mes        VARCHAR,
    estatus    VARCHAR,   -- 'abierto' | 'cerrado'
    costo      DOUBLE,
    ingreso    DOUBLE,
    utilidad   DOUBLE,
    cerrado_en TIMESTAMP
);

-- Cobro generado por cliente/mes (para descargar y monitorear su estatus).
CREATE TABLE IF NOT EXISTS cobros (
    seller_id   BIGINT,
    cliente     VARCHAR,
    mes         VARCHAR,
    guias       BIGINT,
    monto       DOUBLE,
    estatus     VARCHAR,   -- 'generado' | 'enviado' | 'pagado'
    generado_en TIMESTAMP,
    nota        VARCHAR
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
