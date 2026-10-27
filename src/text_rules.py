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
# Never union language-specific exclusions in automatic mode: German verbs can
# end subordinate clauses, and words such as "die", "as" and "in" overlap.
LANGUAGE_HINTS={
    'English':'the this that these those we you they which what is are was were and with of to it how would should because',
    'German':'der die das dass ich wir nicht eine ein einer einem einen den dem des ist sind und bei jetzt hier werden wird gibt muss sondern wie',
    'French':'le la les une un des du nous vous ils elles est sont avec que qui dans sur pour ce cette pas ne',
    'Spanish':'el la los las un una del que con por para es son esto eso nosotros ellos está pero ahora',
    'Italian':'il lo gli della delle del noi voi loro sono è che per questo questa non ma quindi',
    'Portuguese':'os as um uma da do das dos eu ele ela são é com isso não mas então',
    'Dutch':'het een dat zijn dit ik wij we is de die niet wordt worden deze maar ook naar',
    'Swedish':'och eller att är av med för det en ett jag inte vi den här',
}
HINT_INDEX={}
for _language,_words in LANGUAGE_HINTS.items():
    for _word in _words.split():HINT_INDEX.setdefault(_word,[]).append(_language)
NAME_PREFIXES=frozenset('national international united european royal new san saint mount lake large'.split())
VERB_FINAL={
    'German':frozenset('dass weil obwohl wenn ob wie was damit bevor nachdem während falls sodass'.split()),
    'Dutch':frozenset('dat omdat hoewel als of hoe wat'.split()),
}
ENGLISH_SUBJECTS=frozenset('i you he she it we they this that these those there'.split())
ENGLISH_WH=frozenset('what who whom where which how'.split())
ENGLISH_FINITE=frozenset('is are was were has have had can could will would shall should may might must does do did'.split())
END_MARKS='.?!。！？؟۔।॥'
CLAUSE_STARTERS=frozenset('but however meanwhile therefore yet'.split())
DEPENDENT=frozenset('if when while although because unless whether before after until since'.split())
CONTINUATIONS=frozenset('of to for with from at by in on into onto about which whose than as that and or because if when while although whether known including excluding using based called'.split())
UNITS=frozenset('percent percentage dollars euros pounds ev kev mev gev tev pev mev/c gev/c gev/c² gev/c^2 hz khz mhz ghz kg g mg km m cm mm nm s ms ns k kelvin joules joule watts watt tesla parsecs mpc kpc'.split())
NUMBER_WORDS=frozenset(SMALL_NUMBERS)|frozenset(TENS)|frozenset('hundred thousand million billion trillion point'.split())|UNITS
SHORT_REPLIES=frozenset(('yes','no','okay','ok','thanks','thank you','good morning','good afternoon','good evening','good night',
    'of course','all right','that is right',"that's right",'i agree','i understand','i see',"let's go",'sorry','sure','hello','hi'))
CLOSERS='"\'”’)]}」』》»›'
STRIP='.,!?;:…。！？；：，、“”"()[]{}「」『』《》«»‹›؟۔।॥;'


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
def terminal(text,next_text='',language='auto'):
    clean=text.rstrip(CLOSERS)
    if not clean:return ''
    if clean[-1] in ';;' and (language=='Greek' or re.search(r'[\u0370-\u03ff]',clean)):return '?'
    if clean[-1] not in END_MARKS:return ''
    if clean.endswith(('...','…')):return ''
    if clean[-1]!='.':return clean[-1]
    bare=clean[:-1].casefold()
    if bare in ABBREVIATIONS or bare in {'bzw','usw','abb','nr','z.b','u.a','d.h','sra','srta','mme','mlle','p.ej','т.е','т.д','рис'} or re.fullmatch(r'(?:[^\W\d_]\.)+[^\W\d_]?',bare):return ''
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


@lru_cache(maxsize=2048)
def rule_language(text,language='auto'):
    """Select conservative lexical guards, not an ASR language prediction.

    No model, I/O or background work. Ambiguous phrases use generic punctuation
    rather than applying every language's forbidden-word list simultaneously.
    """
    if language not in ('auto',None):return language
    if re.search(r'[\u3040-\u30ff]',text):return 'Japanese'
    if re.search(r'[\u3400-\u9fff]',text):return 'Chinese'
    if re.search(r'[\u0370-\u03ff]',text):return 'Greek'
    if re.search(r'[\u0400-\u04ff]',text):return 'Ukrainian' if re.search('[іїєґІЇЄҐ]',text) else 'Russian'
    if re.search(r'[\u0600-\u06ff\u0900-\u097f\uac00-\ud7af]',text):return 'generic'
    tokens=text.split();tokens=tokens if len(tokens)<=48 else tokens[:24]+tokens[-24:]
    scores={}
    for token in tokens:
        choices=HINT_INDEX.get(plain(token),())
        for candidate in choices:scores[candidate]=scores.get(candidate,0)+(2 if len(choices)==1 else 1)
    ranked=sorted(scores.items(),key=lambda item:item[1],reverse=True)
    if ranked and ranked[0][1]>=3 and (len(ranked)==1 or ranked[0][1]-ranked[1][1]>=2):return ranked[0][0]
    return 'generic'


