"""Shared bounded final-delivery gate for recognized-but-unpublished speech.

The gate is deliberately independent of preview, provisional and translation
work.  Only a real committed segment calls :meth:`FinalDeliveryGate.note_committed`
and resets its monotonic timer.  Observing rewritten text never resets it.

Word contract
-------------
* English contractions (``don't``) and hyphenated compounds (``well-known``)
  count as one word.
* Unicode Latin, Greek, Cyrillic, Arabic, Indic and other letter runs and
  numerals count as one word each; punctuation and SentencePiece subword
  markers count as zero.  Dotted abbreviations and decimals (``Dr.``,
  ``U.S.``, ``3.14``) count as one.
* Unicode combining marks (Mn/Mc), viramas, Arabic harakat, ZWJ/ZWNJ and
  decomposed accents stay attached to their base word; mark-only text is not
  a word.  The scanner returns original-text spans, so a cut cannot truncate a
  diacritic.
* A contiguous unspaced CJK/Kana/Hangul or Thai/Lao/Khmer/Myanmar/Tibetan run
  counts as **one conservative token**, never one token per character.  No
  dictionary or neural tokenizer is used.  This deliberately underestimates
  those script word counts, so the wall-clock threshold remains the delivery
  guarantee for unspaced text.  Mixed English/CJK runs keep surrounding
  English words separate.

Time contract
-------------
The clock is an injectable ``time.monotonic`` callable, never the audio sample
clock.  The timer starts at detected speech onset (the existing VAD/input
speech indication), not at the first recognized preview.  Empty text before the
first ASR result never disarms an active speech timer.  A real committed
segment resets it while speech remains active.  Silence/noise without any
recognized text can never emit.  Start/stop/endpoint/idle reset state, and the
first voice of the next utterance arms a fresh timer.

If the engine's blocking inference delays observation, the deadline remains
expired and fires as soon as the worker can process the available result.  The
gate cannot make a hard four-second promise while inference itself blocks.
"""
from dataclasses import dataclass
from functools import lru_cache
import math
import re
import time
import unicodedata

from text_rules import (ABBREVIATIONS, CLOSERS, DANGLING, DEPENDENT, UNITS,
    complete_text, dangling_tail, joins_continuation, number_part,
    open_delimiters, plain, rule_language, sentence_boundary, sentence_spans)

# Scripts written without reliable inter-word spaces are one conservative
# token per contiguous run.  Their marks stay attached to the run.
_DENSE_RANGES=(
    (0x0E00,0x0E7F),   # Thai
    (0x0E80,0x0EFF),   # Lao
    (0x0F00,0x0FFF),   # Tibetan
    (0x1000,0x109F),   # Myanmar
    (0x1100,0x11FF),   # Hangul Jamo
    (0x1780,0x17FF),   # Khmer
    (0x3040,0x30FF),   # Hiragana / Katakana
    (0x3100,0x312F),   # Bopomofo
    (0x3130,0x318F),   # Hangul compatibility Jamo
    (0x3400,0x4DBF),   # CJK extension A
    (0x4E00,0x9FFF),   # CJK unified ideographs
    (0xA960,0xA97F),   # Hangul Jamo extended A
    (0xAC00,0xD7AF),   # Hangul syllables
    (0xD7B0,0xD7FF),   # Hangul Jamo extended B
    (0xF900,0xFAFF),   # CJK compatibility ideographs
    (0x20000,0x2FA1F), # CJK extensions B+
)
_CONNECTORS=frozenset(("'",'’','‐','‑','-'))
_JOINERS=frozenset(('‌','‍'))
COMMA = re.compile(r'[，,]')
END_MARKS = '.!?。！？؟۔।॥'
# A forced delivery may legally end at a comma clause whose continuation is a
# conjunction ("..., and then ...").  These ends still make poor word-boundary
# cuts when a nearby better boundary exists.
_CONJUNCTION_HEADS=frozenset(('and','but','or','so','yet','then','however','also'))
_WEAK_ENDS=frozenset(DANGLING)|frozenset(('then','also','however','therefore','meanwhile'))


