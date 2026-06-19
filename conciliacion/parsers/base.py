"""Esquema canónico de una línea de factura de paquetería.

Cualquier parser (DHL hoy, FedEx/UPS después) debe devolver filas con estos campos.
El número de guía (`guia`) es la llave de cruce contra ClickHouse (shipment_number).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from datetime import date
from typing import Optional

# Orden de columnas canónico (coincide con la tabla DuckDB facturas_carrier)
CANONICAL_COLUMNS = [
    "carrier", "guia", "cuenta", "referencia", "producto",
    "origen", "destino", "piezas", "kilos",
    "fecha_envio", "fecha_factura", "no_factura",
    "flete", "seguro", "descuento", "recargos", "iva", "importe_neto",
    "moneda", "remitente", "destinatario", "es_retorno", "archivo_origen", "zona",
]


@dataclass
class LineaFactura:
    carrier: str
    guia: str
    importe_neto: float
    archivo_origen: str
    cuenta: Optional[str] = None
    referencia: Optional[str] = None
    producto: Optional[str] = None
    origen: Optional[str] = None
    destino: Optional[str] = None
    piezas: Optional[int] = None
    kilos: Optional[float] = None
    fecha_envio: Optional[date] = None
    fecha_factura: Optional[date] = None
    no_factura: Optional[str] = None
    flete: float = 0.0
    seguro: float = 0.0
    descuento: float = 0.0
    recargos: float = 0.0
    iva: float = 0.0
    moneda: str = "MXN"
    remitente: Optional[str] = None
    destinatario: Optional[str] = None
    es_retorno: bool = False
    zona: Optional[str] = None

    def as_row(self) -> tuple:
        d = asdict(self)
        return tuple(d[c] for c in CANONICAL_COLUMNS)


assert {f.name for f in fields(LineaFactura)} == set(CANONICAL_COLUMNS), \
    "LineaFactura y CANONICAL_COLUMNS deben coincidir"
