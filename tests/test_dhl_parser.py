"""Prueba el parser DHL contra un xlsx sintético con el formato Acre."""
import datetime as dt

from openpyxl import Workbook

from conciliacion.parsers import parse_dhl

HEADER = ["Fecha Factura", "No.De Guia", None, "No.De Cuenta", "Referencia", "Producto",
          "Org", "Des", "Pza", "Kilos", "Fecha Envio", "No. Factura", "Flete", "Seguro",
          "Descuento", "Imp.I.V.A.", "Importe Neto", "% IVA", "Moneda", "Tipo de Cambio",
          "Fecha Recepcion", "Remitente", "Destinatario", "FF", "OO"]


def _make(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(HEADER)
    # venta normal con recargos FF+OO
    ws.append([dt.datetime(2026, 5, 28), 1234567890, None, "980", ".", "N ", "MEX", "GDL",
               1, 2, dt.datetime(2026, 5, 5), "MEXR001", 100, 0, 0, 16, 118, 16, "MXN", 1,
               "", "ACME", "Juan", 2.0, 0])
    # retorno (Referencia RT + 10 digitos)
    ws.append([dt.datetime(2026, 5, 28), 9999999999, None, "980", "RT1234567890", "N ", "GDL",
               "MEX", 1, 1, dt.datetime(2026, 5, 6), "MEXR001", 80, 0, 0, 12.8, 92.8, 16,
               "MXN", 1, "", "T1.COM", "Vendedor", 1.5, 0])
    wb.save(path)


def test_parser_dhl(tmp_path):
    f = tmp_path / "acre_test.xlsx"
    _make(str(f))
    rows = list(parse_dhl(str(f)))
    assert len(rows) == 2

    venta = rows[0]
    assert venta.guia == "1234567890"
    assert venta.carrier == "dhl"
    assert abs(venta.importe_neto - 118) < 0.01
    assert abs(venta.recargos - 2.0) < 0.01           # FF=2 + OO=0
    assert venta.fecha_envio == dt.date(2026, 5, 5)
    assert venta.fecha_factura == dt.date(2026, 5, 28)
    assert venta.es_retorno is False

    ret = rows[1]
    assert ret.es_retorno is True                      # RT + 10 dígitos
    assert ret.remitente == "T1.COM"
