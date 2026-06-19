# CLAUDE.md — conciliacion (motor costo paquetería vs cobro sistema)

## 0. Propósito

Dar a **finanzas** trazabilidad de lo que se cobra y devengado correcto contra el gasto de paquetería. Cruza **factura del carrier (costo)** vs **cobro en sistema (ClickHouse)** + **facturas reales a clientes de crédito**, guía por guía. Subproyecto independiente bajo el monorepo DashboardT1.

## 1. Decisiones de arranque (confirmadas con el usuario, jun 2026)

- **Local con DuckDB** (`data/conciliacion.duckdb`), NO se escribe al warehouse.
- **CLI + Excel** primero; sin UI.
- **Crédito**: el ingreso se concilia contra **facturas reales subidas** del cliente (no `sale_price`).
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
- **Modelo de pago = `seller_configuration_name`**: `Prepago` (cobra al día con saldo → ingreso = `sale_price` + sobrepeso) vs `Prepago sin saldo` (crédito → ingreso = factura real subida; si no está, "Falta cobrar").
- **Internos** SN00449 / SE00724 / Inbursa*: `sale_price=0` por temas inter-empresa; ingreso = costo × 1.14. Editable en `config.CUENTAS_INTERNAS_MARGEN` / `INTERNAS_SUBSTRING_MARGEN`.
- **Sobrepeso** = `Cargo por sobrepeso` en `fct_wallet_transactions` (reference = guía). El gap costo−ingreso en prepago suele ser re-peso por sub-declaración de medidas (cliente declara chico → DHL re-pesa → se cobra después).
- **Dedup:** ClickHouse ReplacingMergeTree → `argMax(_synced_at)`. Acre refacturas (MEXR→MEXDR, mismos montos) → una fila por guía (primera por `fecha_factura`).
- **Importe Neto del Acre incluye IVA.** Las facturas a cliente (Total) también con IVA → comparables.

## 4. Estatus por guía

`Cobrado OK` · `Sobrepeso pendiente` · `Falta cobrar (credito)` · `Cobrado bajo costo` · `Interno (14%)` · `Sin guia en sistema`.

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
- F5 Multi-carrier (FedEx/UPS) + alertas (bajo costo / sin guía / sobrepeso pendiente) — pendiente.
