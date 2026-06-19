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


def _carrier(guia, neto, kilos=1, zona=None):
    d, fd = dt.date(2026, 5, 5), dt.date(2026, 5, 28)
    return ('dhl', guia, None, None, 'N', None, None, 1, kilos, d, fd, None,
            0, 0, 0, 0, 0, neto, 'MXN', 'rem', 'dst', False, 't', zona)


def test_reglas_de_cobro(con):
    ts = dt.datetime(2026, 5, 5)
    con.executemany('INSERT INTO facturas_carrier VALUES (' + ','.join(['?'] * NCOLS) + ')', [
        _carrier('PREP_OK', 100), _carrier('PREP_REPESO', 200),
        _carrier('INTERNO', 100),
        _carrier('CRED_AUTO', 100),
        _carrier('CRED_MANUAL', 100, kilos=2, zona='1'),
        _carrier('CRED_MAN_SINTAR', 100, kilos=2, zona='9'),
        _carrier('SIN_SIS', 80),
    ])
    con.executemany('INSERT INTO ch_shipments VALUES (?,?,?,?,?,?)', [
        ('PREP_OK', 1, 'A', 'Prepago', 115.0, ts),
        ('PREP_REPESO', 2, 'B', 'Prepago', 60.0, ts),
        ('INTERNO', 3, 'SN00449', 'Prepago sin saldo', None, ts),
        ('CRED_AUTO', 4, 'CtoAuto', 'Prepago sin saldo', 130.0, ts),
        ('CRED_MANUAL', 5, 'CtoManual', 'Prepago sin saldo', 999.0, ts),
        ('CRED_MAN_SINTAR', 5, 'CtoManual', 'Prepago sin saldo', 999.0, ts),
    ])
    con.execute("INSERT INTO ch_sobrepeso VALUES ('PREP_REPESO', 30.0)")
    # CtoManual = cobro manual; tarifa zona 1, 1-5kg -> $150
    con.execute("INSERT INTO config_credito VALUES (5,'CtoManual','manual',NULL,NULL)")
    con.execute("INSERT INTO tarifas VALUES (5,'CtoManual','dhl','1',1,5,150,NULL,NULL)")

    reconcile.build(con)
    r = dict(con.execute("SELECT guia, estatus FROM reconciliacion").fetchall())
    assert r['PREP_OK'] == 'Cobrado OK'
    assert r['PREP_REPESO'] == 'Sobrepeso pendiente'
    assert r['INTERNO'] == 'Interno (14%)'
    assert r['CRED_AUTO'] == 'Cobrado OK'              # credito automatica -> ClickHouse (130>=100)
    assert r['CRED_MANUAL'] == 'Cobrado OK'            # credito manual -> tarifa $150 >= 100
    assert r['CRED_MAN_SINTAR'] == 'Falta cobrar (credito)'  # manual sin tarifa para zona 9
    assert r['SIN_SIS'] == 'Sin guia en sistema'

    g = lambda q: con.execute(q).fetchone()[0]
    assert abs(g("SELECT ingreso FROM reconciliacion WHERE guia='CRED_MANUAL'") - 150) < .01
    assert abs(g("SELECT margen FROM reconciliacion WHERE guia='INTERNO'") - 14) < .01
    assert abs(g("SELECT margen FROM reconciliacion WHERE guia='PREP_REPESO'") - (-110)) < .01
