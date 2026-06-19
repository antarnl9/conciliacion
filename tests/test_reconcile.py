"""Prueba la lógica de conciliación (estatus + reglas de cobro) en DuckDB en memoria."""
import datetime as dt
from pathlib import Path

import duckdb
import pytest

from conciliacion import reconcile

SCHEMA = Path(__file__).resolve().parents[1] / "conciliacion" / "schema.sql"
NCOLS = 24  # facturas_carrier


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute(SCHEMA.read_text())
    return c


def _carrier(guia, neto, kilos=1, zona=None, es_retorno=False, referencia=None):
    d, fd = dt.date(2026, 5, 5), dt.date(2026, 5, 28)
    return ('dhl', guia, None, referencia, 'N', None, None, 1, kilos, d, fd, None,
            0, 0, 0, 0, 0, neto, 'MXN', 'rem', 'dst', es_retorno, 't', zona)


def test_reglas_de_cobro(con):
    ts = dt.datetime(2026, 5, 5)
    con.executemany('INSERT INTO facturas_carrier VALUES (' + ','.join(['?'] * NCOLS) + ')', [
        _carrier('PREP_OK', 100), _carrier('PREP_REPESO', 200),
        _carrier('INTERNO', 100),
        _carrier('CRED_AUTO', 100),
        _carrier('CRED_MANUAL', 100, kilos=2, zona='1'),
        _carrier('CRED_MAN_SINTAR', 100, kilos=2, zona='9'),
        _carrier('SIN_SIS', 80),
        _carrier('RET1', 50, es_retorno=True, referencia='RT1234567890'),  # retorno de '1234567890'
    ])
    con.executemany('INSERT INTO ch_shipments VALUES (?,?,?,?,?,?)', [
        ('PREP_OK', 1, 'A', 'Prepago', 115.0, ts),
        ('PREP_REPESO', 2, 'B', 'Prepago', 60.0, ts),
        ('INTERNO', 3, 'SN00449', 'Prepago sin saldo', None, ts),
        ('CRED_AUTO', 4, 'CtoAuto', 'Prepago sin saldo', 130.0, ts),
        ('CRED_MANUAL', 5, 'CtoManual', 'Prepago sin saldo', 999.0, ts),
        ('CRED_MAN_SINTAR', 5, 'CtoManual', 'Prepago sin saldo', 999.0, ts),
        ('1234567890', 9, 'ClienteRetorno', 'Prepago', 200.0, ts),  # guía original del retorno
    ])
    con.execute("INSERT INTO ch_sobrepeso VALUES ('PREP_REPESO', 30.0)")
    # CtoManual = cobro manual; tarifa zona 1, 1-5kg -> $150 (config_credito: 7 columnas)
    con.execute("INSERT INTO config_credito VALUES (5,'CtoManual','manual',NULL,NULL,NULL,NULL)")
    con.execute("INSERT INTO tarifas VALUES (5,'CtoManual','dhl','1',1,5,150,NULL,NULL)")

    reconcile.build(con)
    r = dict(con.execute("SELECT guia, estatus FROM reconciliacion").fetchall())
    assert r['PREP_OK'] == 'Cobrado OK'
    assert r['PREP_REPESO'] == 'Extra por cobrar'      # re-peso: extra a costo+margen
    assert r['INTERNO'] == 'Interno (14%)'
    assert r['CRED_AUTO'] == 'Cobrado OK'              # credito automatica (130>=100*1.14)
    assert r['CRED_MANUAL'] == 'Cobrado OK'            # credito flat -> tarifa $150 + IVA
    assert r['CRED_MAN_SINTAR'] == 'Falta cobrar (credito)'  # flat sin tarifa para zona 9
    assert r['SIN_SIS'] == 'Sin guia en sistema'
    assert r['RET1'] == 'Extra por cobrar'             # retorno -> se cobra completo a costo+margen

    g = lambda q: con.execute(q).fetchone()[0]
    # flat: tarifa 150 + IVA 16% = 174
    assert abs(g("SELECT ingreso FROM reconciliacion WHERE guia='CRED_MANUAL'") - 174) < .01
    assert abs(g("SELECT margen FROM reconciliacion WHERE guia='INTERNO'") - 14) < .01
    # re-peso: ingreso sube a piso costo*1.14 = 228, margen = 28; extra = 228 - (60+30) = 138
    assert abs(g("SELECT ingreso FROM reconciliacion WHERE guia='PREP_REPESO'") - 228) < .01
    assert abs(g("SELECT extra FROM reconciliacion WHERE guia='PREP_REPESO'") - 138) < .01
    # retorno atribuido a su cliente real, cobrado a costo+margen (50*1.14=57)
    assert g("SELECT cliente_real FROM reconciliacion WHERE guia='RET1'") == 'ClienteRetorno'
    assert abs(g("SELECT extra FROM reconciliacion WHERE guia='RET1'") - 57) < .01
