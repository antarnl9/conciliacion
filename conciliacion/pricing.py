"""Motor de precio del cliente (genera el SQL del cálculo).

precio_cliente por guía, según el método configurado:
  - flat          -> precio fijo de `tarifas` (zona×kilo) + IVA
  - margen_global -> costo × (1+fuel) × (1+margen_cliente) × (1+IVA)
  - margen_zona   -> costo × (1+fuel) × (1+margen_de_la_zona) × (1+IVA)
  - margen_kilo   -> costo × (1+fuel) × (1+margen_del_rango_kilo) × (1+IVA)

`costo` sale del rate card de la paquetería (`costos_tarifa`, vigente a la fecha);
`fuel` del combustible vigente del periodo (`combustible`). `flat` no lleva combustible.
"""
from __future__ import annotations

from . import config

_D0 = "DATE '1900-01-01'"
_D1 = "DATE '2999-01-01'"


def precio_sql(*, carrier: str, zona: str, kilo: str, fecha: str,
               seller: str, metodo: str, margen: str, iva: float | None = None) -> str:
    """Devuelve una expresión SQL que evalúa el precio del cliente.
    Cada argumento (salvo iva) es una expresión SQL: una columna (ej. 'base.zona') o literal."""
    iva = config.IVA_DEFAULT if iva is None else iva

    def vig(a, b):
        return f"({fecha} BETWEEN COALESCE({a},{_D0}) AND COALESCE({b},{_D1}))"

    base = (f"(SELECT ct.costo FROM costos_tarifa ct WHERE ct.carrier={carrier} AND ct.zona={zona} "
            f"AND {kilo} >= COALESCE(ct.peso_min,-1e12) AND {kilo} < COALESCE(ct.peso_max,1e12) "
            f"AND {vig('ct.vigencia_desde','ct.vigencia_hasta')} ORDER BY ct.peso_min LIMIT 1)")
    fuel = (f"COALESCE((SELECT cb.pct FROM combustible cb WHERE cb.carrier={carrier} "
            f"AND {vig('cb.vigencia_desde','cb.vigencia_hasta')} ORDER BY cb.vigencia_desde DESC LIMIT 1),0)")
    mz = (f"COALESCE((SELECT mz.margen FROM margen_zona mz WHERE mz.seller_id={seller} AND mz.zona={zona} LIMIT 1),0)")
    mk = (f"COALESCE((SELECT mk.margen FROM margen_kilo mk WHERE mk.seller_id={seller} "
          f"AND {kilo} >= COALESCE(mk.peso_min,-1e12) AND {kilo} < COALESCE(mk.peso_max,1e12) "
          f"ORDER BY mk.peso_min LIMIT 1),0)")
    flat = (f"(SELECT t.precio FROM tarifas t WHERE t.seller_id={seller} AND t.carrier={carrier} AND t.zona={zona} "
            f"AND {kilo} >= COALESCE(t.peso_min,-1e12) AND {kilo} < COALESCE(t.peso_max,1e12) "
            f"AND {vig('t.vigencia_desde','t.vigencia_hasta')} ORDER BY t.peso_min LIMIT 1)")

    margen_case = (f"CASE {metodo} WHEN 'margen_global' THEN COALESCE({margen},0) "
                   f"WHEN 'margen_zona' THEN {mz} WHEN 'margen_kilo' THEN {mk} ELSE 0 END")

    return (f"CASE WHEN {metodo}='flat' THEN {flat} * (1+{iva}) "
            f"WHEN {metodo} IN ('margen_global','margen_zona','margen_kilo') "
            f"THEN {base}*(1+{fuel})*(1+{margen_case})*(1+{iva}) ELSE NULL END")
