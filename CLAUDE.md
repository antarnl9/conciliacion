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
  - **Crédito automático** (`config_credito.metodo='automatica'`): ingreso = `max(ya_cobrado, piso)` = precio del sistema **+ extra**.
  - **Crédito manual** (métodos `flat` | `margen_global` | `margen_zona` | `margen_kilo`): ingreso = **precio del cliente** calculado por `pricing.precio_sql` (ver abajo); sin `extra` (la tarifa por guía ya cubre re-pesos/retornos).
  - **Prepago**: ingreso = `max(ya_cobrado, piso)`; el **`extra`** es lo único pendiente de cobrar (ya pagó la base con saldo).
- **Módulo de Costos + precio del cliente (`pricing.py`, jul 2026).** Para los métodos manuales el precio sale de un **rate card de costo** por paquetería, no de una matriz por cliente:
  - `costos_tarifa` (carrier, **servicio**, zona×kilo → costo; **vigencia global** por versión; servicio vacío = tarifa general/fallback) · `combustible` (carrier, % por periodo: semanal o mensual) · IVA = `config.IVA_DEFAULT` (16%).
  - El **servicio** de la guía = su `producto` (DHL 'G'/'N', FedEx 'Express Saver', Paquete Express 'Standard'); el costo se busca por servicio (exacto > general).
  - `precio = costo(servicio,zona,kilo,vigente) × (1+combustible_del_periodo) × (1+margen) × (1+IVA)`. `flat` ignora costo/fuel: `precio_fijo × (1+IVA)`.
  - **Recargos** (zona extendida, sobredimensión…): `recargos_mapeo` liga un concepto a la **columna del Acre** donde viene su costo. Al subir el Acre, `recargos.py` captura el costo por guía en `factura_recargos`. El reconcile suma `recargo × (1 + margen_cliente) × (1+IVA)` al ingreso **solo en métodos manuales** (en automático/prepago ya van en el costo real). En el cobro salen como **línea por concepto** (la guía muestra el precio base). Si el cliente no tiene margen → recargo a costo.
  - **Columnas de recargo identificadas** (sugerencias en `RECARGOS_SUG` de la UI): **DHL** códigos `OO`=zona extendida, `YB`=sobredimensión, `YY`=sobrepeso, `YE`=multipieza (`FF`=combustible→va por la config de combustible, no recargo). **FedEx** `ODA`/`OPA`=zona extendida, `Oversize_Charge`=sobredimensión. **Paquete Express** NO desglosa: todo en `Otros` (RAD/EAD son reparto casi universal = costo base).
  - Margen según método: `margen_global` (config_credito.margen), `margen_zona` (tabla `margen_zona`), `margen_kilo` (tabla `margen_kilo`).
  - **Preview** (`/api/tarifa-preview`): muestra la matriz resultante por cliente para validar antes de cerrar. OJO: el alias externo del preview es `cc` (no `ct`) para no chocar con los alias internos de `pricing.precio_sql`.
  - `'manual'` (config vieja) se normaliza a `'flat'`.
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
- F7 Multi-carrier: **DHL + FedEx + Paquete Express** ✅ (UPS/AMPM/Estafeta… pendientes) + alertas — pendiente.

### Paqueterías soportadas (parsers + cruce)
| Carrier (key) | `carrier_name` en CH | Llave de cruce (columna del archivo) | Costo (importe_neto) |
|---|---|---|---|
| `dhl` | `DHL` | `No.De Guia` | `Importe Neto` |
| `fedex` | `FEDEX` | `Guia` (cruza 97.5%) | `Total_Facturado` |
| `paquete_express` | `PAQUETERIA EXPRESS` | **`Rastreo`** (98.7%), NO la `Guía` interna | `Total` |

- **Gotcha Paquete Express:** el `shipment_number` de CH es el **Rastreo**, no la "Guía" (PBC…) que la paquetería usa para facturar. La interna se guarda en `cuenta`.
- **Cuentas/negociaciones:** Paquete Express tiene 2 cuentas (negociaciones) que llegan en **archivos separados** → se tratan como carriers distintos: `paquete_express` (N1) y `paquete_express_2` (N2), mismo parser y mismo `carrier_name` en CH, pero **rate card aparte**. La cuenta queda pegada a la guía desde el archivo (no se resuelve por cliente).
- **Paqueterías activas por cliente** (`cliente_carrier`): en el Tarifario se marcan las paqueterías que usa cada cliente; el selector de tarifa solo muestra esas.
- `ch_sync.sync_shipments` trae **todas** las paqueterías de `CARRIER_CH` en una pasada (`carrier_name IN ...`); el cruce en reconcile es por número de guía (riesgo bajo de colisión entre carriers).
- Para agregar otra: `parsers/<carrier>.py` (mapear por encabezado), registrar en `parsers/__init__.py`, `config.CARRIER_CH`, y `CARRIERS`/`SUPPORTED`/`CARLBL` en la UI.

## 9. Despliegue (nube)

- **Repo:** GitHub `antarnl9/conciliacion` (ramas: `main`). NO commitear `.env.local` ni `data/*.duckdb`.
- **Backend + UI:** Railway (Dockerfile en la raíz; uvicorn en `$PORT`/8770). Volumen montado en **`/app/data`** = DuckDB persistente. Variables: `CH_*` (ClickHouse) + `SUPABASE_*`.
- **Auth + Storage:** Supabase. `supa.py` (stdlib) valida el token contra `/auth/v1/user` y sube/firma archivos en el bucket `facturas`. Middleware en `api/main.py` protege `/api/*` (token en header o `?token=` para descargas); `/api/config` expone URL + llave pública al frontend. Si no hay `SUPABASE_*`, el login se desactiva (dev local abierto).
- **Variables Supabase** (3): `SUPABASE_URL`, `SUPABASE_PUBLIC_KEY` (anon/publishable), `SUPABASE_SECRET_KEY` (service_role/secret). Sin `JWT_SECRET` (se valida por API).
- **Paths en código:** `config.ROOT` = raíz del proyecto (`/app` en Docker). `UI_DIR = ROOT/ui`, `DATA_DIR = ROOT/data`. NO usar `REPO_ROOT` para esos (rompe en Docker).
- **Migraciones:** `db.connect()` corre `ALTER ... ADD COLUMN IF NOT EXISTS` idempotentes sobre el volumen existente. Al cambiar el esquema de `reconciliacion`, hay que **re-conciliar** (CREATE OR REPLACE) para que tome efecto.
