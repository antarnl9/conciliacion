"""CLI del motor de conciliación.

Flujo típico:
  python -m conciliacion.cli sync-ch                      # espejo de ClickHouse (1 vez al día)
  python -m conciliacion.cli ingest-carrier <acre.xlsx>   # subir factura DHL (costo)
  python -m conciliacion.cli ingest-cliente <fact.xlsx>   # subir factura real a cliente (crédito)
  python -m conciliacion.cli reconcile                    # cruzar
  python -m conciliacion.cli report                       # exportar Excel
  python -m conciliacion.cli status                       # ver resumen rápido
"""
from __future__ import annotations

import argparse
import sys

from . import config, db, ch_sync, reconcile, export
from .parsers import PARSERS


def cmd_sync_ch(args):
    con = db.connect()
    print(f"Sincronizando ClickHouse (carrier={args.carrier}, desde={args.desde})...")
    n1 = ch_sync.sync_shipments(con, carrier=args.carrier, desde=args.desde)
    print(f"  ch_shipments: {n1:,} guias")
    n2 = ch_sync.sync_sobrepeso(con, desde=args.desde)
    print(f"  ch_sobrepeso: {n2:,} guias con sobrepeso")
    n3 = ch_sync.sync_recargas(con, desde=args.desde)
    print(f"  ch_recargas:  {n3:,} clientes con recargas")
    con.close()


def cmd_ingest_carrier(args):
    parser = PARSERS.get(args.carrier)
    if parser is None:
        sys.exit(f"Carrier no soportado: {args.carrier}. Disponibles: {list(PARSERS)}")
    con = db.connect()
    if args.reset:
        con.execute("DELETE FROM facturas_carrier WHERE carrier = ?", [args.carrier])
    total = 0
    for f in args.files:
        print(f"Parseando {f} ...")
        n = db.insert_facturas_carrier(con, parser(f))
        print(f"  +{n:,} lineas")
        total += n
    print(f"Total cargado: {total:,} lineas de factura (carrier={args.carrier})")
    con.close()


def cmd_ingest_cliente(args):
    """Carga facturas reales a cliente (crédito): columnas guia + total [+ cliente]."""
    from .parsers.cliente import iter_facturas_cliente
    con = db.connect()
    if args.reset:
        con.execute("DELETE FROM facturas_cliente")
    total = 0
    for f in args.files:
        print(f"Cargando {f} ...")
        n = db.insert_facturas_cliente(con, iter_facturas_cliente(f))
        print(f"  +{n:,}")
        total += n
    print(f"Total facturas cliente: {total:,}")
    con.close()


def cmd_reconcile(args):
    con = db.connect()
    n = reconcile.build(con)
    print(f"Conciliacion construida: {n:,} guias\n")
    _print_resumen(con)
    con.close()


def cmd_report(args):
    con = db.connect()
    out = export.export(con, args.out)
    print(f"Excel escrito: {out}")
    con.close()


def cmd_status(args):
    con = db.connect()
    for t in ("facturas_carrier", "facturas_cliente", "ch_shipments", "ch_sobrepeso"):
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t:20} {n:,}")
    if con.execute("SELECT count(*) FROM reconciliacion").fetchone()[0]:
        print()
        _print_resumen(con)
    con.close()


def _print_resumen(con):
    print(f"{'estatus':26} {'guias':>10} {'costo':>16} {'ingreso':>16} {'margen':>16}")
    for est, g, c, i, m in reconcile.resumen(con):
        print(f"{est:26} {g:>10,} {c or 0:>16,.0f} {i or 0:>16,.0f} {m or 0:>16,.0f}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="conciliacion")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync-ch", help="Espejo ClickHouse -> DuckDB")
    s.add_argument("--carrier", default="dhl")
    s.add_argument("--desde", default="2025-09-01")
    s.set_defaults(func=cmd_sync_ch)

    s = sub.add_parser("ingest-carrier", help="Subir factura(s) de paqueteria (costo)")
    s.add_argument("files", nargs="+")
    s.add_argument("--carrier", default="dhl")
    s.add_argument("--reset", action="store_true", help="Borra lo previo de ese carrier")
    s.set_defaults(func=cmd_ingest_carrier)

    s = sub.add_parser("ingest-cliente", help="Subir factura(s) reales a cliente de credito")
    s.add_argument("files", nargs="+")
    s.add_argument("--reset", action="store_true")
    s.set_defaults(func=cmd_ingest_cliente)

    s = sub.add_parser("reconcile", help="Construir la conciliacion")
    s.set_defaults(func=cmd_reconcile)

    s = sub.add_parser("report", help="Exportar Excel")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("status", help="Estado de las tablas")
    s.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
