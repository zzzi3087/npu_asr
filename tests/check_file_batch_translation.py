"""Synthetic end-to-end checks for local-file batch translation.

No microphone, NPU model, network, API key or user profile is used. The fake
transport replays provider-shaped JSON so thinking fields, truncation handling,
durable batch writes, resume, queue progress, cancellation and app routing can
be checked deterministically.
"""
import json
import os
import queue
import requests
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
folder = tempfile.TemporaryDirectory(prefix='captions-filebatch-test-')
os.environ['PARAKEET_DATA_DIR'] = str(Path(folder.name) / 'data')

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from config_io import import_configuration, validate_configuration
from domain_bias import default_domain_bias
from file_batch_translation import (BatchCancelled, BatchTranslationError, FileBatchTranslator,
    _fit_request, _json_tokens, estimate_tokens, job_from_record, plan_batches,
    provider_budgets, suspicious_flags)
from file_queue import FileQueue
from storage import History, default_settings, load_settings, save_settings
from translation import (TranslationResponseError, TruncatedTranslation,
    capability_status, parse_structured_translations, reasoning_capability,
    structured_request_payload, validate_profile)

qt = QApplication.instance() or QApplication([])


def settle(ms=40):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


def wait_until(predicate, timeout=6000):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if predicate(): return True
        settle(25)
    return predicate()


class FakeResponse:
    def __init__(self, data, status=200):
        self.status_code = status; self.data = data
    def json(self): return self.data


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler; self.calls = []
    def post(self, url, **kwargs):
        body = kwargs.get('json') or {}
        self.calls.append({'url': url, 'body': body})
        return self.handler(body)


def openai_response(translations, finish='stop'):
    return {'choices':[{'message':{'content':json.dumps({'translations':translations}, ensure_ascii=False)},
        'finish_reason':finish}]}


def payload_of(body):
    messages = body.get('messages') or []
    return json.loads(messages[-1]['content'])


def success_handler(body):
    payload = payload_of(body)
    items = payload['translate']
    return FakeResponse(openai_response([
        {'id':item['id'], 'text':'译:'+item['text']} for item in items]))


def row(ident, seq, text, start=None, end=None, status='queued', zh=''):
    start = seq * 2000 if start is None else start
    return {'id':ident, 'seq':seq, 'start_ms':start, 'end_ms':start + (end or 1500),
        'en':text, 'zh':zh, 'status':status, 'error':''}


def base_profile(**patch):
    profile = next(p for p in default_settings()['profiles'] if p['id'] == 'deepseek')
    profile = json.loads(json.dumps(profile))
    profile.update(patch)
    return profile


# 1. Conservative multilingual estimate and natural-sentence planning.
assert estimate_tokens('Hello world') >= 2
assert estimate_tokens('量子场论') >= 4
assert estimate_tokens('') == 0
long_rows = [row(f'u{i}', i + 1, ('physics sentence ' * 60).strip() + '.') for i in range(40)]
long_rows[20]['zh'] = '已翻译'; long_rows[20]['status'] = 'done'
planned = plan_batches(long_rows, base_profile(), default_domain_bias())
covered = [item['id'] for batch in planned for item in batch['items']['target']]
assert len(covered) == len(set(covered)) == 39
assert long_rows[20]['id'] not in covered
assert len(planned) >= 2
for batch in planned:
    payload = batch['items']
    before_tokens = sum(estimate_tokens(text) for text in payload['before'])
    after_tokens = sum(estimate_tokens(text) for text in payload['after'])
    visible = batch['target_tokens'] + before_tokens + after_tokens
    assert visible <= 2800, visible
    assert batch['source_tokens'] == visible
    assert batch['target_tokens'] <= 2000
    assert before_tokens <= 500 and after_tokens <= 300
    for item in payload['target']:
        assert set(item) == {'id', 'seq', 'hash', 'unit'}
        assert not any('time' in key or 'ms' in key for key in item)
