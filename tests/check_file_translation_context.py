"""Offline integration regressions for repair scope, context and historical review.

Real planner, HTTP serialization/parser, SQLite commits and application routing;
only the provider transport and unrelated window presentation are substituted.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
TEMP = tempfile.TemporaryDirectory(prefix='file-context-', ignore_cleanup_errors=True)
os.environ['PARAKEET_DATA_DIR'] = str(Path(TEMP.name) / 'data')
from PySide6.QtWidgets import QApplication, QWidget
from domain_bias import default_domain_bias
from file_batch_translation import FileBatchTranslator, plan_batches, job_from_record
from file_review_dialog import FileReviewDialog
from storage import History, default_settings
import app

qt = QApplication.instance() or QApplication([])
PROFILE = copy.deepcopy(default_settings()['profiles'][0])


class Response:
    status_code = 200
    def __init__(self, values):
        self.values = values
    def json(self):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
            {'translations': [{'id': key, 'text': value} for key, value in self.values.items()]})}}]}


class Transport:
    def __init__(self, handler):
        self.handler = handler
        self.payloads = []
        self.systems = []
    def post(self, url, **kwargs):
        assert url.startswith('https://api.deepseek.com/')
        body = kwargs['json']
        payload = json.loads(body['messages'][-1]['content'])
        self.payloads.append(payload)
        self.systems.append(body['messages'][0]['content'])
        return Response(self.handler(payload, len(self.payloads)))


def source_rows(texts):
    return [dict(id=f'cue-{i}', seq=i + 1, en=text, zh='', status='queued',
                 start_ms=i * 1000, end_ms=i * 1000 + 900) for i, text in enumerate(texts)]


def make_job(rows):
    batch, = plan_batches(rows, PROFILE)
    return job_from_record(dict(batch, session_id='fixture', run_id='run', claim='claim'),
                           rows, PROFILE, 'fixture-key', 'auto', '简体中文')


def test_missing_only():
    rows = source_rows(['An axion moves', 'through the detector.', 'The signal remains.'])
    def handler(payload, count):
        ids = [row['id'] for row in payload['translate']]
        if count == 1:
            assert ids == ['cue-0', 'cue-1', 'cue-2']
            return {'cue-0': '轴子运动', 'cue-2': '信号仍在。'}
        assert ids == ['cue-1'], ids
        return {'cue-1': '穿过探测器。'}
    transport = Transport(handler)
    result = FileBatchTranslator(transport=transport)._translate(make_job(rows), None)
    assert result['translations'] == {'cue-0': '轴子运动', 'cue-1': '穿过探测器。', 'cue-2': '信号仍在。'}
    assert result['requests'] == 2 and not result['flags']
    assert transport.payloads[0]['sentence_groups'] == [['cue-0', 'cue-1']]
    assert all(row['zh'] == '' for row in rows), 'raw inputs mutated'


def test_focused_only():
    rows = source_rows(['The first observation.', 'There are 3 kg.', 'The final observation.'])
    def handler(payload, count):
        ids = [row['id'] for row in payload['translate']]
        if count == 1:
            return {'cue-0': '首次观测。', 'cue-1': '有 5 千克。', 'cue-2': '最后观测。'}
        assert ids == ['cue-1'], ids
        context = payload.get('cue_context', [])
        assert {'for': 'cue-1', 'before': rows[0]['en']} in context
        assert {'for': 'cue-1', 'after': rows[2]['en']} in context
        return {'cue-1': '有 3 千克。'}
    transport = Transport(handler)
    result = FileBatchTranslator(transport=transport)._translate(make_job(rows), None)
    assert result['requests'] == 2 and not result['flags']
    assert result['translations'] == {'cue-0': '首次观测。', 'cue-1': '有 3 千克。', 'cue-2': '最后观测。'}
    assert '仅提供' in transport.systems[1]


class Sink:
    """An unstarted real worker supplies a deterministic application queue."""
    def __init__(self, transport):
        self.worker = FileBatchTranslator(transport=transport)
        self.jobs = self.worker.jobs
    def enqueue(self, job):
        self.jobs.put_nowait(job)
        return True
    def complete_next(self, window):
        job = self.jobs.get_nowait()
        result = self.worker._translate(job, None)
        self.jobs.task_done()
        event = {key: job[key] for key in ('session_id', 'run_id', 'batch_id', 'claim')}
        event.update(result)
        window.on_file_batch_done(event)
        return job, result


class Harness(QWidget):
    # Exercise production routing without initializing audio, trays or live workers.
    start_file_translation = app.Window.start_file_translation
    _enqueue_file_batches = app.Window._enqueue_file_batches
    _fill_batch_queues = app.Window._fill_batch_queues
    on_file_batch_done = app.Window.on_file_batch_done
    request_file_cue_translation = app.Window.request_file_cue_translation
    finish_review_cue = app.Window.finish_review_cue
    review_cue_saved = app.Window.review_cue_saved
    def __init__(self, history, transport):
        super().__init__()
        self.history = history
        self.settings = default_settings()
        self.file_batches = Sink(transport)
        self.batch_contexts = {}
        self.batch_active_runs = {}
        self.review_requests = {}
        self.review_dialogs = {}
        self.closing = self._closed = False
        self.messages = []
        self.file_queue = type('Queue', (), {'translation_update': lambda *a: None})()
    def profile(self): return PROFILE
    def update_file_translation_job(self, *args): pass
    def set_translation_status(self, *args): pass
    def schedule_sessions_refresh(self): pass
    def refresh_review_cues(self, *args): pass
    def render(self): pass
    def notify(self, text): self.messages.append(text)
    def cancel_review_cue(self, *args):
        # Leave an already-running request registered to verify snapshot rejection.
        pass


def test_first_time_context_and_review():
    history = History(Path(TEMP.name) / 'history.db')
    transport = Transport(lambda payload, count: {
        row['id']: ('旧参考译名。' if row['text'].startswith('Schwarzschild') else '观测结果。')
        for row in payload['translate']})
    window = Harness(history, transport)
    domain = default_domain_bias()
    domain['translation_enabled'] = True
    domain['profiles'][0]['preferred_names'] = {'简体中文': {'Schwarzschild': '施瓦西'}}
    window.settings['domain_bias'] = domain
    sid = history.create_session('Historical local file', PROFILE, kind='file')
    # A 2300-token early sentence forces a separate batch, followed by a short
    # preceding sentence which fits the following batch's 500-token context.
    texts = [' '.join(['Observation'] * 1450) + '.', 'Schwarzschild observes', 'the detector.',
             ' '.join(['Signal'] * 220) + '.', 'Schwarzschild follows the result.']
    ids = [history.add(sid, text, i * 1000, i * 1000 + 900) for i, text in enumerate(texts)]
    history.finish(sid)
    before = {row['id']: (row['en'], row['seq'], row['start_ms'], row['end_ms']) for row in history.rows(sid)}
    with patch.object(app, 'get_key', return_value='fixture-key'):
        assert window.start_file_translation(sid)
        run = history.batch_run(sid)
        assert run['batch_count'] >= 2
        assert window.file_batches.jobs.qsize() == 1, 'later batches must wait for commits'
        while not window.file_batches.jobs.empty():
            window.file_batches.complete_next(window)
        assert history.batch_run(sid)['status'] == 'completed'
        assert any([ids[1], ids[2]] in payload['sentence_groups'] for payload in transport.payloads)
        assert any(payload.get('preferred_names') == [{'source': 'Schwarzschild', 'preferred': '施瓦西'}]
                   for payload in transport.payloads)
        assert any('优先于参考译文' in system for system in transport.systems)
        prior_pairs = [pair for payload in transport.payloads for pair in payload['prior_translated']]
        assert any(pair['id'] in ids[1:4] for pair in prior_pairs), prior_pairs
        assert all(pair['translation'] == history.segment(pair['id'])['zh'] for pair in prior_pairs)
        assert any(payload.get('preferred_names') and
                   {'id': ids[1], 'translation': '旧参考译名。'} in payload['prior_translated']
                   for payload in transport.payloads), 'preferred name and conflicting prior must coexist on wire'
        # A finished historical file remains reviewable after reopening SQLite.
        history.close()
        history = History(Path(TEMP.name) / 'history.db')
        window.history = history
        ident = ids[2]
        flag = {'id': ident, 'kind': 'quantity', 'source': '3', 'target': '5'}
        with history.db:
            history.db.execute('UPDATE translation_batches SET flags=? WHERE session_id=? AND ordinal=0',
                               (json.dumps([flag]), sid))
        dialog = FileReviewDialog(window, sid)
        window.review_dialogs[sid] = dialog
        assert dialog.row['id'] == ident and dialog.retry.isEnabled()
        sent_before = len(transport.payloads)
        dialog.retranslate()
        assert window.file_batches.jobs.qsize() == 1
        window.file_batches.complete_next(window)
        assert len(transport.payloads) == sent_before + 1
        assert [row['id'] for row in transport.payloads[-1]['translate']] == [ident]
        assert history.segment(ident)['status'] == 'done'
        assert {row['id']: (row['en'], row['seq'], row['start_ms'], row['end_ms']) for row in history.rows(sid)} == before
        # Start another retry and save a manual edit before its result arrives.
        with history.db:
            history.db.execute('UPDATE translation_batches SET flags=? WHERE session_id=? AND ordinal=0',
                               (json.dumps([flag]), sid))
        dialog.reload_translation()
        dialog.retranslate()
        assert window.file_batches.jobs.qsize() == 1
        dialog.edit.setPlainText('人工核对并修正。')
        dialog.save_edit()
        assert history.segment(ident)['zh'] == '人工核对并修正。'
        window.file_batches.complete_next(window)
        assert history.segment(ident)['zh'] == '人工核对并修正。'
        assert any('忽略旧' in message for message in window.messages)
        assert not history.review_cues(sid)
        assert {row['id']: (row['en'], row['seq'], row['start_ms'], row['end_ms']) for row in history.rows(sid)} == before
        persisted = History(Path(TEMP.name) / 'history.db')
        assert persisted.segment(ident)['zh'] == '人工核对并修正。'
        persisted.close()
        dialog.close()
    window.close()
    history.close()


if __name__ == '__main__':
    try:
        test_missing_only()
        test_focused_only()
        test_first_time_context_and_review()
        print(json.dumps(dict(missing_ids_only=True, focused_flagged_ids_only=True,
            sentence_groups_on_wire=True, preferred_names_priority_on_wire=True,
            newly_committed_prior_translations_on_wire=True, historical_single_cue_retry=True,
            original_ids_and_timestamps_retained=True, stale_review_result_discarded=True), indent=2))
    finally:
        TEMP.cleanup()
