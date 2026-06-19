# CLAUDE.md — conciliacion (motor costo paquetería vs cobro sistema)

## 0. Propósito

Dar a **finanzas** trazabilidad de lo que se cobra y devengado correcto contra el gasto de paquetería. Cruza **factura del carrier (costo)** vs **cobro en sistema (ClickHouse)** + **facturas reales a clientes de crédito**, guía por guía. Subproyecto independiente bajo el monorepo DashboardT1.

## 1. Decisiones de arranque (confirmadas con el usuario, jun 2026)

- **DuckDB como motor** (`data/conciliacion.duckdb`), NO se escribe al warehouse. En la nube vive en un volumen de Railway.
- Arrancó CLI + Excel; hoy hay **UI web** (FastAPI + `ui/index.html`) con login y desplegada (ver §9).
- **Crédito**: el modelo de cobro evolucionó (jun 2026) a **tarifa configurada (manual)** o **sistema + extra (automático)** — ver §3. (Ya NO es "factura real subida"; `facturas_cliente` quedó como opcional.)
- **Solo DHL** al inicio; parsers extensibles por carrier.

## 2. Arquitectura

```
Archivos carrier (Acre xlsx) --parser--> facturas_carrier (DuckDB)
Facturas reales a cliente   --ingest--> facturas_cliente (DuckDB)
ClickHouse fct_shipments + fct_wallet_transactions --sync--> ch_shipments / ch_sobrepeso (DuckDB)
                                  |
                          reconcile.build()  (SQL en DuckDB)
                                  v
                          tabla reconciliacion --> export Excel
```

## 3. Reglas de negocio (el núcleo — viven en `reconcile.py` y `config.py`)

- **Llave:** `No.De Guia` del Acre = `fct_shipments.shipment_number` (filtrar `carrier_name`). NO es `shipment_id` (ese es _id de Mongo) ni `tracking_code` (estatus).
- **Cliente real = `seller_name`** de ClickHouse. Resuelve revendedores tipo IBL (un commerce, muchos remitentes en el Acre). NUNCA usar el `Remitente` del Acre como cliente.
- **Modelo de pago = `seller_configuration_name`**: `Prepago` (paga al día con saldo) vs `Prepago sin saldo` (crédito, se factura con rezago).
- **Modelo de cobro (jun 2026, núcleo de `reconcile.py` + `cobro/generar`).** Por guía, con `m` = margen del cliente (`config_credito.margen` o `config.MARGEN_EXTRA_DEFAULT`=0.14):
  - `ya_cobrado = sale_price + sobrepeso_ya_cobrado`
  - `piso = costo × (1 + m)`  ·  `extra = max(0, piso − ya_cobrado)` → cubre sobrepeso, retornos y desfase, **con margen**.
  - **Crédito manual** (`config_credito.metodo='manual'`): ingreso = **tarifa(zona,kilo)** de la matriz `tarifas` (re-pesos y retornos ya vienen como guía en el Acre → la tarifa los cubre; sin `extra`).
  - **Crédito automático**: ingreso = `max(ya_cobrado, piso)` = precio del sistema **+ extra**.
  - **Prepago**: ingreso = `max(ya_cobrado, piso)`; el **`extra`** es lo único pendiente de cobrar (ya pagó la base con saldo).
- **Internos** SN00449 / SE00724 / Inbursa*: `sale_price=0` inter-empresa; ingreso = costo × 1.14. En `config.CUENTAS_INTERNAS_MARGEN` / `INTERNAS_SUBSTRING_MARGEN`.
- **Sobrepeso** = `Cargo por sobrepeso` en `fct_wallet_transactions` (reference = guía). El gap costo−ya_cobrado en prepago suele ser re-peso por sub-declaración (cliente declara chico → DHL re-pesa → se cobra como `extra`).
- **Retornos** (`Referencia ~ ^RT\d{10}$`, los 10 dígitos = guía original): se atribuyen al **seller de la guía original** (join extra a `ch_shipments` por `orig_guia`), se cobran completos a costo+margen como una guía más. ~97% se resuelven.
- **Dedup:** ClickHouse ReplacingMergeTree → `argMax(_synced_at)`. Acre refacturas (MEXR→MEXDR, mismos montos) → una fila por guía (primera por `fecha_factura`).
- **Importe Neto del Acre incluye IVA.** Las facturas a cliente (Total) también con IVA → comparables.

## 4. Estatus por guía

`Cobrado OK` · `Extra por cobrar` (sobrepeso/retorno/desfase pendiente) · `Falta cobrar (credito)` (manual sin tarifa para esa zona/kilo) · `Interno (14%)` · `Cobrado, costo pendiente` (cobrado en sistema, la paquetería aún no factura) · `Sin guia en sistema`.

## 4b. Cobranza = libro de cuentas por cobrar (`cobros` + `pagos`)

`cobro/generar` corre al cerrar el mes y arma el cobro por cliente:
- **Crédito** → factura completa (Σ `ingreso`). `concepto='factura'`.
- **Prepago** → solo extras (Σ `extra`); aparece únicamente si hay extras > 0. `concepto='extra'`.
- Es **upsert**: conserva el ciclo (fecha enviada, pagos) de cobros ya existentes al regenerar.

