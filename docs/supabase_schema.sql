-- Esquema Supabase (Postgres) — estado durable de conciliación.
-- Pegar en Supabase → SQL Editor → Run. (La conciliación pesada y el espejo de
-- ClickHouse siguen en DuckDB dentro del contenedor; aquí solo va lo compartido.)

create table if not exists config_credito (
  seller_id   bigint primary key,
  cliente     text,
  metodo      text,                 -- 'automatica' | 'manual'
  valor       double precision,
  nota        text,
  updated_at  timestamptz default now()
);

create table if not exists tarifas (
  id             bigserial primary key,
  seller_id      bigint,
  cliente        text,
  carrier        text,
  zona           text,
  peso_min       double precision,
  peso_max       double precision,
  precio         double precision,
  vigencia_desde date,
  vigencia_hasta date
);
create index if not exists idx_tarifas_lookup on tarifas (seller_id, carrier, zona);

create table if not exists periodos (
  mes        text primary key,
  estatus    text,                  -- 'abierto' | 'cerrado'
  costo      double precision,
  ingreso    double precision,
  utilidad   double precision,
  cerrado_en timestamptz
);

create table if not exists cobros (
  seller_id   bigint,
  cliente     text,
  mes         text,
  guias       bigint,
  monto       double precision,
  estatus     text,                 -- 'generado' | 'enviado' | 'pagado'
  generado_en timestamptz default now(),
  nota        text,
  primary key (seller_id, mes)
);

create table if not exists cobro_adjuntos (
  seller_id  bigint,
  mes        text,
  filename   text,
  path       text,                  -- ruta del PDF en Supabase Storage (bucket 'facturas')
  subido_en  timestamptz default now(),
  primary key (seller_id, mes)
);

-- Storage: crear un bucket 'facturas' (privado) para los PDFs y los Excel subidos.
-- Auth/RLS: cuando se conecte el login de finanzas, habilitar RLS por rol.
