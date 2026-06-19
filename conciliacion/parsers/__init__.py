"""Parsers de archivos de paquetería -> esquema canónico de factura."""
from .dhl import parse_dhl

PARSERS = {
    "dhl": parse_dhl,
}
