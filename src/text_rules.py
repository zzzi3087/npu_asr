"""Cached lexical guards shared by streaming ASR; no neural text processing."""
import re
import unicodedata
from functools import lru_cache

SMALL_NUMBERS=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
TENS=dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10)))
ABBREVIATIONS=frozenset('mr mrs ms dr prof sr jr st mt vs etc e.g i.e fig figs eq eqs sec secs ref refs vol pp p cf al et no nos inc ltd co corp dept approx est ca a.m p.m u.s u.k ph.d'.split())
DANGLING=frozenset(('a an the of to for with from at by in on into onto about over under between through and or but because if when while although whether which that whose than as this these those my your his her our their would could should will can may might must not is are was were am be been being has have had do does did just very also so more less per plus minus times aged approximately roughly equals').split())
STARTERS=frozenset('i you he she it we they this that these those there here however meanwhile therefore furthermore finally now then well yes no okay ok but and so'.split())
STARTERS|=frozenset('il le la les un une nous vous ils elles ce cette cela es wir sie das der die ich dies el los las esto eso una yo él ella ellos ellas eu ele ela os as isso questo questa noi voi loro это он она они мы вы я αυτό αυτή εμείς det vi'.split())
LANGUAGE_TAILS={
    'French':frozenset('de du des le la les un une et ou avec pour dans sur aux est sont que qui au'.split()),
    'German':frozenset('ist sind der die das ein eine und oder mit von zu für dass im zum zur'.split()),
    'Spanish':frozenset('el la los las del y o que con por para de es son un una'.split()),
    'Italian':frozenset('il lo gli della delle del e o che con per di è sono un una'.split()),
    'Portuguese':frozenset('o a os as um uma de da do das dos e ou que com para por é são'.split()),
    'Russian':frozenset('и или что для из это как при на в с к по от до'.split()),
    'Ukrainian':frozenset('і або що для із це як при на в з до'.split()),
    'Greek':frozenset('και ή το η τα της του των με σε από για είναι'.split()),
    'Dutch':frozenset('een en het van voor dat met zijn de is'.split()),
    'Swedish':frozenset('och eller att är av med för det en ett'.split()),
}
LANGUAGE_TAILS['auto']=frozenset().union(*LANGUAGE_TAILS.values())
CLAUSE_STARTERS=frozenset('but however meanwhile therefore yet'.split())
DEPENDENT=frozenset('if when while although because unless whether before after until since'.split())
CONTINUATIONS=frozenset('of to for with from at by in on into onto about which whose than as that and or because if when while although whether known including excluding using based called'.split())
UNITS=frozenset('percent percentage dollars euros pounds ev kev mev gev tev pev mev/c gev/c gev/c² gev/c^2 hz khz mhz ghz kg g mg km m cm mm nm s ms ns k kelvin joules joule watts watt tesla parsecs mpc kpc'.split())
NUMBER_WORDS=frozenset(SMALL_NUMBERS)|frozenset(TENS)|frozenset('hundred thousand million billion trillion point'.split())|UNITS
SHORT_REPLIES=frozenset(('yes','no','okay','ok','thanks','thank you','good morning','good afternoon','good evening','good night',
    'of course','all right','that is right',"that's right",'i agree','i understand','i see',"let's go",'sorry','sure','hello','hi'))
CLOSERS='"\'”’)]}」』》'
STRIP='.,!?;:…。！？；：，、“”"()[]{}「」『』《》'


@lru_cache(maxsize=4096)
def plain(text):return text.casefold().strip(STRIP)


@lru_cache(maxsize=4096)
def normalized(word):
    parts=re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',word).casefold())
    if len(parts)==1:
        if parts[0] in SMALL_NUMBERS:return str(SMALL_NUMBERS[parts[0]])
        if parts[0] in TENS:return str(TENS[parts[0]])
    if len(parts)==2 and parts[0] in TENS and parts[1] in SMALL_NUMBERS and 0<SMALL_NUMBERS[parts[1]]<10:
        return str(TENS[parts[0]]+SMALL_NUMBERS[parts[1]])
    return ''.join(parts) or plain(word)