Ciclo en la UI (vista Cobranza): `generado → enviado → aprobado → parcial → pagado`.
- **Enviar** (`cobro/enviar`): fija `fecha_enviada` y calcula `fecha_vencimiento = fecha + dias_credito` (de `config_credito`, default `config.DIAS_CREDITO_DEFAULT`=30). `dias_atraso` se deriva contra `current_date`. Las vencidas (atraso > 0) se pintan en rojo.
- **Aprobado** (`cobro/estatus` con `'aprobado'`): el cliente aprobó; los días de crédito **siguen contando desde `fecha_enviada`** (aprobar no reinicia el vencimiento).
- **Pagos parciales** (`cobro/pago` → tabla `pagos`, histórico por cliente; `cobro/pagos` lista, `cobro/pago/eliminar` borra por `rowid`): `saldo = monto − Σ pagos`; `_recompute_cobro` actualiza estatus a `parcial`/`pagado` y conserva `enviado`/`aprobado` si no hay pagos.
- **Excel por cliente** (`cobro/seller`): detalle SIN costo ni margen. Crédito = todas las guías con `ingreso`; prepago = solo guías con `extra`.
- **PDF de factura** → Supabase Storage (bucket `facturas`); en dev local cae a disco.

Config por cliente de crédito (vista Configuración de tarifas): `metodo` (automatica/manual), **`margen`** (para extras), **`dias_credito`**. Endpoint `cliente/cobro` hace merge (no pisa campos no enviados).

## 5. ClickHouse (fuente)

- Conexión: creds en `../data-platform/.env.local` (password bueno de PROD está en `../data-platform/cube/.env` si `.env.local` da 401). Host PROD `b9yqryvtor...`, DB `t1_envios`.
- Tablas: `fct_shipments` (shipment_number, seller_name, seller_configuration_name, sale_price, carrier_name, created_at), `fct_wallet_transactions` (transaction_type_name='Cargo por sobrepeso', reference, amount).

## 6. Comandos

```bash
PY=.venv/bin/python
$PY -m conciliacion.cli sync-ch
$PY -m conciliacion.cli ingest-carrier <acre.xlsx> --reset
$PY -m conciliacion.cli ingest-cliente <fact_cliente.xlsx> --reset
$PY -m conciliacion.cli reconcile
$PY -m conciliacion.cli report
$PY -m conciliacion.cli status
$PY -m pytest tests/ -q
```

## 7. Convenciones

- Responder al usuario en español.
- snake_case en storage; nombres de columna del Acre tal cual ("No.De Guia", "Importe Neto") al mapear.
- Mapear SIEMPRE por encabezado (3 variantes de esquema en los Acres).
- No commitear `.env.local` ni `data/*.duckdb`.
- Para agregar carrier: nuevo `parsers/<carrier>.py` que haga `yield LineaFactura`, registrar en `parsers/__init__.py` y `config.CARRIER_CH`.

## 8. Roadmap

- F1 Fundación (parser DHL + DuckDB + sync) ✅
- F2 Motor de conciliación ✅
- F3 Reportes Excel ✅
- F4 API FastAPI + UI web (`conciliacion/api/` + `ui/index.html`): subir archivos, sync, conciliar, KPIs y tablas ✅. Levantar: `.venv/bin/uvicorn conciliacion.api.main:app --port 8770`.
- F5 Modelo de cobranza: extras con margen, factura por tipo de cliente, libro de cuentas por cobrar (cobros/pagos, enviar/pagar/vencimiento), retornos atribuidos ✅
- F6 Despliegue: GitHub + Railway (Docker + volumen) + Supabase (Auth + Storage) ✅ (ver §9)
- F7 Multi-carrier (FedEx/UPS/AMPM/Estafeta…) + alertas — pendiente.

## 9. Despliegue (nube)

- **Repo:** GitHub `antarnl9/conciliacion` (ramas: `main`). NO commitear `.env.local` ni `data/*.duckdb`.
- **Backend + UI:** Railway (Dockerfile en la raíz; uvicorn en `$PORT`/8770). Volumen montado en **`/app/data`** = DuckDB persistente. Variables: `CH_*` (ClickHouse) + `SUPABASE_*`.
- **Auth + Storage:** Supabase. `supa.py` (stdlib) valida el token contra `/auth/v1/user` y sube/firma archivos en el bucket `facturas`. Middleware en `api/main.py` protege `/api/*` (token en header o `?token=` para descargas); `/api/config` expone URL + llave pública al frontend. Si no hay `SUPABASE_*`, el login se desactiva (dev local abierto).
- **Variables Supabase** (3): `SUPABASE_URL`, `SUPABASE_PUBLIC_KEY` (anon/publishable), `SUPABASE_SECRET_KEY` (service_role/secret). Sin `JWT_SECRET` (se valida por API).
- **Paths en código:** `config.ROOT` = raíz del proyecto (`/app` en Docker). `UI_DIR = ROOT/ui`, `DATA_DIR = ROOT/data`. NO usar `REPO_ROOT` para esos (rompe en Docker).
- **Migraciones:** `db.connect()` corre `ALTER ... ADD COLUMN IF NOT EXISTS` idempotentes sobre el volumen existente. Al cambiar el esquema de `reconciliacion`, hay que **re-conciliar** (CREATE OR REPLACE) para que tome efecto.
