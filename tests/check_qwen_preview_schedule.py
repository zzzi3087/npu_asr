"""Causal Qwen/Mega preview scheduling contracts; no model, NPU, audio, GUI, network.

Virtual wall time and virtual audio blocks are independent. The fake engine
advances only wall time while it "decodes", so a cost measured by
``time.perf_counter`` must cover the whole call rather than a reported field.
No sleeps are used.
"""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import qwen_streaming

RATE = 16000
BLOCK = 512
STEP = BLOCK / RATE
TEXT = 'The detector measures particles accurately today.'


class WallClock:
    def __init__(self):
        self.value = 0.

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeVad:
    def __call__(self, block):
        return .99 if block[0] else 0.


class FakeEngine:
    model_id = 'qwen3-asr-1.7b'
    language = 'English'
    streaming_mode = 'utterance'
    max_samples = RATE * 17

    def __init__(self, wall, cost=0., text=TEXT, model_id=None):
        self.wall = wall
        self.cost = cost
        self.text = text
        if model_id:
            self.model_id = model_id
        self.calls = []
        self.inside = False
        self.infer_ms = 0.

    def infer(self, audio):
        assert not self.inside, 'concurrent inference'
        self.inside = True
        try:
            self.calls.append(len(audio))
            if self.cost:
                self.wall.advance(self.cost)
            words = [(word, .1 * index) for index, word in enumerate(self.text.split())]
            return dict(text=self.text, words=words, language='English',
                        infer_ms=self.infer_ms, cpu_ms=0)
        finally:
            self.inside = False


def make(interval, wall, engine, events=None, **kwargs):
    emit = (lambda kind, value: events.append((kind, value))) if events is not None else (lambda *_: None)
    return qwen_streaming.QwenRecognizer(
        engine, emit, quiet=True, interval=interval, agc={'enabled': False},
        final_delivery_clock=wall, **kwargs)


def feed(recognizer, seconds, speech=True):
    block = np.full(BLOCK, .01 if speech else 0., np.float32)
    for _ in range(int(round(seconds * RATE / BLOCK))):
        recognizer.push(block)
        recognizer.poll()


def feed_until_first_call(recognizer, engine, limit=5.):
    block = np.full(BLOCK, .01, np.float32)
    for _ in range(int(round(limit * RATE / BLOCK))):
        if engine.calls:
            break
        recognizer.push(block)
        recognizer.poll()
    assert engine.calls, 'no first inference'


def test_first_context_and_interval_choices():
    for model_id in ('qwen3-asr-1.7b', 'mega-asr'):
        rows = {}
        for interval in (.5, 1., 1.5, 2.):
            wall = WallClock()
            engine = FakeEngine(wall, model_id=model_id)
            with patch.object(qwen_streaming, 'Vad', FakeVad), \
                    patch.object(qwen_streaming.time, 'perf_counter', wall):
                recognizer = make(interval, wall, engine)
                feed(recognizer, 2.9, True)
                assert engine.calls == [], (model_id, interval, engine.calls)
                feed(recognizer, .2, True)
                assert engine.calls, (model_id, interval)
                assert int(3. * RATE) - BLOCK <= engine.calls[0] <= int(3.2 * RATE), engine.calls[0]
                feed(recognizer, 6., True)
            sizes = engine.calls
            assert len(sizes) >= 3, (model_id, interval, sizes)
            deltas = [later - earlier for earlier, later in zip(sizes, sizes[1:])]
            assert all(delta >= int(interval * RATE) for delta in deltas), (model_id, interval, sizes)
            rows[str(interval)] = sizes
            recognizer.poll(stop=True)
        assert rows['2.0'][1] - rows['2.0'][0] > rows['0.5'][1] - rows['0.5'][0]


