"""Causal virtual capture/inference simulation; never a real NPU latency claim.

No audio device, model, GUI or network is opened. Words become available only
after their known acoustic end plus decoder discovery lag. Inference advances
virtual wall time while capture continues; pending blocks drain before poll.
"""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import streaming

RATE = 16000
BLOCK = 512
STEP = BLOCK / RATE
TARGET = 'The detector measures particle energies accurately today safely.'


def terminal_candidate_contract():
    """Two tail rechecks can seek context; their periods alone cannot commit."""
    events = []

    class Vad:
        def __call__(self, block):
            return .99

    class Engine:
        language = 'English'
        max_samples = RATE * 30
        calls = 0

        def infer(self, audio):
            self.calls += 1
            words = [(w, .1 + i * .1) for i, w in enumerate('The detector is ready.'.split())]
            return dict(text='The detector is ready.', words=words,
                        word_spans=[(w, t, t + .06) for w, t in words], infer_ms=80)

    with patch.object(streaming, 'Vad', Vad):
        engine = Engine()
        recognizer = streaming.Recognizer(engine, lambda k, v: events.append((k, v)),
                                         interval=.5, progressive=True, agc={'enabled': False})
        for _ in range(48):
            recognizer.push(np.full(BLOCK, .01, np.float32))
            recognizer.poll()
            if engine.calls in (1, 2):
                assert recognizer.confirmation_due is not None
                due = recognizer.confirmation_due
                recognizer.schedule_confirmation('The detector is ready.')
                assert recognizer.confirmation_due == due
                assert recognizer.confirmation_checks == engine.calls
            if engine.calls == 3:
                break
        assert engine.calls == 3
        assert recognizer.confirmation_checks == 2
        assert not any(kind == 'committed' for kind, _ in events)
        assert any(kind == 'provisional' for kind, _ in events)
        assert recognizer.confirmation_due is None
        recognizer.schedule_confirmation('The detector is ready.')
        assert recognizer.confirmation_due is None
        # A fresh decode still cannot arrange a third recheck for this sentence.
        recognizer.inference_generation += 1
        recognizer.schedule_confirmation('The detector is ready.')
        assert recognizer.confirmation_due is None
        assert recognizer.confirmation_checks == 2
        # Advancing the sentence gives it a separate budget, even with the same
        # latest result. Repeated scheduling cannot move or consume that timer.
        recognizer.segmenter.sequence += 1
        recognizer.schedule_confirmation('The detector is ready.')
        due = recognizer.confirmation_due
        assert due is not None and recognizer.confirmation_checks == 1
        recognizer.clock += BLOCK
        recognizer.schedule_confirmation('The detector is ready.')
        assert recognizer.confirmation_due == due
        assert recognizer.confirmation_checks == 1
        # Even after a timer is cleared, the same result cannot spend twice.
        recognizer.confirmation_due = None
        recognizer.schedule_confirmation('The detector is ready.')
        assert recognizer.confirmation_due is None
        assert recognizer.confirmation_checks == 1


def simulate(interval, phase, cost, continuous):
    state = dict(wall=0., delivered=0, calls=0, backlog=0., future_checks=0)
    target_end = 4.5 + phase
    tokens = [(w, .1 + i * .6 + phase, .3 + i * .6 + phase)
              for i, w in enumerate(TARGET.split())]
    if continuous:
        tokens += [(w, target_end + .05 + i * .18,
                    target_end + .17 + i * .18)
                   for i, w in enumerate('We begin another careful measurement with the new detector now'.split())]
    speech_end = tokens[-1][2]
    events = []
    recognizer = None

    class Vad:
        def __call__(self, block):
            return .99 if block[0] else 0.

    class Engine:
        model_id = 'parakeet-v3'
        language = 'English'
        max_samples = RATE * 30

        def infer(self, audio):
            state['calls'] += 1
            observed = recognizer.clock / RATE
            base = recognizer.base / RATE
            assert len(audio) <= recognizer.clock - recognizer.base
            visible = [(w, start, end) for w, start, end in tokens
                       if start >= base and end + .08 <= observed + 1e-8]
            assert all(end <= observed for _, _, end in visible)
            state['future_checks'] += len(visible)
            state['wall'] += cost
            state['backlog'] = max(state['backlog'], state['wall'] - observed)
            return dict(text=' '.join(w for w, _, _ in visible),
                        words=[(w, start - base) for w, start, _ in visible],
                        word_spans=[(w, start - base, end - base) for w, start, end in visible],
                        infer_ms=cost * 1000, cpu_ms=0)

    def emit(kind, value):
        if kind in ('partial', 'provisional', 'committed'):
            events.append((kind, value.get('text', ''), state['wall'], recognizer.clock / RATE))

    # Patching the module's timer also supports adaptive schedules which use
    # measured elapsed inference time. This does not make zero-cost fake ASR.
    with patch.object(streaming, 'Vad', Vad), patch('time.perf_counter', lambda: state['wall']):
        recognizer = streaming.Recognizer(Engine(), emit, quiet=True, interval=interval,
                                         progressive=True, agc={'enabled': False})
        while state['wall'] < speech_end + 5:
            available = math.floor((state['wall'] + 1e-8) / STEP)
            if available <= state['delivered']:
                state['wall'] = (state['delivered'] + 1) * STEP
                available = state['delivered'] + 1
            # Actual AudioWorker coalesces all queued capture blocks before ASR.
            while state['delivered'] < available:
                end = (state['delivered'] + 1) * STEP
                speech = end <= speech_end + STEP
                recognizer.push(np.full(BLOCK, .01 if speech else 0., np.float32))
                state['delivered'] += 1
            recognizer.poll()

    first = {}
    for kind, text, wall, audio in events:
        if TARGET in text and kind not in first:
            first[kind] = dict(wall_delay=round(wall - target_end, 4),
                               audio_delay=round(audio - target_end, 4))
            if 'source' not in first:
                first['source'] = dict(first[kind])
    assert 'source' in first and 'committed' in first, (interval, phase, cost, continuous, events)
    assert all(v['wall_delay'] >= 0 for v in first.values()), first
    assert state['calls'] > 2 and state['future_checks'] > 0
    # Catch an accidental busy loop while allowing two boundary verifications.
    assert state['calls'] <= math.ceil((speech_end + 5) / min(interval, .256)) + 2
    return dict(interval=interval, phase=phase, infer_seconds=cost,
                scenario='continuous' if continuous else 'silence',
                acoustic_end=round(target_end, 3), delivery=first,
                calls=state['calls'], max_capture_backlog=round(state['backlog'], 4))


if __name__ == '__main__':
    terminal_candidate_contract()
    rows = [simulate(interval, phase, cost, continuous)
            for interval in (1.5, .5)
            for continuous in (False, True)
            for cost in (.08, .12, .15, .25, .65)
            for phase in (0., .0625, .125, .1875, .25, .3125, .375, .4375)]
    print(json.dumps(dict(measurement='simulation_only_not_real_NPU_or_GUI_or_translation',
                          discovery_lag_seconds=.08, rows=rows), indent=2))
    failures = [row for row in rows if row['interval'] == .5
                and row['scenario'] == 'continuous' and row['infer_seconds'] <= .15
                and row['delivery']['committed']['wall_delay'] >= 1.]
    assert not failures, ('subsecond simulation contract failed', failures)
