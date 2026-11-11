"""Conservative numeric and attached-unit fingerprints for translated cues.

No floating point conversion: scientific notation and decimal forms compare
exactly, while SI prefix case remains significant. Inputs are bounded before
Decimal hashing, which otherwise permits enormous exponent work.
"""
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

_SUPERSCRIPTS = '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻'
_TO_ASCII = str.maketrans(_SUPERSCRIPTS, '0123456789+-')
_ALIASES = {'公斤':'kg','千克':'kg','克':'g','毫克':'mg','千米':'km','公里':'km',
            '厘米':'cm','毫米':'mm','米':'m','秒':'s','分钟':'min','小时':'h',
            '赫兹':'Hz','千赫兹':'kHz','兆赫兹':'MHz','开尔文':'K',
            '摄氏度':'°C','毫升':'mL','升':'L','帕斯卡':'Pa','千帕':'kPa',
            '电子伏特':'eV','兆电子伏特':'MeV','吉电子伏特':'GeV'}
_UNIT = (r'(?:'+ '|'.join(sorted(map(re.escape,_ALIASES),key=len,reverse=True)) +
         r'|[fpnumµmkMGT]?eV(?:/c\^?2)?|[kMGT]?Hz|km/s|km/h|m/s|'
         r'°[CF]|[cmkµn]?m|[mkµ]?g|[mhkM]?Pa|atm|mL|L|min|h|s|K|%|‰)')
_UNIT = '(?:'+_UNIT+r')(?:\^[+\-]?\d{1,2}(?!\d))?'
_NUMBER = r'[+\-]?(?:\d+(?:[.,]\d+)?|[.]\d+)(?:[eE][+\-]?\d+)?'
_MEASUREMENT = re.compile(
    r'(?<![a-zA-Z0-9_.])(?P<op><=|>=|[<>≤≥±≈~])?\s*(?P<number>'+_NUMBER+
    r')(?:\s*[×x·*]\s*10\s*\^\s*(?P<exponent>[+\-]?\d+))?'
    r'(?:\s*(?P<unit>'+_UNIT+r')(?![a-zA-Z]))?')
_INCOMPLETE_EXPONENT = re.compile(r'[eE](?:[+\-]|\d|$)')
_FRACTIONAL_EXPONENT = re.compile(r'(?<![A-Za-z0-9_.])'+_NUMBER+r'\s*[×x·*]\s*10\s*\^\s*[+\-]?\d+[.,]\d+|(?<![A-Za-z0-9_.])10\s*\^\s*[+\-]?\d+[.,]\d+')
_BOUND = re.compile(r'(at most|at least|less than|more than|不超过|至多|至少|小于|大于)\s*$', re.I)
_OPS = {'<=':'≤', '>=':'≥', '~':'≈', 'at most':'≤', '至多':'≤', '不超过':'≤',
        'at least':'≥', '至少':'≥', 'less than':'<', '小于':'<', 'more than':'>', '大于':'>'}
_SHARED_UNIT = re.compile(r'\s*(?:±|to|至|到|[–—-])\s*$',re.I)
_MAX_DIGITS=512
_MAX_EXPONENT=10000


def _decimal(raw, exponent=None):
    if len(raw)>_MAX_DIGITS:return None
    try:
        number=Decimal(raw.replace(',','.'))
        sign,digits,power=number.as_tuple()
        if exponent is not None:
            if len(exponent.lstrip('+-'))>5:return None
            power+=int(exponent)
        if abs(power)>_MAX_EXPONENT:return None
        return Decimal((sign,digits,power))
    except (InvalidOperation,ValueError,OverflowError):
        return None


def quantities(text):
    text=str(text).replace('−','-').replace('μ','µ').replace('＋','+').replace('+/-','±')
    text=re.sub('['+_SUPERSCRIPTS+']+',lambda m:'^'+m[0].translate(_TO_ASCII),text)
    text=_FRACTIONAL_EXPONENT.sub(lambda m:' '*len(m[0]),text)
    text=re.sub(r'([×x·*]\s*)?(?<![A-Za-z0-9.])10\^([+\-]?\d+)',
                lambda m:m[0] if m[1] else '1e'+m[2],text)
    numbers,pairs=Counter(),Counter()
    parsed=[]
    for match in _MEASUREMENT.finditer(text):
        # Never salvage a prefix of an incomplete exponent or a numbered name.
        if match['exponent'] is None and _INCOMPLETE_EXPONENT.match(text,match.end('number')):continue
        number=_decimal(match['number'],match['exponent'])
        if number is None:continue
        op=match['op'] or ''
        if not op:
            boundary=_BOUND.search(text[max(0,match.start()-20):match.start()])
            if boundary:op=boundary[0].strip().lower()
        unit=match['unit']
        if unit:unit=_ALIASES.get(unit,unit).replace('^','')
        parsed.append([match,number,_OPS.get(op,op),unit])
    # A trailing unit scopes an uncertainty or range: 5 ± 0.2 kg, 3–5 kg.
    # A unit already attached to the first number always wins.
    for i in range(len(parsed)-1):
        left,right=parsed[i],parsed[i+1]
        bridge=text[left[0].end():right[0].start('number')]
        # The right regex may have consumed ± as its operator.
        if not left[3] and right[3] and _SHARED_UNIT.fullmatch(bridge):left[3]=right[3]
        # ASCII '-' is normally consumed as the second number's sign.
        if not left[3] and right[3] and not bridge.strip() and right[0]['number'].startswith('-'):
            left[3]=right[3]
            if left[0].end()==right[0].start('number'):
                right[1]=abs(right[1])
    for match,number,op,unit in parsed:
        numbers[(op,number)]+=1
        if unit:pairs[(number,unit)]+=1
    return numbers,pairs
