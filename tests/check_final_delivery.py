"""Synthetic final-delivery gate contracts; no API, microphone, model or GUI.

Fake clocks make the wall-clock contract deterministic. Fake engines count
every inference call so a gate flush can be proven not to add ASR work.
"""
import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

os.environ['PARAKEET_DATA_DIR'] = tempfile.mkdtemp(prefix='parakeet-final-delivery-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import numpy as np
from final_delivery import (FinalDeliveryGate, forced_delivery, lexical_spans,
    lexical_word_count, lexical_tokens)
from progressive_captions import DraftCaptions, StablePrefix
import qwen_streaming
import streaming

BLOCK = 512
RATE = 16000


class Clock:
    def __init__(self): self.value = 0.

    def __call__(self): return self.value

    def set(self, value): self.value = value


class FakeVad:
    def __call__(self, block): return .99 if block[0] else 0.


streaming.Vad = FakeVad
qwen_streaming.Vad = FakeVad


def timed_result(text, audio_seconds=2.):
    words = text.split()
    step = min(.2, max(.002, audio_seconds / max(1, len(words))))
    tokens = [(word, .05 + index * step) for index, word in enumerate(words)]
    return dict(text=text, words=tokens,
                word_spans=[(word, stamp, stamp + step * .8) for word, stamp in tokens],
                language='English', infer_ms=1, cpu_ms=0)


class TimedEngine:
    model_id = 'parakeet-v3'
    language = 'English'
    max_samples = RATE * 30

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def infer(self, audio):
        self.calls += 1
        return timed_result(self.text, len(audio) / RATE)


class SequenceEngine:
    model_id = 'qwen3-asr-1.7b'
    language = 'English'
    streaming_mode = 'utterance'
    max_samples = RATE * 17

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def infer(self, audio):
        self.calls += 1
        text = self.texts[min(self.calls - 1, len(self.texts) - 1)]
        return timed_result(text, len(audio) / RATE)


def feed(recognizer, seconds, speech=True):
    block = np.full(BLOCK, .01 if speech else 0., np.float32)
    for _ in range(max(1, round(seconds * RATE / BLOCK))):
        recognizer.push(block)
        recognizer.poll()


def feed_clocked(recognizer, seconds, clock, start=0.):
    """Causal non-blocking cadence: wall clock advances with captured audio."""
    step = BLOCK / RATE
    for index in range(max(1, round(seconds / step))):
        clock.set(start + index * step)
        recognizer.push(np.full(BLOCK, .01, np.float32))
        clock.set(start + (index + 1) * step)
        recognizer.poll()
    return start + max(1, round(seconds / step)) * step


def first_commit(recognizer, clock, emit):
    return next((value, wall) for kind, value, wall in emit if kind == 'committed')


def committed(events): return [value for kind, value in events if kind == 'committed']


def test_word_contract():
    assert lexical_word_count("don't well-known") == 2
    assert lexical_word_count("Dr. U.S. e.g. 3.14 1,000") == 5
    assert lexical_word_count('3.14 125 GeV') == 3
    assert lexical_word_count('!!! ... ,,,') == 0
    assert lexical_word_count('verylongword') == 1
    assert lexical_word_count('a' * 200) == 1
    assert lexical_word_count('▁hello ▁world') == 2
    # Unicode letters count; unspaced CJK is one conservative token per run,
    # never one per character, while adjacent English stays separate.
    assert lexical_word_count('αβγ θεωρία') == 2
    assert lexical_word_count('материя теория') == 2
    assert lexical_word_count('我们使用English') == 2
    assert lexical_word_count('我们 use English') == 3
    assert lexical_word_count('一二三四五六七八九十') == 1
    assert lexical_tokens('你好，world!') == ['你好', 'world']


def test_unicode_marks_and_dense_scripts():
    # Devanagari vowel signs and viramas attach to their base letters.
    assert lexical_word_count('हिन्दी भाषा') == 2
    assert lexical_word_count(' '.join(['हिन्दी'] * 14)) == 14
    assert lexical_word_count(' '.join(['हिन्दी'] * 35)) == 35
    assert lexical_word_count(' '.join(['हिन्दी'] * 36)) == 36
    # Arabic harakat and Indic ZWJ/ZWNJ stay inside one lexical word.
    assert lexical_word_count('مَرْحَبًا') == 1
    assert lexical_word_count('می‌خواهم') == 1
    assert lexical_word_count('क्‍ष') == 1
    # A Thai unspaced run is one conservative token, not one per mark/letter.
    assert lexical_word_count('วันนี้อากาศดีมาก') == 1
    # NFD Latin accents count once, with spans ending after the mark sequence.
    nfd = 'énergie'
    assert lexical_word_count(nfd) == 1
    assert lexical_word_count(' '.join([nfd] * 35)) == 35
    assert lexical_word_count(' '.join([nfd] * 36)) == 36
    spans = lexical_spans(' '.join([nfd] * 3))
    assert spans[0][1] > spans[0][0] + 1
    # Mixed scripts retain separate English, Indic, CJK and Thai words.
    assert lexical_word_count('hello हिन्दी 世界 วันนี้') == 4
    # Mark/punctuation-only text is never a word.
    assert lexical_word_count(' ।। ्́ ') == 0
    # A forced cut never leaves an orphan combining mark at the tail head.
    long_nfd = ' '.join([nfd] * 40)
    part = forced_delivery(long_nfd, 'English', 35)
    assert part.words == 35
    assert part.tail and not unicodedata.combining(part.tail[0])
    assert not part.text.endswith('́')
    # The word gate sees 14 Hindi words but not a false 35/36 threshold.
    clock = Clock()
    gate = FinalDeliveryGate(4., 35, True, clock)
    hindi14 = ' '.join(['हिन्दी'] * 14)
    assert gate.trigger(hindi14, now=0.) is None
    assert gate.trigger(hindi14, now=4.001) == 'time'
    gate.reset()
    assert gate.trigger(' '.join(['हिन्दी'] * 35), now=0.) is None
    assert gate.trigger(' '.join(['हिन्दी'] * 36), now=0.) == 'words'


def test_voice_onset_timer():
    clock = Clock()
    gate = FinalDeliveryGate(4., 35, True, clock)
    gate.voice_started(now=0.)
    assert gate.armed_at == 0. and gate.speech_active
    # Empty text before the first ASR result must not disarm active speech.
    assert gate.trigger('', now=4.5) is None
    assert gate.armed_at == 0.
    assert gate.trigger('alpha', now=4.501) == 'time'
    # A real commit while speech continues resets the timer.
    gate.note_committed(now=5.)
    assert gate.armed_at == 5.
    assert gate.trigger('alpha beta', now=9.0) is None
    assert gate.trigger('alpha beta', now=9.001) == 'time'
    # True idle/endpoint reset clears the onset; next voice arms fresh.
    gate.reset()
    assert not gate.speech_active and gate.armed_at is None
    gate.voice_started(now=20.)
    assert gate.armed_at == 20.
    assert gate.trigger('', now=24.5) is None
    assert gate.trigger('word', now=24.501) == 'time'


def test_gate_thresholds_and_state():
    clock = Clock()
    gate = FinalDeliveryGate(4., 35, True, clock)
    words35 = ' '.join('word' + str(index) for index in range(35))
    words36 = words35 + ' word35'
    assert gate.trigger(words35, now=0.) is None       # 35 is not above 35
    assert gate.trigger(words36, now=0.) == 'words'    # 36 is
    gate.reset()
    assert gate.trigger(words35, now=0.) is None
    assert gate.trigger(words35, now=4.0) is None      # strict greater than 4s
    assert gate.trigger(words35, now=4.001) == 'time'
    # Rewritten text does not move the armed deadline.
    gate.reset()
    assert gate.trigger('alpha beta', now=10.) is None
    assert gate.trigger('alpha beta gamma', now=12.5) is None
    assert gate.trigger('alpha beta gamma delta', now=14.001) == 'time'
    # Only a real commitment resets the clock; a remaining tail re-arms now.
    gate.note_committed(now=20.)
    assert gate.trigger('tail', now=20.5) is None   # first pending after idle
    assert gate.trigger('tail', now=24.5) is None
    assert gate.trigger('tail', now=24.501) == 'time'
    gate.note_committed('remaining tail', now=30.)
    assert gate.trigger('remaining tail', now=34.0) is None
    assert gate.trigger('remaining tail', now=34.001) == 'time'
    # Empty/idle and disabled gates never repeat a flush.
    gate.observe('', now=40.)
    assert gate.armed_at is None and not gate.pending
    assert gate.trigger('', now=100.) is None
    assert FinalDeliveryGate(4., 35, False, clock).trigger(words36, now=100.) is None
    # Unspaced CJK still has the wall-clock guarantee.
    gate.reset()
    assert gate.trigger('测量已经完成我们开始下一轮', now=50.) is None
    assert gate.trigger('测量已经完成我们开始下一轮', now=54.001) == 'time'


def test_boundaries():
    part = forced_delivery('The sensor is ready. We begin the next test.', 'English')
    assert part.kind == 'sentence' and part.text == 'The sensor is ready.' and part.tail
    part = forced_delivery('The detector measures particles, however the team compares results', 'English')
    assert part.kind == 'comma' and part.text.endswith(',') and 'results' in part.tail
    words = ['word' + str(index) for index in range(40)]
    part = forced_delivery(' '.join(words), 'English', 35)
    assert part.kind == 'word' and part.words == 35 and part.tail_words == 5
    assert part.text == ' '.join(words[:35]) and part.tail == ' '.join(words[35:])
    # One very long token is one word, not one per letter; still retained as a
    # valid tail by a time-triggered full commit.
    long_word = 'very' + 'long' * 40
    assert lexical_word_count(long_word) == 1
    part = forced_delivery(long_word, 'English', 1)
    assert part.text == long_word and not part.tail
    # text_rules guards survive the fallback: no cut after an abbreviation.
    part = forced_delivery('Dr. Smith is ready now', 'English', 1)
    assert part.text != 'Dr.' and part.text.startswith('Dr. Smith')
    # Decimal, unit and conjunction boundaries are not split blindly.
    text = 'The energy is 125 GeV and the detector continues with many more measurements'
    part = forced_delivery(text, 'English', 4)
    assert not part.text.endswith('125') and not part.text.endswith('and')
    # Forced delivery uses the real comma after Dr. Smith even though ordinary
    # sentence joining would prefer to keep "and then" attached.
    text = 'Send it to Dr. Smith, and then call the team about the result immediately'
    part = forced_delivery(text, 'English', 5)
    assert part.kind == 'comma', part
    assert part.text == 'Send it to Dr. Smith,', part
    assert part.tail == 'and then call the team about the result immediately', part
    assert part.text + ' ' + part.tail == text
    # Without a comma, do not end on the dangling connector "and then"; the
    # nearby Dr. Smith phrase is the better lexical cut.
    no_comma = 'Send it to Dr. Smith and then call the team about the result immediately'
    part = forced_delivery(no_comma, 'English', 5)
    assert part.kind == 'word' and part.text == 'Send it to Dr. Smith', part
    assert part.tail.startswith('and then call') and not part.text.endswith('then')
    # A dependent lead still gets its real comma clause under forced delivery;
    # lexical fallback keeps the ordinary conditional protection.
    part = forced_delivery('If the detector fails, we repeat the measurement immediately',
                           'English', 5)
    assert part.kind == 'comma' and part.text == 'If the detector fails,', part
    # A comma inside a number is never a clause boundary and is not split.
    part = forced_delivery('The value is 3,000 units and the experiment continues',
                           'English', 3)
    assert part.kind == 'word', part
    assert part.text == 'The value is 3,000' and part.tail.startswith('units'), part
    # A valid full stop keeps priority even when the sentence exceeds the
    # word trigger by a lot: 35 is a trigger, not a hard caption-length cap.
    sentence = ' '.join('word' + str(index) for index in range(40)) + '.'
    part = forced_delivery(sentence + ' We continue', 'English', 5)
    assert part.kind == 'sentence' and part.text == sentence and part.tail == 'We continue'
    # Unspaced CJK becomes conservative tokens; mixed English stays separate
    # and is never swallowed by an adjacent CJK run.
    part = forced_delivery('我们使用English继续观察', 'Chinese', 35)
    assert part.words == 3 and part.text == '我们使用English继续观察' and not part.tail


def test_parakeet_time_gate_without_extra_asr():
    clock = Clock()
    engine = TimedEngine('The detector measures particles and fields with high precision and continues')
    events = []
    recognizer = streaming.Recognizer(engine, lambda kind, value: events.append((kind, value)),
        quiet=True, interval=1.5, agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 1.6, True)
    assert engine.calls == 1
    assert not committed(events), committed(events)
    assert recognizer.delivery.pending and recognizer.pending_text()
    clock.set(4.001)
    recognizer.poll()                       # no push, no new inference generation
    assert engine.calls == 1
    forced = committed(events)
    assert len(forced) == 1, forced
    assert forced[0]['text'] == ' '.join(engine.text.split())
    assert forced[0]['uncertain'] and forced[0]['boundary_reason'] == 'final_delivery_time'
    assert forced[0]['audio_end_ms'] > forced[0]['audio_start_ms']
    # Idle repeats must not publish the same words again.
    for _ in range(20): recognizer.poll()
    assert len(committed(events)) == 1
    recognizer.poll(stop=True)
    assert len(committed(events)) == 1


def test_parakeet_word_gate_drains_same_generation():
    clock = Clock()
    words = ['word' + str(index) for index in range(72)]
    engine = TimedEngine(' '.join(words))
    events = []
    recognizer = streaming.Recognizer(engine, lambda kind, value: events.append((kind, value)),
        quiet=True, interval=1.5, agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 1.504, True)   # exactly one inference boundary, no spare poll
    assert engine.calls == 1
    first = committed(events)
    assert len(first) == 1 and len(first[0]['text'].split()) == 35, first
    assert first[0]['boundary_reason'] == 'final_delivery_words'
    # The original timed timeline still recognizes the promoted prefix as
    # committed, so a repeated hypothesis starts after it.
    current = timed_result(' '.join(words), recognizer.clock / RATE)['words']
    assert recognizer.timeline.cut(current) >= 35, recognizer.timeline.cut(current)
    # The same existing hypothesis still has a genuinely new unpublished tail;
    # ordinary polls drain it without another ASR call.
    recognizer.poll()
    second = committed(events)
    assert engine.calls == 1 and len(second) == 2, (engine.calls, second)
    assert len(second[1]['text'].split()) == 35
    assert first[0]['text'] + ' ' + second[1]['text'] == ' '.join(words[:70])
    recognizer.poll()
    assert len(committed(events)) == 2
    assert recognizer.pending_text() == 'word70 word71'
    # A stop flushes the retained tail once and resets the gate.
    recognizer.poll(stop=True)
    final = committed(events)
    assert final[-1]['text'] == 'word70 word71'
    assert recognizer.delivery.armed_at is None and not recognizer.pending_preview


def test_qwen_preview_only_and_time_gate():
    clock = Clock()
    text = 'alpha beta gamma delta epsilon zeta eta theta'
    engine = SequenceEngine(text)
    events = []
    recognizer = qwen_streaming.QwenRecognizer(engine,
        lambda kind, value: events.append((kind, value)), quiet=True, interval=1.5,
        agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 3.2, True)
    assert engine.calls == 1
    assert recognizer.segmenter.text == '' and recognizer.gate_preview
    assert not committed(events)
    clock.set(4.001)
    recognizer.poll()
    assert engine.calls == 1
    forced = committed(events)
    assert len(forced) == 1 and forced[0]['text'] == text, forced
    assert forced[0]['uncertain'] and forced[0]['approximate_timestamps']
    assert forced[0]['boundary_reason'] == 'final_delivery_time'
    for _ in range(10): recognizer.poll()
    assert len(committed(events)) == 1
    recognizer.poll(stop=True)
    assert len(committed(events)) == 1


def test_qwen_window_revision_does_not_duplicate_promoted_prefix():
    clock = Clock()
    original = ['word' + str(index) for index in range(40)]
    revised = (['inserted0', 'inserted1'] + original[:11]
               + ['revised'] + original[12:32])
    tail = original[35:]
    engine = SequenceEngine(' '.join(original), ' '.join(revised + tail))
    events = []
    # This scenario needs exactly two decoder passes; the interval is kept
    # above the 3.2 s feed window so faster preview cadence does not add calls
    # that would obscure the revision/deduplication contract under test.
    recognizer = qwen_streaming.QwenRecognizer(engine,
        lambda kind, value: events.append((kind, value)), quiet=True, interval=3.2,
        agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 3.2, True)
    first = committed(events)
    assert engine.calls == 1 and len(first) == 1 and len(first[0]['text'].split()) == 35
    forced_text = first[0]['text']
    # A revised rolling hypothesis (insertions and a deletion inside the
    # promoted prefix) must not republish that prefix.
    feed(recognizer, 3.2, True)
    assert engine.calls == 2
    later = committed(events)[1:]
    assert all(forced_text not in row['text'] for row in later), later
    recognizer.poll(stop=True)
    later = committed(events)[1:]
    assert all(forced_text not in row['text'] for row in later), later
    # No output may repeat a long run of the already-promoted words.
    promoted_words = forced_text.split()
    for row in later:
        row_words = row['text'].split()
        assert not any(row_words[index:index + 6] == promoted_words[:6]
                       for index in range(max(0, len(row_words) - 5)))


def test_qwen_fast_revision_preserves_tail():
    original = ['word' + str(index) for index in range(40)]
    # Insertions and a substitution keep the words immediately before the
    # unpublished tail intact, so the tail contract is independently visible
    # from the adversarial deletion case above.
    revised = (['inserted0', 'inserted1'] + original[:11]
               + ['revised'] + original[12:35])
    tail = original[35:]
    extra = ['extra0', 'extra1', 'extra2']
    tail_text = ' '.join(tail + extra)
    for interval in (1.5, .5):
        clock = Clock()
        engine = SequenceEngine(' '.join(original),
            ' '.join(revised + tail), ' '.join(revised + tail + extra))
        events = []
        recognizer = qwen_streaming.QwenRecognizer(engine,
            lambda kind, value: events.append((kind, value)), quiet=True, interval=interval,
            agc={'enabled': False}, final_delivery_clock=clock)
        feed(recognizer, 3.2, True)
        first = committed(events)
        assert engine.calls == 1 and len(first) == 1 and len(first[0]['text'].split()) == 35, (interval, engine.calls, first)
        forced_text = first[0]['text']
        feed(recognizer, 3.4, True)
        assert engine.calls > 2, (interval, engine.calls)
        recognizer.poll(stop=True)
        rows = committed(events)
        later = rows[1:]
        assert all(forced_text not in row['text'] for row in later), (interval, later)
        # Faster decoding must not publish the promoted prefix twice or lose
        # the growing tail.
        assert ' '.join(row['text'] for row in later).strip() == tail_text, (interval, later)
        promoted_words = forced_text.split()
        for row in later:
            row_words = row['text'].split()
            assert not any(row_words[index:index + 6] == promoted_words[:6]
                           for index in range(max(0, len(row_words) - 5))), (interval, row)


TIMINGS = {}


def test_causal_voice_onset_timing():
    text = 'alpha beta gamma delta epsilon zeta eta theta iota kappa'

    def run(cls, engine, wait):
        clock = Clock(); events = []
        def emit(kind, value): events.append((kind, dict(value), clock.value))
        recognizer = cls(engine, emit, quiet=True, interval=1.5,
            agc={'enabled': False}, final_delivery_wait=wait,
            final_delivery_clock=clock)
        feed_clocked(recognizer, 6., clock)
        first = next((value, wall) for kind, value, wall in events
                     if kind == 'committed')
        return recognizer, first

    # The first detected voice block arms at wall time 0, before any ASR text.
    for cls, engine in ((streaming.Recognizer, TimedEngine(text)),
                        (qwen_streaming.QwenRecognizer, SequenceEngine(text))):
        clock = Clock(); recognizer = cls(engine, lambda *_: None, quiet=True,
            interval=1.5, agc={'enabled': False}, final_delivery_clock=clock)
        clock.set(0.); recognizer.push(np.full(BLOCK, .01, np.float32))
        assert recognizer.delivery.armed_at == 0.
        assert recognizer.delivery.speech_active
        # Polls with no recognized text yet must not disarm the onset timer.
        clock.set(.5); recognizer.poll()
        assert recognizer.delivery.armed_at == 0. and not recognizer.delivery.pending

    for cls, engine in ((streaming.Recognizer, TimedEngine(text)),
                        (qwen_streaming.QwenRecognizer, SequenceEngine(text))):
        _, (value, wall) = run(cls, engine, 4.)
        assert value['boundary_reason'] == 'final_delivery_time'
        assert 4.0 < wall <= 4.1, (cls.__name__, wall)
        TIMINGS[cls.__name__ + '_wait4'] = round(wall, 3)

    # 2 s wait: Parakeet can flush just after 2 s once a preview exists.
    _, (value, wall) = run(streaming.Recognizer, TimedEngine(text), 2.)
    assert value['boundary_reason'] == 'final_delivery_time'
    assert 2.0 < wall <= 2.1, wall
    TIMINGS['Recognizer_wait2'] = round(wall, 3)
    # Qwen cannot emit before its first result near 3 s, but must not add
    # another 2 s grace after that result.
    _, (value, wall) = run(qwen_streaming.QwenRecognizer, SequenceEngine(text), 2.)
    assert value['boundary_reason'] == 'final_delivery_time'
    assert 3.0 < wall <= 3.1, wall
    TIMINGS['QwenRecognizer_wait2'] = round(wall, 3)


def test_provisional_draft_replaced_by_forced_prefix():
    long = ('The detector measures many different particles, however the scientists '
            'compare these observations against detailed predictions')
    prefix = StablePrefix(limit=2)
    assert prefix.offer(long, 0, 3., 0., 'English') is None
    draft = prefix.offer(long, 0, 6., 0., 'English')
    assert draft and draft['text'].endswith(',')
    forced = forced_delivery(long, 'English', 35)
    assert forced and forced.kind == 'comma' and forced.text == draft['text']
    drafts = DraftCaptions()
    row = drafts.add('session', draft)
    ident = row['id']
    # The forced durable prefix supersedes the pending reversible request.
    drafts.link('session', draft['sentence_id'], 'final-forced', forced.text)
    assert not drafts.entries
    assert not drafts.translated(ident, 'late provisional', True)


def test_blocking_inference_limitation():
    """The deadline cannot fire while the worker is inside blocking inference."""
    clock = Clock()

    class SlowEngine(TimedEngine):
        def infer(self, audio):
            result = super().infer(audio)
            if self.calls == 2:
                clock.set(7.5)   # second decoder call blocks past the 4 s deadline
            return result

    engine = SlowEngine('The detector measures particles and fields with high precision and continues')
    events = []
    recognizer = streaming.Recognizer(engine, lambda kind, value: events.append((kind, value)),
        quiet=True, interval=1.5, agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 1.504, True)
    assert engine.calls == 1 and not committed(events)
    assert recognizer.delivery.pending and recognizer.delivery.armed_at == 0.
    feed(recognizer, 1.504, True)       # second infer returns only after simulated 7.5 s
    assert engine.calls == 2
    first = committed(events)
    assert len(first) == 1 and first[0]['boundary_reason'] == 'final_delivery_time'
    # Observed at 7.5 s, not a hard 4 s: the worker could not poll mid-infer.
    assert clock.value == 7.5 and clock.value - recognizer.delivery.wait > 0


def test_reset_and_window():
    clock = Clock()
    engine = TimedEngine('The detector continues with a reasonably long unpunctuated response')
    recognizer = streaming.Recognizer(engine, lambda *_: None, quiet=True, interval=1.5,
        agc={'enabled': False}, final_delivery_clock=clock)
    feed(recognizer, 1.6, True)
    clock.set(4.001)
    recognizer.poll()
    assert recognizer.delivery.pending is False
    recognizer.reset()
    assert recognizer.delivery.armed_at is None and not recognizer.pending_preview
    # A fresh utterance re-arms only when pending speech appears again.
    feed(recognizer, 1.6, True)
    assert recognizer.delivery.pending
    assert recognizer.delivery.armed_at == clock.value


if __name__ == '__main__':
    test_word_contract()
    test_unicode_marks_and_dense_scripts()
    test_voice_onset_timer()
    test_gate_thresholds_and_state()
    test_boundaries()
    test_causal_voice_onset_timing()
    test_provisional_draft_replaced_by_forced_prefix()
    test_parakeet_time_gate_without_extra_asr()
    test_parakeet_word_gate_drains_same_generation()
    test_qwen_preview_only_and_time_gate()
    test_qwen_window_revision_does_not_duplicate_promoted_prefix()
    test_qwen_fast_revision_preserves_tail()
    test_blocking_inference_limitation()
    test_reset_and_window()
    print(json.dumps(dict(word_contract=True, unicode_marks_and_dense_runs=True,
        voice_onset_timer=True, strict_4s_and_35=True,
        sentence_comma_word_boundaries=True, causal_4s_and_2s_timing=True,
        provisional_draft_replaced=True, parakeet_time_gate=True,
        parakeet_word_gate_same_generation=True, qwen_preview_only=True,
        qwen_window_revision_no_duplicate=True, qwen_fast_revision_tail=True,
        blocking_inference_observed_at_7_5s=True,
        reset_and_window=True, first_commit_seconds=TIMINGS,
        no_extra_asr=True, no_api_microphone_or_model=True)))