def _weak_tail(part):
    words=part.split()
    return bool(words) and plain(words[-1]) in _WEAK_ENDS


@lru_cache(maxsize=8192)
def _char_kind(char):
    """Cheap ASCII fast path; Unicode marks/joiners are classified once."""
    if char in _JOINERS:return 'joiner'
    if char in _CONNECTORS:return 'connector'
    if char=='.':return 'dot'
    if char==',':return 'comma'
    code=ord(char)
    if code<128:
        if ('a'<=char<='z') or ('A'<=char<='Z') or ('0'<=char<='9'):return 'base'
        return 'other'
    category=unicodedata.category(char)
    if category[0] in 'LN':return 'base'
    if category[0]=='M':return 'mark'
    return 'other'


@lru_cache(maxsize=8192)
def _dense_base(char):
    code=ord(char)
    for start,end in _DENSE_RANGES:
        if start<=code<=end:return unicodedata.category(char)[0] in 'LN'
    return False


@lru_cache(maxsize=8192)
def _base_class(char):
    code=ord(char)
    if code<128:
        if ('a'<=char<='z') or ('A'<=char<='Z'):return 'letter'
        if '0'<=char<='9':return 'number'
        return ''
    category=unicodedata.category(char)[0]
    return 'letter' if category=='L' else 'number' if category=='N' else ''


@dataclass(frozen=True)
class ForcedPart:
    """A chosen prefix and the unpublished tail that must be retained."""
    text: str
    tail: str
    kind: str          # sentence | comma | word
    words: int
    tail_words: int


