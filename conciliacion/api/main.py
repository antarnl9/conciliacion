"""API FastAPI del motor de conciliación + UI web para finanzas.

Levantar:  .venv/bin/uvicorn conciliacion.api.main:app --port 8770
Abrir:     http://localhost:8770
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config, db, ch_sync, reconcile, export
from ..parsers import PARSERS
from . import jobs

UI_DIR = config.ROOT / "ui"            # .../conciliacion/ui (local) y /app/ui (Docker)
UPLOADS = config.DATA_DIR / "uploads"

app = FastAPI(title="Conciliación T1", version="0.1.0")


# ---------- helpers ----------
def _save_upload(up: UploadFile, subdir: str) -> Path:
    dest_dir = UPLOADS / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / up.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(up.file, f)
    return dest


def _rows(con, sql, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fecha(desde: str, hasta: str, col: str = "mes_envio"):
    """Devuelve (clausulas, params) para filtrar por rango de mes_envio (YYYY-MM)."""
    cl, p = [], []
    if desde:
        cl.append(f"{col} >= ?"); p.append(desde)
    if hasta:
        cl.append(f"{col} <= ?"); p.append(hasta)
    return cl, p


# ---------- API ----------
@app.get("/api/status")
def status():
    con = db.connect()
    try:
        tablas = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("facturas_carrier", "facturas_cliente", "ch_shipments", "ch_sobrepeso")}
        reconciled = con.execute("SELECT count(*) FROM reconciliacion").fetchone()[0]
        kpis, resumen = None, []
        if reconciled:
            resumen = _rows(con, """
                SELECT estatus, count(*) guias, round(sum(costo),2) costo,
                       round(sum(ingreso),2) ingreso, round(sum(ingreso)-sum(costo),2) margen
                FROM reconciliacion GROUP BY estatus ORDER BY costo DESC""")
            k = con.execute("""
                SELECT round(sum(costo),2) costo,
                       -- ingreso/utilidad REALIZADOS = solo guías que ya tienen costo de paquetería
                       round(sum(CASE WHEN costo IS NOT NULL THEN ingreso ELSE 0 END),2) ingreso,
                       round(sum(CASE WHEN costo IS NOT NULL AND ingreso IS NOT NULL THEN ingreso - costo ELSE 0 END),2) margen,
                       round(sum(CASE WHEN estatus IN ('Falta cobrar (credito)','Sobrepeso pendiente')
                              THEN costo - coalesce(ingreso,0) ELSE 0 END),2) por_cobrar,
                       -- cobrado pero la paquetería aún no factura el costo (devengado)
                       round(sum(CASE WHEN costo IS NULL THEN coalesce(ingreso,0) ELSE 0 END),2) por_devengar
                FROM reconciliacion""").fetchone()
            kpis = {"costo": k[0], "ingreso": k[1], "margen": k[2], "por_cobrar": k[3],
                    "por_devengar": k[4], "guias": reconciled}
        return {"tablas": tablas, "reconciled": reconciled, "kpis": kpis,
                "resumen": resumen, "jobs": jobs.snapshot()}
    finally:
        con.close()


@app.post("/api/upload/carrier")
def upload_carrier(carrier: str = Query("dhl"), reset: bool = Query(False),
                   file: UploadFile = File(...)):
    parser = PARSERS.get(carrier)
    if parser is None:
        return JSONResponse({"error": f"carrier no soportado: {carrier}"}, 400)
    path = _save_upload(file, "carrier")

    def _job():
        con = db.connect()
        try:
            if reset:
                con.execute("DELETE FROM facturas_carrier WHERE carrier = ?", [carrier])
            n = db.insert_facturas_carrier(con, parser(str(path)))
            return f"{n:,} lineas cargadas de {file.filename}"
        finally:
            con.close()

    return jobs.run(f"ingest-carrier:{file.filename}", _job)


@app.post("/api/upload/cliente")
def upload_cliente(reset: bool = Query(False), file: UploadFile = File(...)):
    path = _save_upload(file, "cliente")

    def _job():
        from ..parsers.cliente import iter_facturas_cliente
        con = db.connect()
        try:
            if reset:
                con.execute("DELETE FROM facturas_cliente")
            total = db.insert_facturas_cliente(con, iter_facturas_cliente(str(path)))
            return f"{total:,} facturas de cliente cargadas"
        finally:
            con.close()

    return jobs.run(f"ingest-cliente:{file.filename}", _job)


@app.post("/api/sync-ch")
def sync_ch(desde: str = Query("2025-09-01"), carrier: str = Query("dhl")):
    def _job():
        con = db.connect()
        try:
            n1 = ch_sync.sync_shipments(con, carrier=carrier, desde=desde)
            n2 = ch_sync.sync_sobrepeso(con, desde=desde)
            n3 = ch_sync.sync_recargas(con, desde=desde)
            return f"{n1:,} guias + {n2:,} sobrepeso + {n3:,} clientes con recargas"
        finally:
            con.close()

    return jobs.run("sync-ch", _job)


@app.post("/api/reconcile")
def do_reconcile():
    def _job():
        con = db.connect()
        try:
            n = reconcile.build(con)
            return f"{n:,} guias conciliadas"
        finally:
            con.close()

    return jobs.run("reconcile", _job)


@app.get("/api/clientes/prepago")
def clientes_prepago(limit: int = 500, desde: str = "", hasta: str = ""):
    fc, fp = _fecha(desde, hasta, "r.mes_envio")
    where = "WHERE r.tipo = 'prepago'" + "".join(" AND " + c for c in fc)
    con = db.connect()
    try:
        return _rows(con, f"""
            SELECT r.cliente_real,
                   count(*) guias,
                   round(sum(r.costo),2) costo,
                   round(sum(r.ingreso),2) ingreso,
                   round(sum(r.ingreso)-sum(r.costo),2) margen,
                   round(100*(sum(r.ingreso)-sum(r.costo))/nullif(sum(r.costo),0),1) margen_pct,
                   round(sum(r.sobrepeso_cobrado),2) sobrepeso,
                   round(sum(CASE WHEN r.es_retorno THEN r.costo ELSE 0 END),2) retornos,
                   count(*) FILTER (WHERE r.es_retorno) guias_retorno,
                   round(any_value(cr.recargas),2) recargas
            FROM reconciliacion r
            LEFT JOIN ch_recargas cr ON cr.seller_id = r.seller_id
            {where}
            GROUP BY r.cliente_real ORDER BY costo DESC LIMIT ?""", fp + [limit])
    finally:
        con.close()


@app.get("/api/clientes/credito")
def clientes_credito(limit: int = 500, desde: str = "", hasta: str = ""):
    fc, fp = _fecha(desde, hasta)
    where = "WHERE tipo = 'credito'" + "".join(" AND " + c for c in fc)
    con = db.connect()
    try:
        return _rows(con, f"""
            SELECT cliente_real,
                   count(*) guias,
                   round(sum(costo),2) costo,
                   round(sum(coalesce(ingreso,0)),2) facturado,
                   round(sum(CASE WHEN ingreso IS NULL THEN costo ELSE 0 END),2) falta_cobrar,
                   round(sum(sobrepeso_cobrado),2) sobrepeso,
                   round(sum(CASE WHEN es_retorno THEN costo ELSE 0 END),2) retornos,
                   count(*) FILTER (WHERE es_retorno) guias_retorno
            FROM reconciliacion {where}
            GROUP BY cliente_real ORDER BY costo DESC LIMIT ?""", fp + [limit])
    finally:
        con.close()


@app.get("/api/guias")
def guias(limit: int = 100, offset: int = 0, tipo: str = "", estatus: str = "",
          cliente: str = "", q: str = "", desde: str = "", hasta: str = ""):
    where, params = [], []
    if tipo:
        where.append("tipo = ?"); params.append(tipo)
    if estatus:
        where.append("estatus = ?"); params.append(estatus)
    if cliente:
        where.append("cliente_real ILIKE ?"); params.append(f"%{cliente}%")
    if q:
        where.append("guia ILIKE ?"); params.append(f"%{q}%")
    if desde:
        where.append("mes_envio >= ?"); params.append(desde)
    if hasta:
        where.append("mes_envio <= ?"); params.append(hasta)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    con = db.connect()
    try:
        total = con.execute(f"SELECT count(*) FROM reconciliacion {wsql}", params).fetchone()[0]
        rows = _rows(con, f"""
            SELECT guia, cliente_real, tipo, estatus, es_retorno,
                   round(costo,2) costo, sale_price, round(sobrepeso_cobrado,2) sobrepeso,
                   round(ingreso,2) ingreso, round(margen,2) margen, mes_envio
            FROM reconciliacion {wsql}
            ORDER BY costo DESC LIMIT ? OFFSET ?""", params + [limit, offset])
        return {"total": total, "rows": rows}
    finally:
        con.close()


@app.get("/api/por-cobrar")
def por_cobrar(limit: int = 200, desde: str = "", hasta: str = ""):
    fc, fp = _fecha(desde, hasta)
    extra = "".join(" AND " + c for c in fc)
    con = db.connect()
    try:
        return _rows(con, f"""
            SELECT cliente_real, estatus, count(*) guias,
                   round(sum(costo),2) costo,
                   round(sum(costo)-sum(coalesce(ingreso,0)),2) falta_cobrar
            FROM reconciliacion
            WHERE estatus IN ('Falta cobrar (credito)','Sobrepeso pendiente','Cobrado bajo costo'){extra}
            GROUP BY cliente_real, estatus ORDER BY falta_cobrar DESC LIMIT ?""", fp + [limit])
    finally:
        con.close()


@app.get("/api/cierre/meses")
def cierre_meses():
    con = db.connect()
    try:
        meses = _rows(con, """
            SELECT mes_envio AS mes, count(*) guias,
                   round(sum(costo),2) costo,
                   round(sum(CASE WHEN costo IS NOT NULL THEN ingreso ELSE 0 END),2) ingreso,
                   round(sum(CASE WHEN costo IS NOT NULL AND ingreso IS NOT NULL THEN ingreso - costo ELSE 0 END),2) utilidad,
                   round(sum(CASE WHEN estatus IN ('Falta cobrar (credito)','Sobrepeso pendiente')
                          THEN costo - coalesce(ingreso,0) ELSE 0 END),2) por_cobrar,
                   round(sum(CASE WHEN costo IS NULL THEN coalesce(ingreso,0) ELSE 0 END),2) por_devengar
            FROM reconciliacion WHERE mes_envio IS NOT NULL AND mes_envio <> 'SIN'
            GROUP BY mes_envio ORDER BY mes_envio DESC""")
        cerrados = {r["mes"]: r for r in _rows(con, "SELECT mes, estatus, cerrado_en FROM periodos")}
        for m in meses:
            c = cerrados.get(m["mes"])
            m["estatus"] = c["estatus"] if c else "abierto"
            m["cerrado_en"] = c["cerrado_en"] if c else None
        return meses
    finally:
        con.close()


@app.get("/api/cierre/detalle")
def cierre_detalle(mes: str):
    """Detalle de un mes: cuánto se cobró por cliente y por paquetería."""
    con = db.connect()
    try:
        return _rows(con, """
            SELECT cliente_real, carrier, count(*) guias,
                   round(sum(costo),2) costo,
                   round(sum(coalesce(ingreso,0)),2) cobro,
                   round(sum(coalesce(ingreso,0)) - sum(coalesce(costo,0)),2) margen
            FROM reconciliacion WHERE mes_envio = ?
            GROUP BY cliente_real, carrier ORDER BY costo DESC NULLS LAST LIMIT 3000""", [mes])
    finally:
        con.close()


@app.get("/api/cierre/detalle/excel")
def cierre_detalle_excel(mes: str):
    """Descarga el detalle del mes (cliente × paquetería) en Excel."""
    from openpyxl import Workbook
    con = db.connect()
    try:
        rows = con.execute("""
            SELECT cliente_real, carrier, count(*) guias,
                   round(sum(costo),2), round(sum(coalesce(ingreso,0)),2),
                   round(sum(coalesce(ingreso,0)) - sum(coalesce(costo,0)),2)
            FROM reconciliacion WHERE mes_envio = ?
            GROUP BY cliente_real, carrier ORDER BY sum(costo) DESC NULLS LAST""", [mes]).fetchall()
        wb = Workbook(); ws = wb.active; ws.title = "Detalle"
        ws.append([f"Detalle de cierre · {mes}"])
        ws.append(["Cliente", "Paquetería", "Guías", "Costo", "Cobro", "Margen"])
        for r in rows:
            ws.append(list(r))
        config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = config.EXPORTS_DIR / f"detalle_cierre_{mes}.xlsx"
        wb.save(out)
        return FileResponse(str(out), filename=out.name)
    finally:
        con.close()


@app.post("/api/cierre/cerrar")
def cerrar_mes(mes: str = Query(...)):
    con = db.connect()
    try:
        row = con.execute("""
            SELECT round(sum(costo),2), round(sum(ingreso),2),
                   round(sum(CASE WHEN ingreso IS NOT NULL THEN ingreso - costo ELSE 0 END),2)
            FROM reconciliacion WHERE mes_envio = ?""", [mes]).fetchone()
        con.execute("DELETE FROM periodos WHERE mes = ?", [mes])
        con.execute("INSERT INTO periodos VALUES (?,?,?,?,?, now())",
                    [mes, "cerrado", row[0], row[1], row[2]])
        return {"mes": mes, "estatus": "cerrado", "costo": row[0], "ingreso": row[1], "utilidad": row[2]}
    finally:
        con.close()


@app.post("/api/cierre/reabrir")
def reabrir_mes(mes: str = Query(...)):
    con = db.connect()
    try:
        con.execute("DELETE FROM periodos WHERE mes = ?", [mes])
        return {"mes": mes, "estatus": "abierto"}
    finally:
        con.close()


@app.get("/api/config-credito")
def get_config_credito():
    con = db.connect()
    try:
        return _rows(con, """
            SELECT r.seller_id, any_value(r.cliente_real) cliente, count(*) guias,
                   round(sum(r.costo),2) costo,
                   any_value(cc.metodo) metodo, any_value(cc.valor) valor, any_value(cc.nota) nota
            FROM reconciliacion r
            LEFT JOIN config_credito cc ON cc.seller_id = r.seller_id
            WHERE r.tipo = 'credito'
            GROUP BY r.seller_id ORDER BY costo DESC""")
    finally:
        con.close()


@app.post("/api/config-credito")
def set_config_credito(payload: dict):
    con = db.connect()
    try:
        sid = int(payload["seller_id"])
        con.execute("DELETE FROM config_credito WHERE seller_id = ?", [sid])
        con.execute("INSERT INTO config_credito VALUES (?,?,?,?,?)", [
            sid, payload.get("cliente"), payload.get("metodo"),
            float(payload["valor"]) if payload.get("valor") not in (None, "") else None,
            payload.get("nota")])
        return {"ok": True}
    finally:
        con.close()


@app.get("/api/tarifas/clientes")
def tarifas_clientes():
    """Clientes de crédito + su tipo de cobro (automatica/manual) + filas de tarifa."""
    con = db.connect()
    try:
        return _rows(con, """
            SELECT r.seller_id, any_value(r.cliente_real) cliente, count(*) guias,
                   round(sum(r.costo),2) costo,
                   coalesce(any_value(cc.metodo),'automatica') cobro_tipo,
                   (SELECT count(*) FROM tarifas t WHERE t.seller_id = r.seller_id) filas_tarifa
            FROM reconciliacion r
            LEFT JOIN config_credito cc ON cc.seller_id = r.seller_id
            WHERE r.tipo = 'credito'
            GROUP BY r.seller_id ORDER BY costo DESC""")
    finally:
        con.close()


@app.post("/api/cliente/cobro")
def set_cobro(payload: dict):
    """Define cobro automatico (ClickHouse) o manual (tarifa) para un cliente."""
    con = db.connect()
    try:
        sid = int(payload["seller_id"])
        con.execute("DELETE FROM config_credito WHERE seller_id = ?", [sid])
        con.execute("INSERT INTO config_credito VALUES (?,?,?,?,?)",
                    [sid, payload.get("cliente"), payload.get("cobro_tipo", "automatica"), None, None])
        return {"ok": True}
    finally:
        con.close()


@app.get("/api/tarifas")
def get_tarifas(seller_id: int, carrier: str = "dhl"):
    con = db.connect()
    try:
        return _rows(con, """
            SELECT zona, peso_min, peso_max, precio, vigencia_desde, vigencia_hasta
            FROM tarifas WHERE seller_id = ? AND carrier = ? ORDER BY zona, peso_min""",
            [seller_id, carrier])
    finally:
        con.close()


@app.get("/api/cobranza")
def cobranza():
    """Cobranza: por cliente, lo que falta cobrar (worklist de finanzas)."""
    con = db.connect()
    try:
        return _rows(con, """
            SELECT cliente_real, any_value(tipo) tipo, count(*) guias,
                   round(sum(costo),2) costo,
                   round(sum(coalesce(ingreso,0)),2) cobrado,
                   round(sum(costo) - sum(coalesce(ingreso,0)),2) falta_cobrar,
                   round(100*sum(coalesce(ingreso,0))/nullif(sum(costo),0),1) pct_cobrado
            FROM reconciliacion
            WHERE tipo IN ('prepago','credito')
            GROUP BY cliente_real
            HAVING falta_cobrar > 1
            ORDER BY falta_cobrar DESC LIMIT 500""")
    finally:
        con.close()


@app.post("/api/tarifas")
def save_tarifas(payload: dict):
    """Reemplaza todas las filas de tarifa de un cliente (carrier dhl)."""
    sid = int(payload["seller_id"])
    cliente = payload.get("cliente")
    carrier = payload.get("carrier", "dhl")
    rows = payload.get("rows", [])
    con = db.connect()
    try:
        con.execute("DELETE FROM tarifas WHERE seller_id = ? AND carrier = ?", [sid, carrier])
        for r in rows:
            con.execute("INSERT INTO tarifas VALUES (?,?,?,?,?,?,?,?,?)", [
                sid, cliente, carrier, str(r.get("zona", "")).strip(),
                _f(r.get("peso_min")), _f(r.get("peso_max")), _f(r.get("precio")),
                r.get("vigencia_desde") or None, r.get("vigencia_hasta") or None])
        return {"ok": True, "filas": len(rows)}
    finally:
        con.close()


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.post("/api/cobro/generar")
def generar_cobro(mes: str = Query(...)):
    """Genera el cobro por cliente para un mes (desde la conciliación)."""
    con = db.connect()
    try:
        rows = _rows(con, """
            SELECT seller_id, any_value(cliente_real) cliente, count(*) guias,
                   round(sum(coalesce(ingreso,0)),2) monto
            FROM reconciliacion
            WHERE mes_envio = ? AND seller_id IS NOT NULL AND tipo = 'credito'
            GROUP BY seller_id HAVING monto > 0 ORDER BY monto DESC""", [mes])
        con.execute("DELETE FROM cobros WHERE mes = ?", [mes])
        for r in rows:
            con.execute("INSERT INTO cobros VALUES (?,?,?,?,?,?, now(), NULL)",
                        [r["seller_id"], r["cliente"], mes, r["guias"], r["monto"], "generado"])
        return {"mes": mes, "sellers": len(rows), "monto_total": round(sum(r["monto"] for r in rows), 2)}
    finally:
        con.close()


@app.get("/api/cobros")
def list_cobros(mes: str):
    con = db.connect()
    try:
        return _rows(con, """
            SELECT c.seller_id, c.cliente, c.guias, c.monto, c.estatus,
                   (SELECT count(*) FROM cobro_adjuntos a WHERE a.seller_id=c.seller_id AND a.mes=c.mes) > 0 AS tiene_pdf
            FROM cobros c WHERE c.mes = ? ORDER BY c.monto DESC""", [mes])
    finally:
        con.close()


@app.post("/api/cobro/estatus")
def cobro_estatus(payload: dict):
    con = db.connect()
    try:
        con.execute("UPDATE cobros SET estatus = ? WHERE seller_id = ? AND mes = ?",
                    [payload["estatus"], int(payload["seller_id"]), payload["mes"]])
        return {"ok": True}
    finally:
        con.close()


@app.get("/api/cobro/seller")
def cobro_seller(seller_id: int, mes: str):
    """Descarga el cobro de un cliente: detalle estilo Acre (paquetería) + cobro + margen."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    con = db.connect()
    try:
        cliente = con.execute("SELECT any_value(cliente_real) FROM reconciliacion WHERE seller_id = ?",
                              [seller_id]).fetchone()[0] or str(seller_id)
        # Excel PARA EL CLIENTE: detalle del servicio + importe a cobrar (sin costo ni margen).
        rows = con.execute("""
            WITH carrier AS (
                SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY guia ORDER BY fecha_factura NULLS LAST) rn
                               FROM facturas_carrier) WHERE rn = 1)
            SELECT fc.guia, fc.fecha_envio, fc.fecha_factura, fc.producto, fc.origen, fc.destino,
                   fc.piezas, fc.kilos, fc.zona, round(r.ingreso,2) AS importe
            FROM reconciliacion r JOIN carrier fc ON fc.guia = r.guia
            WHERE r.seller_id = ? AND r.mes_envio = ? ORDER BY fc.guia""",
            [seller_id, mes]).fetchall()
        headers = ["Guía", "Fecha Envío", "Fecha Factura", "Producto", "Origen", "Destino",
                   "Piezas", "Kilos", "Zona", "Importe"]
        wb = Workbook(); ws = wb.active; ws.title = "Cobro"
        ws.append([f"Cobro a {cliente} · {mes}"])
        ws.append(headers)
        for r in rows:
            ws.append(list(r))
        tot_i = sum((r[9] or 0) for r in rows)
        ws.append(["TOTAL", "", "", "", "", "", "", "", "", round(tot_i, 2)])
        for ci in (8, 10):
            for cell in ws[get_column_letter(ci)][2:]:
                cell.number_format = '#,##0.00'
        config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in str(cliente) if ch.isalnum() or ch in " -_")[:40].strip() or str(seller_id)
        out = config.EXPORTS_DIR / f"cobro_{safe}_{mes}.xlsx"
        wb.save(out)
        return FileResponse(str(out), filename=out.name)
    finally:
        con.close()


