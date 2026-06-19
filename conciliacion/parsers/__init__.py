"""Parsers de archivos de paquetería -> esquema canónico de factura."""
from .dhl import parse_dhl
from .fedex import parse_fedex
from .paquete_express import parse_paquete_express

PARSERS = {
    "dhl": parse_dhl,
    "fedex": parse_fedex,
    "paquete_express": parse_paquete_express,
}