@lru_cache(maxsize=2048)
def terminal(text,next_text=''):
    clean=text.rstrip(CLOSERS)
    if not clean or clean[-1] not in '.?!。！？':return ''
    if clean.endswith(('...','…')):return ''
    if clean[-1]!='.':return clean[-1]
    bare=clean[:-1].casefold()
    if bare in ABBREVIATIONS or re.fullmatch(r'(?:[a-z]\.)+[a-z]?',bare):return ''
    if len(bare)==1 and bare.isalpha():return ''
    if re.search(r'https?://|www\.|@',clean,re.I):return ''
    if re.fullmatch(r'[\d,.]+',bare) and next_text and (plain(next_text)[:1].isdigit() or plain(next_text) in UNITS):return ''
    return '.'


@lru_cache(maxsize=2048)
def number_part(text):
    pieces=re.split(r'[\s-]+',plain(text))
    return bool(pieces) and all(p in NUMBER_WORDS or re.fullmatch(r'[\d.,%]+',p) for p in pieces)


def open_delimiters(text):
    # Quotes inside contractions are not delimiters. Closing a quote before
    # punctuation is optional, but an open parenthesis must never end a sentence.
    return any(text.count(a)>text.count(b) for a,b in (('(',')'),('[',']'),('{','}'),('（','）'),('「','」'),('“','”')))


def complete_text(text,language='English'):
    tokens=text.split()
    if not tokens:return False
    lowered=[value for t in tokens if (value:=plain(t))]
    while lowered and lowered[-1] in {'uh','um','er','erm','hmm'}:lowered.pop()
    if not lowered:return False
    if lowered[-1] in DANGLING or lowered[-1] in LANGUAGE_TAILS.get(language,()) or open_delimiters(text):return False
    if re.search(r'(?:因为|由于|如果|虽然|以及|或者|但是|关于|对于|通过|大约|约为|等于|例如|包括|的|和|与|在|是|把|被)[，、。！？.!?]*$',text):return False
    if lowered[0] in DEPENDENT and not any(',' in t or ';' in t for t in tokens[:-1]):return False
    return True


def joins_continuation(left,right,language='English'):
    """Protect units, dependent phrases and title-cased multiword names."""
    if not left or not right:return False
    a,b=left.split()[-1],right.split()[0]
    if plain(a) in DANGLING or plain(a) in LANGUAGE_TAILS.get(language,()) or plain(b) in CONTINUATIONS:return True
    if number_part(a) and (number_part(b) or plain(b) in UNITS):return True
    if language in ('English','auto') and a[:1].isupper() and b[:1].isupper() and plain(b) not in STARTERS:return True
    return False


def sentence_spans(text,language='English'):
    """Unicode punctuation spans without splitting decimals, abbreviations or names.

    Returns (start,end) offsets into the original text, preserving exact spacing.
    The last span may be unfinished; its caller decides when to publish it.
    """
    start=0
    for match in re.finditer(r'[.!?。！？]+[\"\'”’）)\]}」』》]*',text):
        end=match.end()
        if end<len(text) and not text[end].isspace() and match.group()[0] in '.!?':continue
        part=text[start:end].strip();right=text[end:].lstrip()
        if not part:continue
        token=part.split()[-1];next_token=right.split(maxsplit=1)[0] if right else ''
        mark=terminal(token,next_token)
        if not mark or not complete_text(part,language):continue
        if mark=='.' and right:
            if joins_continuation(part,right,language):continue
            if right[:1].islower() and plain(next_token) not in STARTERS:continue
        yield start,end;start=end
    if text[start:].strip():yield start,len(text)


def join_text(left,right):
    if not left:return right
    if not right:return left
    cjk=lambda c:'\u3400'<=c<='\u9fff' or '\u3040'<=c<='\u30ff'
    return left+('' if cjk(left[-1]) and (cjk(right[0]) or right[0] in '，。！？；：') else ' ')+right