def test_wall_cost_backoff_and_invalid_timings():
    wall = WallClock()
    engine = FakeEngine(wall, cost=1.)
    events = []
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine, events=events)
        feed(recognizer, 3.2, True)
        assert engine.calls and recognizer.inference_cost >= 1., recognizer.inference_cost
        assert recognizer.refresh_samples() >= int(1. / .35 * RATE) - BLOCK
        metrics = [value for kind, value in events if kind == 'metrics']
        assert metrics and metrics[-1]['refresh_interval_ms'] >= (int(1. / .35 * RATE) - BLOCK) / 16
        feed(recognizer, 6., True)
        assert len(engine.calls) >= 2, engine.calls
        assert engine.calls[1] - engine.calls[0] >= int(1. / .35 * RATE) - BLOCK, engine.calls
        recognizer.poll(stop=True)

    for bad in (float('nan'), float('inf'), 'bad', None):
        wall = WallClock()
        engine = FakeEngine(wall)
        engine.infer_ms = bad
        with patch.object(qwen_streaming, 'Vad', FakeVad), \
                patch.object(qwen_streaming.time, 'perf_counter', wall):
            recognizer = make(.5, wall, engine)
            feed(recognizer, 3.2, True)
            assert engine.calls, bad
            assert math.isfinite(recognizer.inference_cost), (bad, recognizer.inference_cost)
            assert recognizer.refresh_samples() == recognizer.interval_samples, bad
            recognizer.poll(stop=True)

    # A reported inference time is a fallback when wall time is unavailable.
    wall = WallClock()
    engine = FakeEngine(wall)
    engine.infer_ms = 700.
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 3.2, True)
        assert recognizer.inference_cost >= .7, recognizer.inference_cost
        assert recognizer.refresh_samples() >= int(.7 / .35 * RATE) - BLOCK
        recognizer.poll(stop=True)


def test_numeric_bounds_and_overflow_safety():
    for bad_interval in (1e308, float('inf'), float('nan'), 0, -1, None, 'bad'):
        wall = WallClock()
        engine = FakeEngine(wall)
        with patch.object(qwen_streaming, 'Vad', FakeVad), \
                patch.object(qwen_streaming.time, 'perf_counter', wall):
            recognizer = make(bad_interval, wall, engine)
            assert 8000 <= recognizer.interval_samples <= 999999 * RATE, bad_interval
            assert math.isfinite(recognizer.refresh_samples()), bad_interval
            feed(recognizer, 3.2, True)
            assert len(engine.calls) <= 1, (bad_interval, engine.calls)
            recognizer.poll(stop=True)

    for bad_ms in (1e308, float('inf'), float('nan'), -1e308):
        wall = WallClock()
        engine = FakeEngine(wall)
        engine.infer_ms = bad_ms
        with patch.object(qwen_streaming, 'Vad', FakeVad), \
                patch.object(qwen_streaming.time, 'perf_counter', wall):
            recognizer = make(.5, wall, engine)
            feed(recognizer, 3.2, True)
            assert engine.calls, bad_ms
            assert math.isfinite(recognizer.inference_cost), bad_ms
            assert recognizer.inference_cost <= 3600., recognizer.inference_cost
            assert math.isfinite(recognizer.refresh_samples()), bad_ms
            recognizer.poll(stop=True)

    # A huge finite wall-clock delta must be capped too, not converted to int.
    wall = WallClock()
    engine = FakeEngine(wall, cost=1e308)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 3.2, True)
        assert engine.calls
        assert math.isfinite(recognizer.inference_cost), recognizer.inference_cost
        assert recognizer.inference_cost <= 3600., recognizer.inference_cost
        assert math.isfinite(recognizer.refresh_samples())
        recognizer.poll(stop=True)


def test_short_utterance_endpoint_and_stop_are_not_forced_to_three_seconds():
    for stop in (False, True):
        wall = WallClock()
        engine = FakeEngine(wall)
        with patch.object(qwen_streaming, 'Vad', FakeVad), \
                patch.object(qwen_streaming.time, 'perf_counter', wall):
            recognizer = make(.5, wall, engine)
            feed(recognizer, 1., True)
            if stop:
                recognizer.poll(stop=True)
            else:
                feed(recognizer, .9, False)
            assert len(engine.calls) == 1, (stop, engine.calls)
            assert engine.calls[0] < int(3. * RATE), (stop, engine.calls)


