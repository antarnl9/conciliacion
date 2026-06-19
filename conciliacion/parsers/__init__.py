"""Parsers de archivos de paquetería -> esquema canónico de factura."""
from .dhl import parse_dhl
from .fedex import parse_fedex
from .paquete_express import parse_paquete_express

PARSERS = {
    "dhl": parse_dhl,
    "fedex": parse_fedex,
    "paquete_express": parse_paquete_express,
    # 2ª cuenta/negociación de Paquete Express (mismo parser y mismo cruce CH; archivo aparte).
    "paquete_express_2": lambda p, sheet=None: parse_paquete_express(p, sheet, carrier="paquete_express_2"),
}
