# Conciliación — costo paquetería vs cobro sistema

Motor para que **finanzas** tenga trazabilidad de lo que se cobra y quede bien **devengado** contra el gasto de paquetería. Cruza la **factura del carrier (costo)** contra **lo cobrado en sistema (ClickHouse)** y las **facturas reales a clientes de crédito**, guía por guía, y dice qué falta cobrar y dónde se está perdiendo.

- **Local, con DuckDB** (un archivo, joins rápidos, no toca el warehouse).
- **CLI + Excel** (sin UI por ahora).
- **Solo DHL** de inicio; diseño extensible a otros carriers.

## Instalación

```bash
cd conciliacion
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Las credenciales de ClickHouse se leen de `../data-platform/.env.local` (o de `conciliacion/.env.local` si lo creas). No se duplican secretos.

## Flujo de uso

```bash
PY=.venv/bin/python

# 1. Espejo de ClickHouse a DuckDB (1 vez al día; trae cliente real, sale_price, sobrepeso)
$PY -m conciliacion.cli sync-ch

# 2. Subir la(s) factura(s) de DHL (el formato Acre = costo)
$PY -m conciliacion.cli ingest-carrier "../Acres/acre junio 2026.xlsx" --reset

# 3. Subir las facturas reales a clientes de crédito (lo que se cobró)
$PY -m conciliacion.cli ingest-cliente "../clientes/acre consolidado.xlsx" --reset

# 4. Conciliar
$PY -m conciliacion.cli reconcile

# 5. Exportar el Excel para finanzas
$PY -m conciliacion.cli report          # -> exports/conciliacion.xlsx

# Ver estado en cualquier momento
$PY -m conciliacion.cli status
```

## UI web (para finanzas, sin CLI)

```bash
.venv/bin/uvicorn conciliacion.api.main:app --port 8770
# abrir http://localhost:8770
```

Desde la pantalla: sincronizar ClickHouse, subir factura DHL, subir facturas a cliente, conciliar y descargar Excel — con KPIs (costo / ingreso / margen / por cobrar) y tablas por estatus, por cliente y "por cobrar". Los procesos lentos corren en background y la pantalla se actualiza sola.

> Nota: usar la UI **o** la CLI, no las dos a la vez (DuckDB es de un solo escritor).

## Qué resuelve (reglas de negocio)

| Tema | Regla |
|---|---|
| Llave de cruce | `No.De Guia` (Acre) = `fct_shipments.shipment_number` |
| Cliente real | `seller_name` de ClickHouse (resuelve revendedores; ignora el `Remitente` del Acre) |
| Prepago | cobra al día; ingreso = `sale_price` + sobrepeso cobrado |
| Crédito | cobra por Acre con rezago; ingreso = factura real subida del cliente |
| Internos (SN/SE/Inbursa) | ingreso = costo × 1.14 (sistema guarda 0) — editable en `config.py` |
| Sobrepeso | `Cargo por sobrepeso` (wallet); el gap costo−ingreso = lo que falta cobrar |

## Estatus por guía

`Cobrado OK` · `Sobrepeso pendiente` · `Falta cobrar (credito)` · `Cobrado bajo costo` · `Interno (14%)` · `Sin guia en sistema`

## Salida (Excel)

- **Resumen_estatus** — costo/ingreso/margen por estatus.
- **Por_cliente** — costo vs ingreso vs margen por cliente real.
- **Por_mes_devengado** — devengado por mes de envío.
- **Por_cobrar** — lo accionable: falta cobrar / sobrepeso pendiente / bajo costo.

## Extender a otro carrier

Crear `conciliacion/parsers/<carrier>.py` con una función que reciba el path y haga `yield LineaFactura(...)`, registrarla en `parsers/__init__.py` y agregar el mapeo de nombre en `config.CARRIER_CH`.