def normalize_wait(value=4.):
    """Accept a finite non-negative wall-clock wait, otherwise default 4 s."""
    return float(value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else 4.


def normalize_words(value=35):
    """Accept a positive integer word limit, otherwise default 35."""
    return value if type(value) is int and value>0 else 35


def normalize_enabled(value=True):
    """Only an explicit boolean enables or disables the gate."""
    return value if type(value) is bool else True


def lexical_spans(text):
    """Return ``(start,end)`` spans over the original text.

    Unicode combining marks (Mn/Mc), viramas, Arabic harakat, ZWJ/ZWNJ and
    decomposed accents stay inside their base token.  A mark on its own is not
    a word.  Dense unspaced scripts contribute one span per contiguous run.
    Spans are original offsets, so a forced cut can never truncate a diacritic.
    """
    text=text or '';spans=[];index=0;length=len(text)
    while index<length:
        char=text[index]
        if _char_kind(char)!='base':
            index+=1;continue
        dense=_dense_base(char);start=index
        first=_base_class(char);has_letter=first=='letter';has_number=first=='number'
        index+=1
        while index<length:
            char=text[index];kind=_char_kind(char)
            if kind in ('mark','joiner'):
                index+=1;continue
            if kind=='base':
                if _dense_base(char)!=dense:break
                current=_base_class(char)
                has_letter=has_letter or current=='letter'
                has_number=has_number or current=='number'
                index+=1;continue
            if kind=='connector':
                if index+1<length and _char_kind(text[index+1]) in ('base','mark','joiner'):
                    index+=1;continue
                break
            if kind=='dot':
                following=text[index+1] if index+1<length else ''
                if following:
                    following_kind=_char_kind(following)
                    if following_kind=='base':
                        if dense:break
                        current=_base_class(following)
                        if has_number and not has_letter and current=='number':
                            index+=1;continue
                        if has_letter and current=='letter':
                            index+=1;continue
                        break
                    if following_kind in ('mark','joiner'):break
                # Keep a trailing abbreviation dot with its letters; it does
                # not add a word, but the span remains visually complete.
                if not dense and has_letter:
                    index+=1;continue
                break
            if kind=='comma':
                if (index+1<length and has_number and not has_letter
                        and _char_kind(text[index+1])=='base'
                        and _base_class(text[index+1])=='number'):
                    index+=1;continue
                break
            break
        spans.append((start,index))
    return spans


def lexical_tokens(text):
    """Return actual lexical tokens; formatting and subword markers are ignored."""
    return [text[start:end] for start,end in lexical_spans(text)]


def lexical_word_count(text):
    """Count actual words, never letters, characters or ASR subword pieces."""
    return len(lexical_spans(text))


def _sentence_part(text,language):
    """First prefix ending at a valid full-sentence boundary."""
    guards=rule_language(text,language)
    for _,end in sentence_spans(text,guards):
        part=text[:end].strip();tail=text[end:].strip()
        if not part:continue
        if not sentence_boundary(part,tail,guards):continue
        return part,tail
    return None


def _forced_complete(part,language):
    """Comma delivery may accept a dependent lead followed by a real comma."""
    if complete_text(part,language):return True
    lowered=[value for token in part.split() if (value:=plain(token))]
    if not lowered or open_delimiters(part):return False
    if dangling_tail(lowered,language):return False
    if (language=='English' and lowered[0] in DEPENDENT
            and any(',' in token for token in part.split())):
        return True
    return False


def _comma_part(text,language,word_limit):
    """Last safe comma/clause boundary within the bounded word budget."""
    guards=rule_language(text,language)
    for match in reversed(list(COMMA.finditer(text))):
        start,end=match.span()
        if start>0 and end<len(text) and text[start-1].isdigit() and text[end].isdigit():
            continue  # "1,234" is a number, not a clause boundary.
        part=text[:end].strip();tail=text[end:].strip()
        if not part or not tail:continue
        words=lexical_word_count(part)
        if words<=0 or words>word_limit:continue
        if not _forced_complete(part,guards):continue
        if _weak_tail(part):continue
        last=plain(part.split()[-1])
        first=plain(tail.split()[0])
        # Keep Dr./U.S. attached to the name they introduce and never read a
        # comma between a number and its unit as a clause boundary.
        if last in ABBREVIATIONS:continue
        if number_part(last) and (number_part(first) or first in UNITS):continue
        # Deliberately do not apply the ordinary joins_continuation guard here:
        # a real comma before "and then" is exactly the requested forced clause.
        return part,tail
    return None


def _unsafe_word_end(text,start,end,next_text,language):
    """Refuse a word-boundary cut after an abbreviation, decimal or unit lead."""
    token=text[start:end]
    if plain(token) in ABBREVIATIONS:return True
    stripped=token.rstrip(CLOSERS)
    if stripped.endswith(tuple(END_MARKS)) and not sentence_boundary(
            text[:end],next_text,language):
        # Dr. / U.S. / 3.14 / 125. GeV must stay with what follows.
        return True
    return False


def _forced_join_ok(part,tail,language):
    """Forced lexical cuts may precede a conjunction; protect units/names."""
    last=plain(part.split()[-1]);first=plain(tail.split()[0])
    if number_part(last) and (number_part(first) or first in UNITS):return False
    if first in _CONJUNCTION_HEADS:return True
    return not joins_continuation(part,tail,language)


def _word_part(text,language,word_limit):
    """Bounded lexical word boundary, normally within ``word_limit`` words.

    The threshold is a trigger, not a caption-length cap: a nearby safe phrase
    slightly above it is preferred to collapsing to one word.  A small upward
    allowance keeps abbreviations/units with their following word; the hard
    pathological fallback still cuts only between lexical spans.
    """
    spans=lexical_spans(text)
    if not spans:return None
    count=len(spans)
    if count<=word_limit:
        value=text.strip()
        return (value,'') if value and lexical_word_count(value) else None
    guards=rule_language(text,language)
    target=min(word_limit,count-1)
    allowance=max(2,min(8,word_limit//3))

    def safe(index):
        cut=spans[index][0]
        part=text[:cut].strip();tail=text[cut:].strip()
        if not part or not tail:return None
        previous=spans[index-1]
        next_text=text[spans[index][0]:].strip()
        if _unsafe_word_end(text,previous[0],previous[1],next_text,guards):return None
        if not complete_text(part,guards):return None
        if _weak_tail(part):return None
        if not _forced_join_ok(part,tail,guards):return None
        return part,tail

    # Exact threshold first; then a small positive allowance for a meaningful
    # safe phrase; only then walk down but never below two words when context
    # makes a two-word phrase possible.
    value=safe(target)
    if value:return value
    for index in range(target+1,min(count-1,target+allowance)+1):
        value=safe(index)
        if value:return value
    minimum=min(2,target)
    for index in range(target-1,minimum-1,-1):
        value=safe(index)
        if value:return value
    # Pathological punctuation-free speech still needs delivery at the deadline.
    # Keep the threshold-sized prefix rather than collapsing to the first word.
    cut=spans[target][0]
    part=text[:cut].strip();tail=text[cut:].strip()
    return (part,tail) if part and tail else None


def forced_delivery(text,language='auto',word_limit=35):
    """Choose the next bounded prefix: sentence, then comma, then word boundary.

    ``text`` is pending recognized text only.  The returned tail is never
    submitted by this choice and must stay available to later polls.
    """
    word_limit=normalize_words(word_limit)
    if not text or lexical_word_count(text)<=0:return None
    sentence=_sentence_part(text,language)
    if sentence:
        part,tail=sentence
        return ForcedPart(part,tail,'sentence',lexical_word_count(part),lexical_word_count(tail))
    comma=_comma_part(text,language,word_limit)
    if comma:
        part,tail=comma
        return ForcedPart(part,tail,'comma',lexical_word_count(part),lexical_word_count(tail))
    word=_word_part(text,language,word_limit)
    if word:
        part,tail=word
        return ForcedPart(part,tail,'word',lexical_word_count(part),lexical_word_count(tail))
    return None


class FinalDeliveryGate:
    """Monotonic deadline and word threshold for pending recognized speech.

    ``trigger`` is the only state transition that can report a reason;
    ``observe`` never moves an armed deadline.  ``note_committed`` is called
    only by real committed events, never by previews or translations.
    """

    def __init__(self,wait=4.,words=35,enabled=True,clock=None):
        self.wait=normalize_wait(wait)
        self.words=normalize_words(words)
        self.enabled=normalize_enabled(enabled)
        self.clock=clock or time.monotonic
        self.armed_at=None
        self.speech_active=False
        self.pending=False
        self.pending_words=0

    def now(self):
        return float(self.clock())

    def reset(self):
        """Start/stop/endpoint/idle reset; no active speech or pending text."""
        self.armed_at=None
        self.speech_active=False
        self.pending=False
        self.pending_words=0

    def voice_started(self,now=None):
        """Arm at detected speech onset, before any recognized text exists."""
        if now is None:now=self.now()
        if not self.enabled:
            self.reset();return now
        if self.armed_at is None:self.armed_at=now
        self.speech_active=True
        return now

    def observe(self,text,now=None):
        """Track pending text without disarming an active speech timer."""
        if now is None:now=self.now()
        if not self.enabled:
            self.reset();return 0
        count=lexical_word_count(text)
        if count<=0:
            self.pending=False
            self.pending_words=0
            # Before the first ASR result the VAD may have fired and text is
            # still empty; keep the onset timer until real idle/endpoint reset.
            if not self.speech_active:self.armed_at=None
        else:
            if self.armed_at is None:self.armed_at=now
            self.pending=True
            self.pending_words=count
        return count

    def trigger(self,text,now=None):
        """Return ``'words'``/``'time'`` when strict limits are exceeded."""
        if now is None:now=self.now()
        count=self.observe(text,now)
        if not self.enabled or count<=0:return None
        if count>self.words:return 'words'
        if self.armed_at is not None and now-self.armed_at>self.wait:return 'time'
        return None

    def note_committed(self,text='',now=None):
        """A real final segment resets the clock while speech remains active."""
        if now is None:now=self.now()
        if not self.enabled:
            self.reset();return
        count=lexical_word_count(text)
        self.pending=count>0
        self.pending_words=count
        if self.speech_active:
            self.armed_at=now
        else:
            self.armed_at=now if count>0 else None