def test_silence_skip_and_same_audio_stop_reuse():
    wall = WallClock()
    engine = FakeEngine(wall)
    events = []
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine, events=events)
        feed(recognizer, 3.2, True)
        assert len(engine.calls) == 1, engine.calls
        # No preview inference may be scheduled from silence alone.
        feed(recognizer, .7, False)
        assert len(engine.calls) == 1, engine.calls
        # The endpoint may finalize the short tail; that is not a preview.
        feed(recognizer, .2, False)
        assert len(engine.calls) == 2, engine.calls
        recognizer.poll(stop=True)

    wall = WallClock()
    engine = FakeEngine(wall)
    events = []
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine, events=events)
        feed_until_first_call(recognizer, engine)
        assert len(engine.calls) == 1
        for _ in range(50):
            recognizer.poll()
        assert len(engine.calls) == 1, engine.calls
        recognizer.poll(stop=True)
        assert len(engine.calls) == 1, engine.calls
        assert recognizer.size == 0
        committed = [value for kind, value in events if kind == 'committed']
        assert committed and recognized_text(committed) == TEXT, committed


def recognized_text(rows):
    return ' '.join(row['text'] for row in rows).strip()


def test_new_utterance_ignores_inherited_cost_after_stop():
    wall = WallClock()
    engine = FakeEngine(wall, cost=1.48)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 3.2, True)
        assert recognizer.inference_cost >= 1.4, recognizer.inference_cost
        recognizer.poll(stop=True)
        assert recognizer.inference_cost == 0.
        # Also cover a real idle gap before the next utterance.
        feed(recognizer, 5., False)
        engine.cost = .1
        calls = len(engine.calls)
        feed(recognizer, 2.4, True)
        assert len(engine.calls) == calls, engine.calls
        feed(recognizer, .2, True)
        assert len(engine.calls) == calls + 1, engine.calls
        assert int(2.9 * RATE) <= engine.calls[-1] <= int(3.2 * RATE), engine.calls[-1]
        recognizer.poll(stop=True)


def test_hard_window_continuation_retains_cost_and_idle_clears_it():
    wall = WallClock()
    engine = FakeEngine(wall, cost=5.)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 12., True)
        assert recognizer.inference_cost >= 5., recognizer.inference_cost
        # An actual idle gap after a hard-window reset is a new utterance.
        feed(recognizer, 5., False)
        assert not recognizer.active and recognizer.inference_cost >= 5.
        engine.cost = .1
        calls = len(engine.calls)
        feed(recognizer, 2.4, True)
        assert len(engine.calls) == calls, engine.calls
        feed(recognizer, .2, True)
        assert len(engine.calls) == calls + 1, engine.calls
        assert int(2.9 * RATE) <= engine.calls[-1] <= int(3.2 * RATE), engine.calls[-1]
        assert recognizer.inference_cost <= .2, recognizer.inference_cost
        recognizer.poll(stop=True)


def test_continuous_hard_window_retains_cost_without_storm():
    wall = WallClock()
    engine = FakeEngine(wall, cost=1.48)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 12., True)
        assert recognizer.inference_cost >= 1.4, recognizer.inference_cost
        calls = len(engine.calls)
        # No idle: the cost remains in force, so continuation does not restart
        # with a 3 s / 0.5 s burst. One bounded first decode is still allowed.
        feed(recognizer, 5.5, True)
        assert len(engine.calls) == calls + 1, engine.calls
        assert int(4.1 * RATE) <= engine.calls[-1] <= int(4.4 * RATE), engine.calls[-1]
        recognizer.poll(stop=True)