@app.post("/api/cobro/pdf")
def subir_pdf(seller_id: int = Query(...), mes: str = Query(...), file: UploadFile = File(...)):
    """Adjunta el PDF de la factura de un cliente para el mes."""
    dest_dir = config.DATA_DIR / "uploads" / "pdf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{seller_id}_{mes}_{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    con = db.connect()
    try:
        con.execute("DELETE FROM cobro_adjuntos WHERE seller_id = ? AND mes = ?", [seller_id, mes])
        con.execute("INSERT INTO cobro_adjuntos VALUES (?,?,?,?, now())",
                    [seller_id, mes, file.filename, str(dest)])
        return {"ok": True, "filename": file.filename}
    finally:
        con.close()


@app.get("/api/cobro/pdf")
def ver_pdf(seller_id: int, mes: str):
    con = db.connect()
    try:
        row = con.execute("SELECT path, filename FROM cobro_adjuntos WHERE seller_id = ? AND mes = ?",
                          [seller_id, mes]).fetchone()
        if not row:
            return JSONResponse({"error": "sin PDF"}, 404)
        return FileResponse(row[0], filename=row[1], media_type="application/pdf")
    finally:
        con.close()


@app.get("/api/export")
def download_export():
    con = db.connect()
    try:
        out = export.export(con)
        return FileResponse(out, filename="conciliacion.xlsx")
    finally:
        con.close()


# ---------- UI estática ----------
app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
