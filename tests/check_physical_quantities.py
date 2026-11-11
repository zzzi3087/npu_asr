"""Exact and bounded scientific quantity fingerprints."""
import sys,time
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from physical_quantities import quantities

def same(a,b):assert quantities(a)==quantities(b),(a,b,quantities(a),quantities(b))
def different(a,b):assert quantities(a)!=quantities(b),(a,b)
same('1.2 × 10⁻³ GeV','0.0012 GeV')
same('−1.2 × 10^-3 GeV','-0.0012 GeV')
same('1.25e+2 kg','125 公斤')
same('10⁻³ s','0.001 秒')
same('at most 2 kg','≤2 千克')
same('at least 2 kg','>=2 kg')
same('less than 2 kg','小于2公斤')
same('5 ± 0.2 kg','5 kg ± 0.2 kg')
same('5 +/- 0.2 kg','5 kg ± 0.2 kg')
same('3–5 kg','3 kg–5 kg')
same('3-5 kg','3 kg–5 kg')
same('3 to 5 kg','3 kg 至5 kg')
same('5 GeV/c²','5 GeV/c^2')
same('4 m²','4 m^2')
same('1,25 kg','1.25 kg')
different('5 MeV','5 meV')
different('2 kg and 3 m','2 m and 3 kg')
different('5 ± 0.2 kg','5 kg ± 0.2 m')
different('≤ 2 kg','≥ 2 kg')
different('3–5 kg','3–6 kg')
nums,pairs=quantities('5 ± 0.2 kg')
assert pairs[(Decimal('5'),'kg')]==pairs[(Decimal('.2'),'kg')]==1
assert quantities('XENON1T and H2O')==({}, {})
for text in ['1e99999999999999999 kg','1e-99999999999999999 kg','1 × 10^99999999999 kg','9'*10000+' kg','1e+ kg','2 × 10^-3.5 kg','10^-3.5 kg']:
    started=time.monotonic();result=quantities(text)
    assert time.monotonic()-started<2
    assert not result[0],(text[:40],result)
print('physical quantity checks passed')