def dangling_tail(tokens,language):
    last=tokens[-1]
    tails=DANGLING if language=='English' else LANGUAGE_TAILS.get(language,())
    if last not in tails:return False
    if language=='English':
        if tuple(tokens[-2:]) in {('so','on'),('and','on')} or tuple(tokens[-3:])==('and','so','forth'):return False
        if last in {'this','that','these','those'} and len(tokens)>=4 and tokens[-2] in {'show','do','check','see','explain','understand','know','use','measure'}:return False
    # Verb-final subordinate clauses are complete, unlike "Die Probe ist ...".
    if language in VERB_FINAL and last in {'ist','sind','is','zijn'}:
        if len(tokens)>=5 and any(word in VERB_FINAL[language] for word in tokens[:-2]):return False
    # Stranded prepositions/auxiliaries in questions and embedded wh-clauses.
    if language=='English' and last in {'is','are','was','were','be','for','to','from','with','about','at','in','on','of'}:
        if len(tokens)>=4 and any(word in ENGLISH_WH for word in tokens[:-2]):return False
    return True


@lru_cache(maxsize=2048)
def complete_text(text,language='English'):
    tokens=text.split()
    if not tokens:return False
    lowered=[value for t in tokens if (value:=plain(t))]
    while lowered and lowered[-1] in {'uh','um','er','erm','hmm'}:lowered.pop()
    if not lowered:return False
    language=rule_language(text,language)
    if dangling_tail(lowered,language) or open_delimiters(text):return False
    if re.search(r'(?:因为|由于|如果|虽然|以及|或者|但是|关于|对于|通过|大约|约为|等于|例如|包括|的|和|与|在|是|把|被)[，、。！？.!?]*$',text):return False
    if language=='English' and lowered[0] in DEPENDENT and not any(',' in t or ';' in t for t in tokens[:-1]):return False
    return True


def independent_english_start(right):
    """Small bounded clause check; no parser and no capitalized-noun heuristic."""
    words=right.split()[:10]
    if len(words)<3 or not words[0][:1].isupper():return False
    keys=[plain(w) for w in words]
    # Demonstrative subject, not "that we ..." introducing a relative clause.
    if keys[0] in {'that','this','these','those'}:return keys[1] in ENGLISH_FINITE
    start=1 if keys[0] in {'and','but','so','yet','however'} else 0
    for i in range(start,min(len(keys)-1,8)):
        subject=keys[i] in ENGLISH_SUBJECTS or keys[i] in {'the','a','an'}
        if subject and any(w in ENGLISH_FINITE for w in keys[i+1:i+5]):return True
    return False


def joins_continuation(left,right,language='English'):
    """Protect units, dependent phrases and title-cased multiword names."""
    if not left or not right:return False
    a,b=left.split()[-1],right.split()[0]
    language=rule_language(left+' '+right,language)
    tokens=[plain(t) for t in left.split() if plain(t)]
    if tokens and dangling_tail(tokens,language):return True
    if language=='English' and plain(b) in CONTINUATIONS:
        # "With care" may continue a phrase; "In this trial, we ..." starts a
        # new clause. Inspect bounded right context instead of one word alone.
        following=right.split()[:8]
        independent=(independent_english_start(right) or
            (plain(b) not in {'that','which','whose','because','if','when','while','although','whether'}
                and b[:1].isupper() and len(following)>=4 and any(plain(t) in ENGLISH_SUBJECTS for t in following[1:])))
        if not independent:return True
    if number_part(a) and (number_part(b) or plain(b) in UNITS):return True
    if language=='English' and plain(a) in NAME_PREFIXES and a[:1].isupper() and b[:1].isupper() and plain(b) not in STARTERS:return True
    return False


def sentence_boundary(left,right='',language='English'):
    """One boundary policy for timed and text-only models."""
    language=rule_language(left+' '+right,language)
    token=left.split()[-1] if left.split() else ''
    following=right.split(maxsplit=1)[0] if right else ''
    mark=terminal(token,following,language)
    if not mark:return ''
    if not complete_text(left,language):
        # Spoken explanations can start with "because". A clearly independent
        # next sentence must not keep that completed explanation in preview.
        parts=left.split(maxsplit=1)
        explanation=(language=='English' and len(parts)==2 and plain(parts[0])=='because'
            and independent_english_start(right) and complete_text(parts[1],language))
        if not explanation:return ''
    if mark=='.' and right and joins_continuation(left,right,language):return ''
    return mark


def sentence_spans(text,language='English'):
    """Unicode punctuation spans without splitting decimals, abbreviations or names.

    Returns (start,end) offsets into the original text, preserving exact spacing.
    The last span may be unfinished; its caller decides when to publish it.
    """
    start=0;language=rule_language(text,language)
    for match in re.finditer(r'[.!?。！？؟۔।॥;;]+[\"\'”’）)\]}」』》»›]*',text):
        end=match.end()
        if end<len(text) and not text[end].isspace() and match.group()[0] in '.!?':continue
        part=text[start:end].strip();right=text[end:].lstrip()
        if not part:continue
        if not sentence_boundary(part,right,language):continue
        yield start,end;start=end
    if text[start:].strip():yield start,len(text)


def join_text(left,right):
    if not left:return right
    if not right:return left
    cjk=lambda c:'\u3400'<=c<='\u9fff' or '\u3040'<=c<='\u30ff'
    return left+('' if cjk(left[-1]) and (cjk(right[0]) or right[0] in '，。！？；：') else ' ')+right