middle = planned[len(planned)//2]['items']
assert middle['before'] and middle['after']
# Read-only context is stored as natural sentences, not cue ID maps.
assert all(isinstance(text, str) for text in middle['before'] + middle['after'])
assert middle['prior'] == [] or all(set(pair) == {'id','translation'} for pair in middle['prior'])
# The completed row is visible as approved prior/read-only context when adjacent.

# 2. Provider-specific thinking is explicit; unknown custom services never
# silently fall back to non-thinking.
deepseek = base_profile(reasoning='auto')
cap = reasoning_capability(deepseek)
assert cap['enabled'] and cap['effort'] == 'high'
assert cap['request_fields'] == {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high'}
url, headers, body = structured_request_payload(deepseek, 'system', '{}', 900, key='k')
assert body['thinking'] == {'type': 'enabled'} and body['max_tokens'] >= 900
assert body['reasoning_effort'] == 'high'
assert body['model'] == 'deepseek-flash'
default_budget = provider_budgets(deepseek)
assert default_budget['target'] == 2000
assert default_budget['before'] == 500 and default_budget['after'] == 300
assert default_budget['source'] == default_budget['visible'] == 2800
for mode, expected in [('low', 'low'), ('medium', 'high'), ('high', 'high'), ('max', 'max')]:
    mode_cap = reasoning_capability(dict(deepseek, reasoning=mode))
    _, _, mode_body = structured_request_payload(dict(deepseek, reasoning=mode),
        'system', '{}', 900, key='k')
    assert mode_body['thinking'] == {'type': 'enabled'}
    assert mode_body['reasoning_effort'] == expected, (mode, mode_body)
    if mode != 'medium':
        assert mode_cap['effort'] == expected
reasoner = dict(deepseek, model='deepseek-reasoner', reasoning='auto')
reasoner_cap = reasoning_capability(reasoner)
_, _, reasoner_body = structured_request_payload(reasoner, 'system', '{}', 900, key='k')
assert reasoner_cap['effort'] == 'provider-default'
assert 'thinking' not in reasoner_body and 'reasoning_effort' not in reasoner_body
off = base_profile(reasoning='off')
cap_off = reasoning_capability(off)
assert cap_off['request_fields'] == {'thinking': {'type': 'disabled'}}
# GPT-5/o-series have no request-level off switch: never claim otherwise.
openai = base_profile(id='openai', name='OpenAI', base_url='https://api.openai.com/v1',
    model='gpt-5', reasoning='medium')
cap_openai = reasoning_capability(openai)
assert cap_openai['enabled'] and cap_openai['request_fields'] == {'reasoning_effort': 'medium'}
_, _, body_openai = structured_request_payload(openai, 'system', '{}', 900, key='k')
assert body_openai['reasoning_effort'] == 'medium' and 'max_completion_tokens' in body_openai
assert 'max_tokens' not in body_openai
openai_off = dict(openai, reasoning='off')
openai_off_cap = reasoning_capability(openai_off)
assert '已明确关闭' not in openai_off_cap['status']
assert '无法' in openai_off_cap['status'] or '默认' in openai_off_cap['status']
_, _, body_openai_off = structured_request_payload(openai_off, 'system', '{}', 900, key='k')
assert 'reasoning_effort' not in body_openai_off and 'max_completion_tokens' in body_openai_off
# Older Claude models use an explicit budget; current 4.6+/5 use adaptive thinking.
anthropic = base_profile(id='anthropic', name='Anthropic', protocol='anthropic',
    base_url='https://api.anthropic.com', model='claude-sonnet-4-5', reasoning='high',
    api_version='2023-06-01')
cap_anthropic = reasoning_capability(anthropic)
assert cap_anthropic['enabled'] and cap_anthropic['budget_tokens'] == 4096
_, _, body_anthropic = structured_request_payload(anthropic, 'system', '{}', 900, key='k')
assert body_anthropic['thinking'] == {'type': 'enabled', 'budget_tokens': 4096}
assert body_anthropic['max_tokens'] > 4096
adaptive = dict(anthropic, model='claude-opus-4-6', reasoning='auto')
cap_adaptive = reasoning_capability(adaptive)
assert cap_adaptive['mechanism'] == 'adaptive' and cap_adaptive['effort'] == 'high'
_, _, body_adaptive = structured_request_payload(adaptive, 'system', '{}', 900, key='k')
assert body_adaptive['thinking'] == {'type': 'adaptive'}
assert body_adaptive['output_config'] == {'effort': 'high'} and 'budget_tokens' not in body_adaptive['thinking']
custom = base_profile(id='custom', name='Custom', base_url='https://third.example/v1',
    model='unknown-model', reasoning='auto', extra={})
assert reasoning_capability(custom)['error']
try:
    structured_request_payload(custom, 'system', '{}', 900, key='k')
    raise AssertionError('unknown custom service must not silently skip thinking')
except ValueError:
    pass
custom_off = dict(custom, reasoning='off')
assert not reasoning_capability(custom_off)['error']
assert '未验证' in capability_status(custom_off)
custom_budget = provider_budgets(custom_off)
assert custom_budget['target'] < 2000
assert 0 < custom_budget['target'] <= custom_budget['source'] <= 2800
assert custom_budget['before'] <= 500 and custom_budget['after'] <= 300
# A tighter provider buys source room for the target before optional context.
import file_batch_translation as file_batch_module
real_capability = file_batch_module.reasoning_capability
try:
    file_batch_module.reasoning_capability = lambda profile: dict(
        real_capability(profile), context_tokens=4600, max_output=2048)
    constrained_budget = provider_budgets(custom_off)
    assert constrained_budget['source'] == 952
    assert constrained_budget['target'] == 746
    assert constrained_budget['before'] == 206 and constrained_budget['after'] == 0
    assert constrained_budget['before'] + constrained_budget['target'] <= constrained_budget['source']
finally:
    file_batch_module.reasoning_capability = real_capability
custom_explicit = dict(custom, reasoning='high',
    extra={'reasoning_effort': 'high', 'temperature': 0.2})
assert reasoning_capability(custom_explicit)['enabled']
_, _, body_custom = structured_request_payload(custom_explicit, 'system', '{}', 900, key='k')
assert body_custom['reasoning_effort'] == 'high' and 'temperature' not in body_custom
try:
    structured_request_payload(dict(custom, reasoning='high'), 'system', '{}', 900, key='k')
    raise AssertionError('custom thinking without explicit payload must fail')
except ValueError:
    pass
validate_profile(deepseek)

# 3. Structured response: strict coverage, duplicates, unknown/empty IDs,
# truncation, repair and thinking blocks excluded.
protocol = 'openai'
valid = openai_response([{'id':'a','text':'甲'}, {'id':'b','text':'乙'}])
assert parse_structured_translations(protocol, valid, ['a','b']) == {'a':'甲','b':'乙'}
for entries in ([{'id':'a','text':'甲'}, {'id':'a','text':'重复'}],
                [{'id':'a','text':'甲'}, {'id':'x','text':'未知'}],
                [{'id':'a','text':'甲'}, {'id':'b','text':'   '}]):
    try:
        parse_structured_translations(protocol, openai_response(entries), ['a','b'])
        raise AssertionError('invalid batch response accepted')
    except TranslationResponseError:
        pass
try:
    parse_structured_translations(protocol, openai_response([{'id':'a','text':'甲'}], finish='length'), ['a','b'])
    raise AssertionError('truncated response accepted')
except TruncatedTranslation:
    pass
try:
    parse_structured_translations(protocol, openai_response([{'id':'a','text':'甲'}]), ['a','b'])
    raise AssertionError('missing id accepted')
except TranslationResponseError as error:
    assert error.incomplete
# Complete objects salvaged from a cut-off closing bracket are still strict.
cut = {'choices':[{'message':{'content':'{"translations":[{"id":"a","text":"甲"},{"id":"b","text":"乙"}'},
    'finish_reason':'stop'}]}
assert parse_structured_translations(protocol, cut, ['a','b']) == {'a':'甲','b':'乙'}
anthropic_data = {'stop_reason':'end_turn','content':[
    {'type':'thinking','thinking':'secret chain of thought'},
    {'type':'text','text':'{"translations":[{"id":"a","text":"甲"}]}'}]}
assert parse_structured_translations('anthropic', anthropic_data, ['a']) == {'a':'甲'}

# 4. Worker: truncation splits by natural group; duplicate IDs trigger a strict
# retry; suspicious quantity/unit flags are collected without rewriting text.
truncate_rows = [row(f't{i}', i + 1, f'Sentence number {i+1}.') for i in range(4)]
truncate_profile = base_profile()
truncate_batches = plan_batches(truncate_rows, truncate_profile)
assert len(truncate_batches) == 1 and len(truncate_batches[0]['items']['units']) == 4
state = {'calls': 0}
def truncation_handler(body):
    payload = payload_of(body); ids = [item['id'] for item in payload['translate']]
    state['calls'] += 1
    if state['calls'] == 1:
        partial = json.dumps({'translations':[{'id':ids[0],'text':'甲'}]}, ensure_ascii=False)[:-1]
        return FakeResponse({'choices':[{'message':{'content':partial},'finish_reason':'length'}]})
    return success_handler(body)
transport = FakeTransport(truncation_handler)
worker = FileBatchTranslator(transport=transport)
session_id = 's1'
record = {'session_id': session_id, 'run_id': 'r1', 'batch_id': 'b1', 'claim': 'c1',
    'items': json.dumps(truncate_batches[0]['items'], ensure_ascii=False)}
jobs = {}
# Rows are rebuilt in the same order as the synthetic transcript.
by_id = {r['id']: r for r in truncate_rows}
job = job_from_record(record, truncate_rows, truncate_profile, 'k', 'auto', '简体中文',
    default_domain_bias())
assert job
result = worker._translate(job, None)
assert set(result['translations']) == {r['id'] for r in truncate_rows}
assert state['calls'] == 3, state['calls']  # truncation + two natural halves
assert result['flags'] == []

duplicate_state = {'calls': 0}
def duplicate_handler(body):
    duplicate_state['calls'] += 1
    payload = payload_of(body); ids = [item['id'] for item in payload['translate']]
    if duplicate_state['calls'] == 1:
        entries = [{'id':ids[0], 'text':'甲'}, {'id':ids[0], 'text':'重复'}]
        return FakeResponse(openai_response(entries))
    return success_handler(body)
worker_dup = FileBatchTranslator(transport=FakeTransport(duplicate_handler))
dup_job = job_from_record(record, truncate_rows, truncate_profile, 'k', 'auto', '简体中文',
    default_domain_bias())
dup_result = worker_dup._translate(dup_job, None)
assert duplicate_state['calls'] == 2
assert set(dup_result['translations']) == {r['id'] for r in truncate_rows}

flags = suspicious_flags({'a':'The mass is 5 kg.','b':'No signal was seen.'},
    {'a':'质量是 5 克。','b':'信号被看到。'})
assert {flag['kind'] for flag in flags} == {'unit','negation'}

# One focused anchoring retry is issued for a suspect quantity/unit batch,
# and the reviewed result replaces the first response only if coverage is valid.
focus_rows = [row('f1', 1, 'The mass is 5 kg.')]
focus_batches = plan_batches(focus_rows, base_profile())
focus_record = {'session_id':'focus', 'run_id':'focus-run', 'batch_id':'focus-batch',
    'claim':'focus-claim', 'items':json.dumps(focus_batches[0]['items'], ensure_ascii=False)}
focus_job = job_from_record(focus_record, focus_rows, base_profile(), 'k', 'auto', '简体中文',
    default_domain_bias())
focus_calls = {'n': 0}
def focus_handler(body):
    focus_calls['n'] += 1
    payload = payload_of(body); ident = payload['translate'][0]['id']
    text = '质量是 5 克。' if focus_calls['n'] == 1 else '质量是 5 kg。'
    return FakeResponse(openai_response([{'id':ident, 'text':text}]))
focus_result = FileBatchTranslator(transport=FakeTransport(focus_handler))._translate(focus_job, None)
assert focus_calls['n'] == 2
assert focus_result['translations'] == {'f1':'质量是 5 kg。'}
assert focus_result['flags'] == []

# Relevant configured glossary entries and approved prior translations are
# read-only context; no definition is generated for absent terms.
glossary_rows = [row('g0', 1, 'Previous approved sentence.', zh='上一句已译。', status='done'),
    row('g1', 2, 'Schwarzschild metric and axions.')]
glossary_batch = plan_batches(glossary_rows, base_profile(), default_domain_bias())[0]['items']
assert 'Schwarzschild' in glossary_batch['glossary']
assert glossary_batch['prior'] == [{'id':'g0', 'translation':'上一句已译。'}]
assert all('source' not in pair for pair in glossary_batch['prior'])
assert glossary_batch['before'] and 'Previous approved sentence.' in glossary_batch['before'][0]
assert all('definition' not in term.lower() for term in glossary_batch['glossary'])

# Pathological single cue/context limits: never send a request that cannot fit,
# never truncate the target cue, and trim only read-only context.
huge_profile = base_profile(reasoning='off', model='conservative-model',
    base_url='https://custom.example/v1', name='Custom')
huge_rows = [row('huge', 1, '字' * 2400)]
huge_plan = plan_batches(huge_rows, huge_profile)[0]
huge_record = {'session_id':'huge', 'run_id':'huge-run', 'batch_id':'huge-batch',
    'claim':'huge-claim', 'items':json.dumps(huge_plan['items'], ensure_ascii=False)}
huge_job = job_from_record(huge_record, huge_rows, huge_profile, 'k', 'auto', '简体中文', None)
huge_transport = FakeTransport(success_handler)
try:
    FileBatchTranslator(transport=huge_transport)._translate(huge_job, None)
    raise AssertionError('over-capacity single cue must fail visibly')
except BatchTranslationError as error:
    assert '未发送请求' in str(error)
assert huge_transport.calls == []

# A single cue larger than the whole 2800-token source window cannot be split
# without breaking its ID/timestamp contract; fail before any billable request.
over_rows = [row('over', 1, '字' * 2600)]
over_plan = plan_batches(over_rows, base_profile())[0]
assert over_plan['target_tokens'] > 2800
over_record = {'session_id':'over', 'run_id':'over-run', 'batch_id':'over-batch',
    'claim':'over-claim', 'items':json.dumps(over_plan['items'], ensure_ascii=False)}
over_job = job_from_record(over_record, over_rows, base_profile(), 'k', 'auto', '简体中文', None)
over_transport = FakeTransport(success_handler)
try:
    FileBatchTranslator(transport=over_transport)._translate(over_job, None)
    raise AssertionError('source-window overrun must fail visibly')
except BatchTranslationError as error:
    assert '单批源文预算' in str(error) and '未发送请求' in str(error)
assert over_transport.calls == []

trim_rows = [row('t1', 1, 'Short target sentence.')]
trim_plan = plan_batches(trim_rows, huge_profile)[0]
trim_record = {'session_id':'trim', 'run_id':'trim-run', 'batch_id':'trim-batch',
    'claim':'trim-claim', 'items':json.dumps(trim_plan['items'], ensure_ascii=False)}
trim_job = job_from_record(trim_record, trim_rows, huge_profile, 'k', 'auto', '简体中文', None)
trim_payload = {'source_language':'auto', 'target_language':'简体中文',
    'glossary':['term'+str(i) for i in range(30)], 'topic':'字'*5000,
    'context_before':['字'*8000], 'context_after':['字'*8000], 'prior_translated':[],
    'translate':[{'id':'t1','text':'Short target sentence.'}]}
fitted, fitted_system = _fit_request(trim_job, trim_payload, False, False)
assert fitted['translate'] == [{'id':'t1','text':'Short target sentence.'}]
cap_trim = trim_job['budgets']['capability']
allowed = cap_trim['context_tokens'] - cap_trim['max_output'] - 256
assert _json_tokens(fitted) + estimate_tokens(fitted_system) <= allowed + 1
assert len(fitted['context_before']) + len(fitted['context_after']) < 2

# Outbound mock payloads: the planner's 500/2000/300 source caps must survive
# into the actual request JSON for start, middle, end, missing-context and
# constrained-provider cases. Context is never padded and prior translations
# carry id+translation only (their source is already in context_before).
def outbound_payloads(rows, profile, domain_bias=None):
    batches = plan_batches(rows, profile, domain_bias)
    transport = FakeTransport(success_handler)
    worker = FileBatchTranslator(transport=transport)
    for batch in batches:
        record = {'session_id':batch['batch_id'], 'run_id':'run', 'batch_id':batch['batch_id'],
            'claim':'claim', 'items':json.dumps(batch['items'], ensure_ascii=False)}
        job = job_from_record(record, rows, profile, 'k', 'auto', '简体中文', domain_bias)
        worker._translate(job, None)
    return batches, [payload_of(call['body']) for call in transport.calls]

payload_rows = [row(f'p{i}', i + 1,
    f'Outbound payload sentence {i} ' + ('context word ' * 55).strip() + '.')
    for i in range(60)]
payload_batches, sent = outbound_payloads(payload_rows, base_profile())
assert len(payload_batches) >= 3 and len(sent) == len(payload_batches)
for batch, request in zip(payload_batches, sent):
    target_tokens = sum(estimate_tokens(item['text']) for item in request['translate'])
    before_tokens = sum(estimate_tokens(text) for text in request['context_before'])
    after_tokens = sum(estimate_tokens(text) for text in request['context_after'])
    assert target_tokens <= 2000
    assert before_tokens <= 500 and after_tokens <= 300
    assert target_tokens + before_tokens + after_tokens <= 2800
    assert request['translate'] and all(item['id'] and item['text'] for item in request['translate'])
    assert all(str(text).strip() for text in request['context_before'] + request['context_after'])
    assert all(set(pair) == {'id', 'translation'} for pair in request['prior_translated'])
    assert all('source' not in pair for pair in request['prior_translated'])
start_request, middle_request, end_request = sent[0], sent[len(sent)//2], sent[-1]
assert start_request['context_before'] == [] and start_request['prior_translated'] == []
assert middle_request['context_before'] and middle_request['context_after']
assert end_request['context_after'] == []

# Missing context on a one-row file is represented as empty lists, not padding.
missing_batches, missing_sent = outbound_payloads(
    [row('solo', 1, 'Standalone sentence.')], base_profile())
assert len(missing_batches) == len(missing_sent) == 1
assert missing_sent[0]['context_before'] == []
assert missing_sent[0]['context_after'] == []
assert missing_sent[0]['prior_translated'] == []

# Approved prior translations from the preceding window use id+translation,
# and no extra source is appended outside the selected context.
prior_rows = [row('prior0', 1, 'Approved preceding sentence.', zh='已批准的前句。', status='done'),
    row('prior1', 2, 'Target sentence to translate.')]
prior_batches, prior_sent = outbound_payloads(prior_rows, base_profile())
assert prior_sent[0]['prior_translated'] == [{'id':'prior0', 'translation':'已批准的前句。'}]
assert 'Approved preceding sentence.' in prior_sent[0]['context_before'][0]

# A translated row outside the selected 500-token preceding window must not
# reappear in prior_translated (which would be extra source/payload).
overflow_rows = [
    row('far', 1, 'Far approved sentence ' + ('preceding context words ' * 35).strip() + '.',
        zh='远端旧译。', status='done')]
for index in range(2, 6):
    overflow_rows.append(row(f'mid{index}', index,
        f'Mid approved sentence {index} ' + ('preceding context words ' * 35).strip() + '.',
        status='done'))
overflow_rows.append(row('goal', 6, 'Target sentence for the overflow check.'))
overflow_batch, overflow_sent = outbound_payloads(overflow_rows, base_profile())
assert len(overflow_batch[0]['items']['before']) == 3
assert all('Far approved sentence' not in text for text in overflow_batch[0]['items']['before'])
assert overflow_batch[0]['items']['prior'] == []
assert overflow_sent[0]['prior_translated'] == []

# Constrained provider: source room shrinks within the caps and the outbound
# payload never reports the normal 2800-token window as available.
import file_batch_translation as file_batch_module
real_capability = file_batch_module.reasoning_capability
try:
    file_batch_module.reasoning_capability = lambda profile: dict(
        real_capability(profile), context_tokens=5000, max_output=2048)
    constrained_profile = base_profile(reasoning='off', model='conservative-model',
        base_url='https://custom.example/v1', name='Custom')
    constrained_budget = provider_budgets(constrained_profile)
    assert constrained_budget['source'] == 1352
    assert constrained_budget['target'] == 746
    assert constrained_budget['before'] == 500 and constrained_budget['after'] == 106
    constrained_batches, constrained_sent = outbound_payloads(payload_rows, constrained_profile)
    for request in constrained_sent:
        target_tokens = sum(estimate_tokens(item['text']) for item in request['translate'])
        before_tokens = sum(estimate_tokens(text) for text in request['context_before'])
        after_tokens = sum(estimate_tokens(text) for text in request['context_after'])
        assert target_tokens <= constrained_budget['target']
        assert before_tokens <= 500 and after_tokens <= 300
        assert target_tokens + before_tokens + after_tokens <= constrained_budget['source']
finally:
    file_batch_module.reasoning_capability = real_capability

# stop() must end the QThread even when the transport blocks; the abandoned
# daemon HTTP call cannot keep Qt alive or force terminate().
block_started = threading.Event(); block_release = threading.Event()
def blocked(body):
    block_started.set(); block_release.wait(6); return success_handler(body)
block_worker = FileBatchTranslator(transport=FakeTransport(blocked))
block_worker.start(); block_worker.enqueue(job)
assert wait_until(block_started.is_set, 3000)
block_worker.stop()
assert wait_until(lambda: not block_worker.isRunning(), 3000)
block_release.set(); block_worker.wait(2000)
assert not block_worker.isRunning()
assert wait_until(lambda: not any(t.name == 'file-batch-http' for t in threading.enumerate()), 3000)

# Shared single billable HTTP slot: many canceled waiters produce exactly one
# active request; after it releases, a new run proceeds; errors clear the slot;
# distinct translators serialize fairly.
class SlotTransport:
    def __init__(self):
        self.lock = threading.Lock(); self.calls = 0; self.active = 0
        self.started = threading.Event(); self.release = threading.Event()

    def post(self, url, **kwargs):
        with self.lock:
            self.calls += 1; self.active += 1
        self.started.set()
        self.release.wait(10)
        with self.lock:
            self.active -= 1
        return FakeResponse(openai_response([{'id':'s1', 'text':'译文'}]))

slot_transport = SlotTransport()
slot_worker = FileBatchTranslator(transport=slot_transport)
cancels = [threading.Event() for _ in range(6)]
outcomes = []
def slot_call(cancel):
    try:
        slot_worker._post_cancellable('https://example.test', {}, {}, (1, 1), cancel)
        outcomes.append('completed')
    except BaseException as error:
        outcomes.append(type(error).__name__)
slot_threads = [threading.Thread(target=slot_call, args=(cancel,)) for cancel in cancels]
for thread in slot_threads: thread.start()
assert wait_until(slot_transport.started.is_set, 3000)
for cancel in cancels: cancel.set()
for thread in slot_threads: thread.join(3)
assert not any(thread.is_alive() for thread in slot_threads)
assert outcomes == ['BatchCancelled'] * 6, outcomes
with slot_transport.lock:
    assert slot_transport.calls == 1 and slot_transport.active == 1
assert len([t for t in threading.enumerate() if t.name == 'file-batch-http']) == 1
slot_transport.release.set()
assert wait_until(lambda: not any(t.name == 'file-batch-http' for t in threading.enumerate()), 4000)
slot_worker._post_cancellable('https://example.test', {}, {}, (1, 1), threading.Event())
with slot_transport.lock:
    assert slot_transport.calls == 2 and slot_transport.active == 0

class ErrorTransport:
    def post(self, url, **kwargs): raise requests.ConnectionError('synthetic')
error_transport = ErrorTransport()
try:
    FileBatchTranslator(transport=error_transport)._post_cancellable(
        'https://example.test', {}, {}, (1, 1), threading.Event())
    raise AssertionError('synthetic error must propagate')
except requests.ConnectionError:
    pass
assert wait_until(lambda: not any(t.name == 'file-batch-http' for t in threading.enumerate()), 3000)
class EmptyTransport:
    def __init__(self): self.calls = 0
    def post(self, url, **kwargs):
        self.calls += 1
        return FakeResponse(openai_response([{'id':'x', 'text':'y'}]))
success_transport = EmptyTransport()
FileBatchTranslator(transport=success_transport)._post_cancellable(
    'https://example.test', {}, {}, (1, 1), threading.Event())
assert success_transport.calls == 1

fair_transport = SlotTransport()
first_worker = FileBatchTranslator(transport=fair_transport)
second_worker = FileBatchTranslator(transport=fair_transport)
fair_results = []
first_thread = threading.Thread(target=lambda: fair_results.append(
    first_worker._post_cancellable('https://example.test', {}, {}, (1, 1), threading.Event())))
first_thread.start()
assert wait_until(fair_transport.started.is_set, 3000)
second_thread = threading.Thread(target=lambda: fair_results.append(
    second_worker._post_cancellable('https://example.test', {}, {}, (1, 1), threading.Event())))
second_thread.start()
settle(150)
with fair_transport.lock:
    assert fair_transport.calls == 1, fair_transport.calls
fair_transport.release.set()
first_thread.join(4); second_thread.join(4)
assert not first_thread.is_alive() and not second_thread.is_alive()
with fair_transport.lock:
    assert fair_transport.calls == 2
assert len(fair_results) == 2

# 5. Durable guarded writes, stale claims, resume and errors in status only.
with tempfile.TemporaryDirectory() as directory:
    db = History(path=Path(directory) / 'history.sqlite3')
    settings = default_settings()
    sid = db.create_session('file fixture', settings['profiles'][0], kind='file')
    for index in range(20):
        db.add(sid, ('long source sentence ' * 40).strip() + f' {index}.', index * 2000,
            index * 2000 + 1600, 'queued',
            metadata={'source_language':'English','target_language':'简体中文'})
    db.finish(sid)
    source_rows = db.rows(sid)
    original_times = [(r['id'], r['start_ms'], r['end_ms']) for r in source_rows]
    batches = plan_batches(source_rows, base_profile(), default_domain_bias())
    assert len(batches) >= 2
    revision = db.translation_revision(sid)
    run_id = db.create_batch_run(sid, revision, base_profile(), '简体中文', 'auto', batches)
    claimed = db.claim_batch(sid, run_id, batches[0]['batch_id'])
    assert claimed and claimed['claim']
    targets = [item['id'] for item in json.loads(claimed['items'])['target']]
    wrong_revision = db.apply_batch_translations(sid, run_id, batches[0]['batch_id'],
        claimed['claim'], 'wrong-revision', {ident:'译文' for ident in targets})
    assert not wrong_revision['ok']
    wrong_claim = db.apply_batch_translations(sid, run_id, batches[0]['batch_id'],
        'wrong-claim', revision, {ident:'译文' for ident in targets})
    assert not wrong_claim['ok']
    applied = db.apply_batch_translations(sid, run_id, batches[0]['batch_id'],
        claimed['claim'], revision, {ident:'译文' for ident in targets}, [{'kind':'quantity'}])
    assert applied['ok']
    duplicate = db.apply_batch_translations(sid, run_id, batches[0]['batch_id'],
        claimed['claim'], revision, {ident:'重复覆盖' for ident in targets})
    assert not duplicate['ok']  # a late/duplicate result can never overwrite
    assert all(db.segment(ident)['zh'] == '译文' for ident in targets)
    assert [(r['id'], r['start_ms'], r['end_ms']) for r in db.rows(sid)] == original_times
    assert all(db.segment(ident)['zh'] == '译文' for ident in targets)
    assert db.batch_flag_count(sid, run_id) >= 1
    db.close()
    # Restart: completed batch is preserved; only the outstanding batch resumes.
    db = History(path=Path(directory) / 'history.sqlite3', recover=False)
    resumable = db.resumable_batch_runs()
    assert [run['session_id'] for run in resumable] == [sid]
    pending = db.resume_batch_run(sid, run_id)
    assert len(pending) == len(batches) - 1
    done_rows = {r['id']: r for r in db.rows(sid)}
    assert all(done_rows[ident]['zh'] == '译文' for ident in targets)
    second = pending[0]
    claim2 = db.claim_batch(sid, run_id, second['batch_id'])
    targets2 = [item['id'] for item in json.loads(claim2['items'])['target']]
    failed = db.fail_batch(sid, run_id, second['batch_id'], claim2['claim'], 'fixture failure')
    assert failed['ok']
    assert all(db.segment(ident)['zh'] == '' and db.segment(ident)['status'] == 'failed'
        for ident in targets2)
    assert all(db.segment(ident)['error'] == 'fixture failure' for ident in targets2)
    db.close()

# 6. Imported/saved schema accepts explicit reasoning modes only.
with tempfile.TemporaryDirectory() as directory:
    candidate = default_settings()
    candidate['profiles'][0]['reasoning'] = 'max'
    validated = validate_configuration(candidate)
    assert validated['profiles'][0]['reasoning'] == 'max'
    bad = json.loads(json.dumps(candidate))
    bad['profiles'][0]['reasoning'] = 'sometimes'
    try:
        validate_configuration(bad)
        raise AssertionError('invalid reasoning mode accepted')
    except ValueError:
        pass
    save_settings(candidate, directory)
    assert load_settings(directory)['profiles'][0]['reasoning'] == 'max'
    path = Path(directory) / 'config.json'
    from config_io import export_configuration
    export_configuration(candidate, path)
    assert import_configuration(path)['profiles'][0]['reasoning'] == 'max'

# 7. Queue translation state persists independently of ASR activity.
with tempfile.TemporaryDirectory() as directory:
    q = FileQueue(path=Path(directory) / 'queue.json')
    fixture = Path(directory) / 'fixture.wav'; fixture.write_bytes(b'fake')
    q.set_available(False)
    assert q.add([fixture], default_settings()) == 1
    q.jobs[0]['session'] = 'session-fixture'; q.save()
    q.translation_update('session-fixture', {'translation_status':'translating',
        'translation_done':2,'translation_total':5,'translation_percent':40})
    restored = FileQueue(path=q.path)
    assert restored.jobs[0]['translation_status'] == 'translating'
    assert restored.jobs[0]['translation_done'] == 2
    assert restored.jobs[0]['translation_percent'] == 40

# 8. App routing: file commits never enqueue ordinary per-cue translation, and
# completion drives the batch worker end-to-end. Two sessions are processed by
# one serial worker; cancellation keeps completed batches and status visible.
import app as app_module
app_module.get_key = lambda profile: 'fixture-key'
window = app_module.Window(start_workers=False)
try:
    # Replace the idle real threads with a fake ordinary translator and a batch
    # worker using the synthetic transport.
    window.translator.stop(); window.translator.wait(2000)
    window.file_batches.stop(); window.file_batches.wait(2000)
    class FakeTranslator:
        def __init__(self): self.jobs = queue.Queue(maxsize=32)
        def enqueue(self, *job): self.jobs.put_nowait(job)
        def isRunning(self): return False
        def cancel_queued(self, *args, **kwargs): return []
        def stop(self): pass
    ordinary = FakeTranslator(); window.translator = ordinary
    transport = FakeTransport(success_handler)
    window.file_batches = FileBatchTranslator(transport=transport)
    window.file_batches.batch_done.connect(window.on_file_batch_done)
    window.file_batches.batch_failed.connect(window.on_file_batch_failed)
    window.file_batches.start()
    window.worker = types.SimpleNamespace(engine_key='fixture', isRunning=lambda: False,
        file_cancel=threading.Event(), download_cancel=threading.Event(),
        stop_capture=threading.Event(), quit_requested=threading.Event(),
        commands=queue.Queue(), update_gain=lambda *_: None)
    window.settings.update(translate=True, target_language='简体中文')

    def run_file_job(ident, texts):
        profile = window.profile()
        sid = window.history.create_session('文件 · '+ident, profile, kind='file')
        job = {'id':ident, 'path':ident+'.wav', 'name':ident+'.wav',
            'options':{'asr_model':'parakeet','source_language':'English'},
            'status':'transcribing','percent':50,'stage_percent':50,'session':sid,
            'error':'','analysis':None}
        window.file_queue.jobs.append(job); window.file_queue.active=ident
        window.current_session = window.view_session = sid
        for index, text in enumerate(texts):
            window.on_file_event({'id':ident, 'kind':'committed',
                'value':{'text':text, 'audio_start_ms':index*2000, 'audio_end_ms':index*2000+1500}})
        assert ordinary.jobs.empty(), 'file ASR must not use per-cue ordinary translation'
        window.on_file_event({'id':ident, 'kind':'done', 'value':{'status':'completed'}})
        return sid, job

    sid1, job1 = run_file_job('fixture-one', ['First source sentence.', 'Second source sentence.',
        'Third source sentence.'])
    raw1 = {r['id']: (r['en'], r['start_ms'], r['end_ms']) for r in window.history.rows(sid1)}
    assert wait_until(lambda: all(r['zh'] for r in window.history.rows(sid1)), 6000)
    assert ordinary.jobs.empty()
    rows1 = window.history.rows(sid1)
    assert all(r['status'] == 'done' and r['zh'].startswith('译:') for r in rows1)
    assert {r['id']: (r['en'], r['start_ms'], r['end_ms']) for r in rows1} == raw1
    assert job1['translation_status'] == 'completed' and job1['translation_percent'] == 100
    assert window.history.session(sid1)['kind'] == 'file'

    # A second finished file joins the same serial worker and continues the queue.
    sid2, job2 = run_file_job('fixture-two', ['Alpha source sentence.', 'Beta source sentence.'])
    assert wait_until(lambda: all(r['zh'] for r in window.history.rows(sid2)), 6000)
    assert job2['translation_status'] == 'completed'

    # Two longer files are started before the first finishes. A bounded
    # per-run window keeps both runs progressing through one serial worker.
    concurrent_texts = [('Concurrent source sentence ' * 30).strip() + f' {index}.'
        for index in range(12)]
    sidc1, jobc1 = run_file_job('fixture-concurrent-a', concurrent_texts)
    sidc2, jobc2 = run_file_job('fixture-concurrent-b', concurrent_texts)
    assert wait_until(lambda: all(r['zh'] for r in window.history.rows(sidc1)) and
        all(r['zh'] for r in window.history.rows(sidc2)), 12000)
    assert jobc1['translation_status'] == 'completed' and jobc2['translation_status'] == 'completed'

    # Simulated interruption: a durable run is marked interrupted, then the
    # startup resume path picks it up without re-planning or losing progress.
    sid4 = window.history.create_session('文件 · resume', window.profile(), kind='file')
    for index in range(3):
        window.history.add(sid4, f'Resume source sentence {index}.', index * 2000,
            index * 2000 + 1500, 'queued',
            metadata={'source_language':'English','target_language':'简体中文'})
    window.history.finish(sid4)
    resume_profile = window.profile()
    resume_batches = plan_batches(window.history.rows(sid4), resume_profile,
        window.settings.get('domain_bias'))
    resume_revision = window.history.translation_revision(sid4)
    resume_run = window.history.create_batch_run(sid4, resume_revision, resume_profile,
        '简体中文', resume_profile.get('reasoning','auto'), resume_batches)
    window.history.interrupt_batch_run(sid4, resume_run)
    window.resume_file_batch_translations()
    assert wait_until(lambda: all(r['zh'] for r in window.history.rows(sid4)), 6000)
    assert window.history.batch_run(sid4)['status'] == 'completed'

    # Cancellation while a request is in flight: completed work stays, the
    # in-flight result cannot be written, and status is recorded not in zh.
    started = threading.Event(); release = threading.Event()
    def blocking_handler(body):
        started.set(); release.wait(5)
        return success_handler(body)
    transport.handler = blocking_handler
    sid3, job3 = run_file_job('fixture-three', [('Long source sentence ' * 20).strip() + '.'])
    assert wait_until(started.is_set, 4000)
    assert window.cancel_file_translation(sid3)
    release.set()
    assert wait_until(lambda: job3.get('translation_status') == 'cancelled', 4000)
    settle(150)
    assert all(not r['zh'] for r in window.history.rows(sid3))
    assert window.history.batch_run(sid3)['status'] == 'cancelled'

    # Retry after cancellation resumes pending batches instead of per-cue calls.
    transport.handler = success_handler
    assert window.start_file_translation(sid3, retry_failed=True)
    assert wait_until(lambda: all(r['zh'] for r in window.history.rows(sid3)), 6000)
    assert job3['translation_status'] == 'completed'
    assert ordinary.jobs.empty()
    window.batch_active_runs.clear()
finally:
    window.request_quit()
    if not wait_until(lambda: window._closed, 5000):
        window.close()

print(json.dumps({'planner_unique_coverage': True, 'budget_trimmed': True,
    'source_window_500_2000_300': True, 'outbound_payload_caps': True,
    'prior_id_translation_only': True, 'constrained_provider_shrinks_context': True,
    'oversize_source_window_rejected': True,
    'natural_readonly_context': True, 'thinking_explicit_provider_fields': True,
    'adaptive_and_legacy_claude': True, 'openai_off_accurate': True,
    'custom_explicit_mechanism': True, 'unknown_custom_never_silent': True,
    'strict_id_coverage': True, 'over_capacity_no_request': True,
    'readonly_context_trimmed': True, 'blocked_shutdown_exits': True,
    'shared_single_http_slot': True, 'fair_serial_distinct_files': True,
    'deepseek_explicit_effort': True, 'max_mode_schema': True,
    'truncation_split': True, 'duplicate_strict_retry': True,
    'focused_anchor_retry': True, 'relevant_glossary_and_prior': True,
    'quantity_unit_negation_flags': True, 'stale_duplicate_guarded': True,
    'timestamps_preserved': True, 'resume_preserves_completed': True,
    'errors_in_status_not_rows': True, 'reasoning_schema_roundtrip': True,
    'queue_progress_persists': True, 'file_never_per_cue': True,
    'end_to_end_two_files': True, 'two_concurrent_files': True,
    'cancel_and_retry': True}, ensure_ascii=False))
