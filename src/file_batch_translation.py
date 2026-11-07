"""Bounded, durable batch translation for completed local-file transcripts.

Planning is deterministic over the raw cue order. A batch carries only target
cue IDs; surrounding natural sentences and approved prior translations are
read-only context. No timing values are ever sent to or accepted from a model.
"""
import json
import math
import queue
import re
import threading
import time
import uuid
from collections import Counter

import requests
from PySide6.QtCore import QThread, Signal

from domain_bias import active_domain
from storage import batch_fingerprint, text_fingerprint
from translation import (TranslationResponseError, TruncatedTranslation,
    http_error_message, parse_structured_translations, reasoning_capability,
    structured_request_payload)

# Approved batch budgets. Reasoning/context/output limits shrink these values
# for conservative custom providers.
VISIBLE_SOURCE_TOKENS = 5000
TARGET_SOURCE_TOKENS = 2000
SIDE_CONTEXT_TOKENS = 1500
MAX_REQUESTS_PER_BATCH = 5
MAX_STRICT_RETRIES = 1
MAX_SPLIT_DEPTH = 2
RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504, 529}


class _HttpSlot:
    """One real billable HTTP request at a time across all batch translators.

    Waiters are FIFO and cooperative: a canceled waiter leaves the queue and
    never starts a helper. The helper releases the slot only after the socket
    call returns/fails, so a canceled in-flight request keeps the next run
    waiting instead of charging another provider request.
    """
    def __init__(self):
        self._condition = threading.Condition(threading.Lock())
        self._queue = []
        self._next_ticket = 0
        self._in_flight = False

    @staticmethod
    def _stopped(cancel, shutdown):
        return shutdown.is_set() or (cancel is not None and cancel.is_set())

    def acquire(self, cancel, shutdown):
        ticket = None
        with self._condition:
            while True:
                if self._stopped(cancel, shutdown):
                    if ticket is not None and ticket in self._queue:
                        self._queue.remove(ticket)
                        self._condition.notify_all()
                    raise BatchCancelled('批量翻译已取消')
                if ticket is None:
                    ticket = self._next_ticket
                    self._next_ticket += 1
                    self._queue.append(ticket)
                if not self._in_flight and self._queue and self._queue[0] == ticket:
                    self._queue.pop(0)
                    self._in_flight = True
                    return
                self._condition.wait(0.2)

    def release(self):
        with self._condition:
            self._in_flight = False
            self._condition.notify_all()


_HTTP_SLOT = _HttpSlot()

_CJK_RE = re.compile(r'[぀-ヿ㐀-䶿一-鿿가-힯฀-๿]')
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_SENTENCE_END_RE = re.compile(r'[.!?。！？…](?:["\'”’)\]}」』〉》]*)$')
_NUMBER_RE = re.compile(r'(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?%?')
_UNIT_RE = re.compile(
    r'(?<![A-Za-z])(?:%|‰|°C|°F|km/s|km/h|km|cm|mm|µm|nm|kg|mg|µg|'
    r'm/s|m|Hz|kHz|MHz|GHz|eV|keV|MeV|GeV|TeV|K|Pa|hPa|atm|L|mL|min|h|s)(?![A-Za-z])', re.I)
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|none|neither|nor|cannot|can't|don't|doesn't|didn't|isn't|aren't|"
    r"won't|without)\b|不|没|無|无|未|非", re.I)


class BatchTranslationError(ValueError):
    pass


class BatchCancelled(BatchTranslationError):
    pass