def test_repeated_short_utterances_do_not_burst():
    wall = WallClock()
    engine = FakeEngine(wall, cost=.1)
    events = []
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine, events=events)
        for _ in range(8):
            feed(recognizer, 1., True)
            feed(recognizer, .9, False)
            assert recognizer.inference_cost == 0., recognizer.inference_cost
            assert not recognizer.active
        assert len(engine.calls) == 8, engine.calls
        assert all(size < int(3. * RATE) for size in engine.calls), engine.calls
        committed = [value for kind, value in events if kind == 'committed']
        assert len(committed) == 8, committed
        assert all(row['text'] == TEXT for row in committed), committed


def test_hard_window_reset_does_not_reuse_old_audio():
    wall = WallClock()
    engine = FakeEngine(wall)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(.5, wall, engine)
        feed(recognizer, 12., True)
        assert any(size >= 12 * RATE for size in engine.calls), engine.calls
        assert not recognizer.active and recognizer.size == 0
        assert recognizer.last_infer_size == 0 and recognizer.latest is None
        calls = len(engine.calls)
        # A new utterance starts from new audio only; its first decode is again
        # around three seconds, not an old 12+ second window.
        feed(recognizer, 3.4, True)
        assert len(engine.calls) == calls + 1, engine.calls
        assert int(2.9 * RATE) <= engine.calls[-1] <= int(3.3 * RATE), engine.calls[-1]
        recognizer.poll(stop=True)


def test_forced_delivery_all_realtime_model_ids_without_extra_infer():
    for model_id in ('qwen3-asr-1.7b', 'mega-asr'):
        for gate in ('time', 'words'):
            wall = WallClock()
            text = (' '.join('word%d' % index for index in range(40)) + '.') if gate == 'words' else TEXT
            engine = FakeEngine(wall, text=text, model_id=model_id)
            events = []
            with patch.object(qwen_streaming, 'Vad', FakeVad), \
                    patch.object(qwen_streaming.time, 'perf_counter', wall):
                recognizer = make(.5, wall, engine, events=events,
                                  final_delivery_enabled=True,
                                  final_delivery_wait=4., final_delivery_words=35)
                feed(recognizer, 3.2, True)
                assert len(engine.calls) == 1, (model_id, gate, engine.calls)
                if gate == 'time':
                    wall.advance(4.1)
                    recognizer.poll()
                committed = [value for kind, value in events if kind == 'committed']
                assert committed, (model_id, gate, events)
                assert committed[0]['boundary_reason'] == 'final_delivery_' + gate, committed
                assert committed[0]['asr_model'] == model_id, committed
                assert len(engine.calls) == 1, (model_id, gate, engine.calls)
                recognizer.poll(stop=True)


def test_file_mode_disables_preview_inference():
    wall = WallClock()
    engine = FakeEngine(wall)
    with patch.object(qwen_streaming, 'Vad', FakeVad), \
            patch.object(qwen_streaming.time, 'perf_counter', wall):
        recognizer = make(999999, wall, engine, final_delivery_enabled=False)
        feed(recognizer, 6., True)
        assert engine.calls == [], engine.calls
        assert recognizer.refresh_samples() >= 999999 * RATE
        recognizer.poll(stop=True)
        assert len(engine.calls) == 1, engine.calls


if __name__ == '__main__':
    test_first_context_and_interval_choices()
    test_wall_cost_backoff_and_invalid_timings()
    test_numeric_bounds_and_overflow_safety()
    test_short_utterance_endpoint_and_stop_are_not_forced_to_three_seconds()
    test_silence_skip_and_same_audio_stop_reuse()
    test_new_utterance_ignores_inherited_cost_after_stop()
    test_hard_window_continuation_retains_cost_and_idle_clears_it()
    test_continuous_hard_window_retains_cost_without_storm()
    test_repeated_short_utterances_do_not_burst()
    test_hard_window_reset_does_not_reuse_old_audio()
    test_forced_delivery_all_realtime_model_ids_without_extra_infer()
    test_file_mode_disables_preview_inference()
    print(json.dumps({'ok': True, 'tests': 12,
                      'scope': 'fake VAD/engine/wall clock; no model, NPU, audio, GUI or network'},
                     ensure_ascii=False))