class BatchRequestError(BatchTranslationError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = bool(retryable)


def estimate_tokens(text):
    """Conservative multilingual estimate without model tokenizers."""
    text = str(text or '')
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    words = len(_WORD_RE.findall(text))
    remainder = _WORD_RE.sub('', _CJK_RE.sub('', text))
    remainder = re.sub(r'\s+', '', remainder)
    # Non-Latin alphabetic scripts (Cyrillic, Arabic, Indic, ...) are
    # conservatively near one token per character; punctuation is cheaper.
    other_letters = sum(1 for char in remainder if ord(char) > 127 and char.isalpha())
    other = len(remainder) - other_letters
    return max(1, math.ceil(words * 1.35 + cjk * 1.15 + other_letters + other * 0.5))


def _ends_sentence(text):
    return bool(_SENTENCE_END_RE.search(str(text or '').strip()))


def sentence_units(rows):
    """Natural sentence units while preserving original cue identity and order."""
    units, current = [], []
    for row in sorted(rows, key=lambda item: item['seq']):
        if current:
            gap = int(row['start_ms']) - int(current[-1]['end_ms'])
            if gap > 1500:
                units.append(current); current = []
        current.append(row)
        text = ' '.join(str(item.get('en', '')).strip() for item in current)
        if _ends_sentence(text) or estimate_tokens(text) >= 600:
            units.append(current); current = []
    if current:
        units.append(current)
    return [{'rows': unit, 'tokens': sum(estimate_tokens(row.get('en', '')) for row in unit)}
            for unit in units]


def _unit_text(unit):
    return ' '.join(str(row.get('text', row.get('en', ''))).strip() for row in unit['rows'] if str(row.get('text', row.get('en', ''))).strip())


def provider_budgets(profile):
    cap = reasoning_capability(profile)
    planning_reserve = int(cap.get('planning_reserve', cap.get('response_reserve', 0)))
    usable = max(1024, int(cap['max_output']) - planning_reserve)
    target = min(TARGET_SOURCE_TOKENS, max(256, int((usable - 256) / 2.4)))
    # Reserve room for the JSON envelope, system prompt, glossary and prior
    # translations in addition to the model output.
    visible = min(VISIBLE_SOURCE_TOKENS, max(1000, int(cap['context_tokens']) - int(cap['max_output']) - 1600))
    target = min(target, max(256, visible - 400))
    surrounding = max(0, visible - target)
    prior = min(SIDE_CONTEXT_TOKENS, surrounding // 2)
    after = min(SIDE_CONTEXT_TOKENS, surrounding - prior)
    return {'visible': visible, 'target': target, 'prior': prior, 'after': after,
            'max_output': int(cap['max_output']), 'output_limit': usable,
            'capability': cap}


def _term_present(term, text):
    term = str(term or '').strip()
    if not term:
        return False
    if all(ord(char) < 128 for char in term):
        pattern = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
        return re.search(pattern, text, re.I) is not None
    return term.casefold() in text.casefold()


def relevant_glossary(domain_bias, text, limit=24, max_chars=1200):
    """Only configured terms that actually occur in the visible source window."""
    try:
        _config, profile = active_domain(domain_bias)
    except (ValueError, TypeError, KeyError):
        return [], ''
    found, used = [], 0
    for term in profile.get('terms', []):
        if not _term_present(term, text):
            continue
        if used + len(term) > max_chars or len(found) >= limit:
            break
        found.append(term); used += len(term)
    topic = profile.get('background', '').strip()[:600] if found else ''
    return found, topic


def _select_context(units, start, end, before_budget, after_budget):
    before, used = [], 0
    for unit in reversed(units[:start]):
        if used + unit['tokens'] > before_budget:
            break
        before.insert(0, unit); used += unit['tokens']
    after, used = [], 0
    for unit in units[end:]:
        if used + unit['tokens'] > after_budget:
            break
        after.append(unit); used += unit['tokens']
    return before, after


def _pending(row, only_pending):
    if not only_pending:
        return True
    return not str(row.get('zh', '') or '').strip() and row.get('status') != 'done'


def plan_batches(rows, profile, domain_bias=None, source_language='auto',
                 target_language='简体中文', only_pending=True):
    """Plan contiguous natural-sentence batches covering each pending ID once."""
    units = sentence_units(rows)
    budget = provider_budgets(profile)
    batches = []
    index = 0
    while index < len(units):
        if not any(_pending(row, only_pending) for row in units[index]['rows']):
            index += 1
            continue
        selected, token_total = [], 0
        while index < len(units):
            pending_rows = [row for row in units[index]['rows'] if _pending(row, only_pending)]
            if not pending_rows:
                break
            addition = sum(estimate_tokens(row.get('en', '')) for row in pending_rows)
            if selected and token_total + addition > budget['target']:
                break
            selected.append((index, pending_rows)); token_total += addition; index += 1
            if token_total >= budget['target']:
                break
        if not selected:
            index += 1; continue
        first_unit, last_unit = selected[0][0], selected[-1][0]
        remaining = max(0, budget['visible'] - token_total)
        prior_budget = min(budget['prior'], remaining // 2)
        after_budget = min(budget['after'], remaining - prior_budget)
        before_units, after_units = _select_context(units, first_unit, last_unit + 1, prior_budget, after_budget)
        target_items, groups = [], []
        for local, (_unit_index, pending_rows) in enumerate(selected):
            ids = []
            for row in pending_rows:
                target_items.append({'id': row['id'], 'seq': int(row['seq']),
                    'hash': text_fingerprint(row.get('en', '')), 'unit': local})
                ids.append(row['id'])
            groups.append(ids)
        prior, prior_tokens = [], 0
        if first_unit:
            collected = []
            for unit in reversed(units[max(0, first_unit - 4):first_unit]):
                for row in reversed(unit['rows']):
                    if not str(row.get('zh', '') or '').strip():
                        continue
                    cost = estimate_tokens(row.get('en', '')) + estimate_tokens(row.get('zh', ''))
                    if len(collected) >= 6 or prior_tokens + cost > 600:
                        break
                    collected.append({'source': row.get('en', ''), 'target': row.get('zh', '')})
                    prior_tokens += cost
                if len(collected) >= 6 or prior_tokens >= 600:
                    break
            prior = list(reversed(collected))
        visible_text = ' '.join(str(row.get('en', '')) for unit in before_units + selected_units(selected) + after_units
                                for row in unit['rows'])
        glossary, topic = relevant_glossary(domain_bias, visible_text)
        texts = [next(row.get('en', '') for _index, pending_rows in selected for row in pending_rows
                      if row['id'] == item['id']) for item in target_items]
        payload = {'target': target_items, 'units': groups,
            'before': [_unit_text(unit) for unit in before_units],
            'after': [_unit_text(unit) for unit in after_units],
            'prior': prior,
            'glossary': glossary, 'topic': topic}
        batches.append({'batch_id': uuid.uuid4().hex, 'items': payload,
            'source_hash': batch_fingerprint(texts),
            'target_tokens': token_total})
    return batches


def selected_units(selected):
    """Adapter for the visible-text window: selected units already carry rows."""
    return [{'rows': rows} for _index, rows in selected]


def job_from_record(record, rows, profile, key, source_language, target_language,
                    domain_bias=None):
    payload = record.get('payload') if isinstance(record, dict) and isinstance(record.get('payload'), dict) else None
    if payload is None:
        raw = record.get('items', '{}') if isinstance(record, dict) else record
        payload = json.loads(raw) if isinstance(raw, str) else raw
    by_id = {row['id']: row for row in rows}
    target = []
    for item in payload.get('target', []):
        row = by_id.get(item['id'])
        if not row:
            return None
        target.append({'id': item['id'], 'seq': int(item['seq']), 'text': row.get('en', '')})
    unit_ids = payload.get('units') or [[item['id']] for item in target]
    units = []
    for ids in unit_ids:
        unit = [next(item for item in target if item['id'] == ident) for ident in ids]
        units.append(unit)
    if not units:
        units = [[item] for item in target]
    visible_text = ' '.join([item['text'] for item in target] + list(payload.get('before', [])) + list(payload.get('after', [])))
    glossary = payload.get('glossary')
    topic = payload.get('topic', '')
    if glossary is None:
        glossary, topic = relevant_glossary(domain_bias, visible_text)
    return {'session_id': record['session_id'], 'run_id': record['run_id'],
        'batch_id': record['batch_id'], 'claim': record.get('claim', ''),
        'target': target, 'units': units,
        'before': list(payload.get('before', [])), 'after': list(payload.get('after', [])),
        'prior': list(payload.get('prior', [])), 'glossary': list(glossary or []), 'topic': topic,
        'source_language': source_language, 'target_language': target_language,
        'profile': dict(profile), 'key': key, 'budgets': provider_budgets(profile)}


def build_system_prompt(target_language, glossary=None, topic='', strict=False, focused=False):
    lines = [
        '你是专业多语言字幕译者。把 translate 数组中每个条目的 text 翻译为' + str(target_language) + '。',
        '只输出一个 JSON 对象：{"translations":[{"id":"原 id","text":"译文"}]}。',
        '必须包含 translate 中全部且仅这些 id，每个 id 恰好一次；不得新增、遗漏、重复、合并或拆分条目；不得输出时间戳或额外字段。',
        'context_before、context_after 和 prior_translated 仅供理解，不翻译、不输出。',
        '按原文最可能的语义直译；原文来自 ASR，可能有识别错误。不要解释、不要补充原文没有的信息、不要编造定义。',
        '严格保留数字、单位、正负号、否定词和专名。若语义不确定，宁可保持直译。',
        '不要输出思维过程、分析或推理内容，只输出最终 JSON。',
    ]
    if glossary:
        lines.append('术语（仅当原文出现时使用，不要添加原文没有的定义）：' + '、'.join(glossary) + '。')
    if topic:
        lines.append('领域背景（仅供理解，不要补充原文没有的内容）：' + topic)
    if strict:
        lines.append('上一次响应未通过严格校验；请重新生成完整 JSON，并确保 id 完全对应。')
    if focused:
        lines.append('请重点复核数字、单位、正负号和否定含义，并保持相邻句子的指代一致。')
    return '\n'.join(lines)


def _payload_for(job, start, end, focused=False):
    units = job['units'][start:end]
    before = list(job['before']) + [_unit_text({'rows': unit}) for unit in job['units'][:start]]
    after = [_unit_text({'rows': unit}) for unit in job['units'][end:]] + list(job['after'])
    translate = [{'id': item['id'], 'text': item['text']} for unit in units for item in unit]
    if focused:
        before = before[-8:]
        after = after[:8]
    return {'source_language': job['source_language'], 'target_language': job['target_language'],
        'glossary': job['glossary'], 'topic': job['topic'],
        'context_before': before, 'context_after': after,
        'prior_translated': job['prior'], 'translate': translate}


def _json_tokens(value):
    return estimate_tokens(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def _fit_request(job, payload, strict, focused):
    """Trim only read-only context; never truncate target cues or invent success."""
    cap = job['budgets']['capability']
    context_limit = max(256, int(cap['context_tokens']))
    max_output = max(256, int(cap['max_output']))
    allowed = max(256, context_limit - max_output - 256)
    glossary = list(payload.get('glossary') or [])
    topic = payload.get('topic', '')
    while True:
        payload['glossary'] = glossary
        payload['topic'] = topic
        system = build_system_prompt(job['target_language'], glossary, topic,
            strict=strict, focused=focused)
        total = estimate_tokens(system) + _json_tokens(payload) + 128
        if total <= allowed:
            return payload, system
        # Context is read-only. Drop the farthest sentence first; prior
        # translations, glossary and topic come after that.
        if payload['context_before'] or payload['context_after']:
            if len(payload['context_before']) >= len(payload['context_after']):
                payload['context_before'].pop(0)
            else:
                payload['context_after'].pop()
        elif payload['prior_translated']:
            payload['prior_translated'].pop(0)
        elif glossary:
            glossary.pop()
        elif topic:
            topic = ''
        else:
            raise BatchTranslationError('请求内容超过服务上下文上限；未发送请求，原文和时间轴保留')


def suspicious_flags(sources, translations):
    """Useful review flags only; this is not a semantic correctness guarantee."""
    flags = []
    for ident, source in sources.items():
        target = translations.get(ident, '')
        source_numbers = Counter(_NUMBER_RE.findall(source))
        target_numbers = Counter(_NUMBER_RE.findall(target))
        if source_numbers != target_numbers:
            flags.append({'id': ident, 'kind': 'quantity',
                'source': source[:120], 'target': target[:120]})
        source_units = {value.casefold() for value in _UNIT_RE.findall(source)}
        target_units = {value.casefold() for value in _UNIT_RE.findall(target)}
        if source_units != target_units:
            flags.append({'id': ident, 'kind': 'unit',
                'source': source[:120], 'target': target[:120]})
        if bool(_NEGATION_RE.search(source)) != bool(_NEGATION_RE.search(target)):
            flags.append({'id': ident, 'kind': 'negation',
                'source': source[:120], 'target': target[:120]})
    return flags[:50]


class FileBatchTranslator(QThread):
    """One bounded provider request at a time; runs are isolated by run_id."""
    batch_done = Signal(object)
    batch_failed = Signal(object)

    def __init__(self, parent=None, transport=None):
        super().__init__(parent)
        self.jobs = queue.Queue(maxsize=128)
        self.transport = transport
        self._cancel = {}
        self._cancel_lock = threading.Lock()
        self._shutdown = threading.Event()

    def enqueue(self, job):
        # Re-enqueueing a run is an explicit resume: discard the old cancel
        # latch so the new attempt is not rejected by a previous cancellation.
        with self._cancel_lock:
            self._cancel.pop((job.get('session_id'), job.get('run_id')), None)
        try:
            self.jobs.put_nowait(job); return True
        except queue.Full:
            return False

    def cancel_run(self, session_id, run_id):
        with self._cancel_lock:
            event = self._cancel.setdefault((session_id, run_id), threading.Event())
            event.set()
        self._drop_queued(session_id, run_id)

    def _drop_queued(self, session_id, run_id):
        with self.jobs.mutex:
            retained, dropped = [], 0
            for item in self.jobs.queue:
                if item is not None and item.get('session_id') == session_id and item.get('run_id') == run_id:
                    dropped += 1
                else:
                    retained.append(item)
            self.jobs.queue.clear(); self.jobs.queue.extend(retained)
            self.jobs.unfinished_tasks = max(0, self.jobs.unfinished_tasks - dropped)
            self.jobs.not_full.notify_all()

    def cancel_all(self):
        with self._cancel_lock:
            for key in list(self._cancel):
                self._cancel[key].set()
            self._cancel.clear()
        with self.jobs.mutex:
            retained = [item for item in self.jobs.queue if item is None]
            dropped = len(self.jobs.queue) - len(retained)
            self.jobs.queue.clear(); self.jobs.queue.extend(retained)
            self.jobs.unfinished_tasks = max(0, self.jobs.unfinished_tasks - dropped)
            self.jobs.not_full.notify_all()

    def stop(self):
        self.cancel_all(); self._shutdown.set()
        try: self.jobs.put_nowait(None)
        except queue.Full: pass

    def _event(self, session_id, run_id):
        """Every in-flight job holds a stable event so cancellation can reach it."""
        with self._cancel_lock:
            return self._cancel.setdefault((session_id, run_id), threading.Event())

    def _client_post(self, url, headers, body, timeout):
        if self.transport is not None:
            return self.transport.post(url, headers=headers, json=body, timeout=timeout, allow_redirects=False)
        # requests.post creates an isolated session per call, so an abandoned
        # daemon request can never race a resumed request through a shared pool.
        return requests.post(url, headers=headers, json=body, timeout=timeout, allow_redirects=False)

    def _post_cancellable(self, url, headers, body, timeout, cancel):
        """Run one billable HTTP call under the shared single-request slot.

        The QThread may leave while the daemon helper is still waiting on the
        socket. That helper keeps the slot until the socket actually returns or
        fails; a subsequent run therefore waits instead of issuing another
        billable request. The helper performs no Qt callbacks or DB writes.
        """
        _HTTP_SLOT.acquire(cancel, self._shutdown)
        if self._shutdown.is_set() or (cancel is not None and cancel.is_set()):
            _HTTP_SLOT.release()
            raise BatchCancelled('批量翻译已取消')
        result = {}
        done = threading.Event()
        def call():
            try:
                result['response'] = self._client_post(url, headers, body, timeout)
            except BaseException as error:
                result['error'] = error
            finally:
                # Release only after the socket call has returned or failed.
                _HTTP_SLOT.release()
                done.set()
        thread = threading.Thread(target=call, daemon=True, name='file-batch-http')
        try:
            thread.start()
        except BaseException:
            _HTTP_SLOT.release()
            done.set()
            raise
        while not done.wait(0.1):
            if self._shutdown.is_set() or (cancel is not None and cancel.is_set()):
                raise BatchCancelled('批量翻译已取消')
        if 'error' in result:
            raise result['error']
        return result.get('response')

    def run(self):
        while not self._shutdown.is_set():
            job = self.jobs.get()
            if job is None or self._shutdown.is_set():
                break
            session_id, run_id = job['session_id'], job['run_id']
            cancel = self._event(session_id, run_id)
            try:
                if cancel is not None and cancel.is_set():
                    raise BatchCancelled('批量翻译已取消')
                result = self._translate(job, cancel)
                self.batch_done.emit({'session_id': session_id, 'run_id': run_id,
                    'batch_id': job['batch_id'], 'claim': job['claim'], **result})
            except BatchCancelled:
                self.batch_failed.emit({'session_id': session_id, 'run_id': run_id,
                    'batch_id': job['batch_id'], 'claim': job['claim'], 'cancelled': True,
                    'error': '批量翻译已取消'})
            except (BatchTranslationError, TranslationResponseError, ValueError, KeyError,
                    TypeError, IndexError) as error:
                self.batch_failed.emit({'session_id': session_id, 'run_id': run_id,
                    'batch_id': job['batch_id'], 'claim': job['claim'], 'cancelled': False,
                    'error': str(error) or '批量翻译失败'})
            except Exception:
                self.batch_failed.emit({'session_id': session_id, 'run_id': run_id,
                    'batch_id': job['batch_id'], 'claim': job['claim'], 'cancelled': False,
                    'error': '批量翻译发生未预期错误，可稍后重试'})
            finally:
                self.jobs.task_done()

    def _translate(self, job, cancel):
        results, requests_made = {}, 0
        subsets = [(0, len(job['units']), 0)]
        while subsets:
            start, end, depth = subsets.pop(0)
            attempts = 0; output_scale = 1.0
            while True:
                if cancel is not None and cancel.is_set():
                    raise BatchCancelled('批量翻译已取消')
                unit_count = end - start
                target_tokens = sum(estimate_tokens(item['text'])
                    for unit in job['units'][start:end] for item in unit)
                projected_output = max(512, int(target_tokens * 2.4 + 256))
                if projected_output > job['budgets']['output_limit']:
                    if unit_count > 1 and depth < MAX_SPLIT_DEPTH:
                        middle = start + unit_count // 2
                        subsets.insert(0, (middle, end, depth + 1))
                        subsets.insert(0, (start, middle, depth + 1))
                        break
                    raise BatchTranslationError(
                        f'目标字幕约 {target_tokens} tokens，超过服务可用输出预算 '
                        f'{job["budgets"]["output_limit"]}；未发送请求，原文和时间轴保留')
                if requests_made >= MAX_REQUESTS_PER_BATCH:
                    raise BatchTranslationError('该批次多次重试仍未通过校验，已停止重试')
                requests_made += 1
                try:
                    part = self._request(job, start, end, strict=attempts > 0 or depth > 0,
                        focused=False, output_scale=output_scale, cancel=cancel)
                    results.update(part)
                    break
                except (TruncatedTranslation, TranslationResponseError) as error:
                    unit_count = end - start
                    if getattr(error, 'incomplete', False) and unit_count > 1 and depth < MAX_SPLIT_DEPTH:
                        middle = start + unit_count // 2
                        subsets.insert(0, (middle, end, depth + 1))
                        subsets.insert(0, (start, middle, depth + 1))
                        break
                    if attempts < MAX_STRICT_RETRIES and requests_made < MAX_REQUESTS_PER_BATCH:
                        attempts += 1; output_scale = max(output_scale, 1.5)
                        continue
                    raise BatchTranslationError(str(error)) from None
                except BatchRequestError as error:
                    if error.retryable and attempts < MAX_STRICT_RETRIES and requests_made < MAX_REQUESTS_PER_BATCH:
                        attempts += 1
                        if cancel is not None:
                            cancel.wait(attempts)
                        else:
                            time.sleep(attempts)
                        continue
                    raise
        expected = {item['id'] for unit in job['units'] for item in unit}
        if set(results) != expected:
            raise BatchTranslationError('批量翻译结果未覆盖全部目标 ID')
        sources = {item['id']: item['text'] for unit in job['units'] for item in unit}
        flags = suspicious_flags(sources, results)
        if flags and requests_made < MAX_REQUESTS_PER_BATCH:
            if cancel is not None and cancel.is_set():
                raise BatchCancelled('批量翻译已取消')
            requests_made += 1
            try:
                focused = self._request(job, 0, len(job['units']), strict=True,
                    focused=True, output_scale=1.2, cancel=cancel)
                if set(focused) == expected:
                    results = focused
                    flags = suspicious_flags(sources, results)
            except (BatchTranslationError, TranslationResponseError):
                pass
        if cancel is not None and cancel.is_set():
            raise BatchCancelled('批量翻译已取消')
        return {'translations': results, 'flags': flags, 'requests': requests_made,
                'target_tokens': sum(estimate_tokens(text) for text in sources.values())}

    def _request(self, job, start, end, strict, focused, output_scale, cancel):
        payload = _payload_for(job, start, end, focused=focused)
        expected = [item['id'] for item in payload['translate']]
        source_tokens = sum(estimate_tokens(item['text']) for item in payload['translate'])
        max_tokens = max(512, min(job['budgets']['max_output'],
            int((source_tokens * 2.4 + 256) * output_scale)))
        payload, system = _fit_request(job, payload, strict, focused)
        url, headers, body = structured_request_payload(job['profile'], system,
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            max_tokens, key=job.get('key', ''), capability=job['budgets']['capability'])
        timeout = (10, 120) if job['budgets']['capability'].get('enabled') else (8, 50)
        last_error = None
        for attempt in range(3):
            if cancel is not None and cancel.is_set():
                raise BatchCancelled('批量翻译已取消')
            try:
                response = self._post_cancellable(url, headers, body, timeout, cancel)
                if cancel is not None and cancel.is_set():
                    raise BatchCancelled('批量翻译已取消')
            except requests.RequestException:
                last_error = BatchRequestError('连接失败或超时，请检查 API 地址和网络', retryable=True)
                if attempt < 2:
                    if cancel is not None: cancel.wait(attempt + 1)
                    else: time.sleep(attempt + 1)
                    continue
                raise last_error
            status = int(getattr(response, 'status_code', 0))
            if status != 200:
                error = BatchRequestError(http_error_message(status), retryable=status in RETRYABLE_HTTP)
                if error.retryable and attempt < 2:
                    if cancel is not None: cancel.wait(attempt + 1)
                    else: time.sleep(attempt + 1)
                    continue
                raise error
            try:
                data = response.json()
            except (ValueError, TypeError):
                raise TranslationResponseError('响应不是有效 JSON', incomplete=True) from None
            return parse_structured_translations(job['profile']['protocol'], data, expected)
        raise last_error or BatchTranslationError('翻译请求失败')
