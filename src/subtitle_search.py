r"""Literal and approximate subtitle search.

Search semantics used by the UI
-------------------------------
exact
    A literal substring of either original or translated text.  Unicode is
    NFKC-normalized and case-folded for matching; SQL ``%``, ``_`` and the
    backslash have no wildcard meaning.  Punctuation is significant.
fuzzy
    Approximate matching for real typos, missing/extra characters and common
    accent differences.  Latin queries are compared word-by-word using a
    bounded Damerau-Levenshtein distance.  CJK/kana/Hangul runs are compared in
    short sliding windows, so no whitespace or word segmentation is required.
    A fuzzy search also contains literal hits.  Every word/run in a multi-part
    query must match within the same original field or the same translated
    field, just as an exact query must fit inside one field.

The corpus scan runs outside the UI thread.  ``cancel`` is checked between
rows and SQLite's progress handler aborts a bounded exact query on request.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from storage import cue_time


_CJK = (r'぀-ヿㇰ-ㇿ㐀-䶿一-鿿'
    r'豈-﫿가-힯\U00020000-\U0002fa1f')
_CJK_RUN = re.compile(f'[{_CJK}]+')
_CJK_CHAR = re.compile(f'[{_CJK}]')
_UNIT = re.compile(f'([{_CJK}]+)|([^\\W_]+)', re.UNICODE)
_WORD = re.compile(r'[^\W_]+', re.UNICODE)
_SCOPES = {'all', 'source', 'translation'}
_MODES = {'exact', 'fuzzy'}


class SearchCancelled(Exception):
    pass


@dataclass
class SearchResponse:
    query: str
    mode: str
    scope: str
    hits: list = field(default_factory=list)
    truncated: bool = False
    scanned: int = 0
    duration_ms: float = 0.0
    cancelled: bool = False


def normalize_text(value, fold_accents=False):
    text = unicodedata.normalize('NFKD' if fold_accents else 'NFKC', str(value or '')).casefold()
    if fold_accents:
        text = ''.join(char for char in text if not unicodedata.combining(char))
    return text


def _hangul_joins(left, right):
    """Canonical Hangul composition happens across jamo code points."""
    first, second = ord(left), ord(right)
    if 0x1100 <= first <= 0x115F and 0x1160 <= second <= 0x1175:
        return True
    if 0x1160 <= first <= 0x1175 and 0x11A8 <= second <= 0x11C2:
        return True
    if 0xAC00 <= first <= 0xD7A3 and 0x11A8 <= second <= 0x11C2:
        return True
    return False


def _normalization_clusters(text):
    """Base characters with their combining marks / Hangul jamo."""
    if not text:
        return
    start = 0
    for index in range(1, len(text)):
        char = text[index]
        if (unicodedata.combining(char) or unicodedata.category(char).startswith('M')
                or _hangul_joins(text[index - 1], char)):
            continue
        yield start, index
        start = index
    yield start, len(text)


def _normalized_with_map(value, fold_accents=False):
    """Whole-string Unicode normalization with original-text span mapping.

    Normalizing each code point in isolation cannot compose ``e`` + combining
    acute into ``é`` (nor decomposed Hangul jamo into a syllable).  Clusters
    are normalized as units, while the final case-fold is applied to the whole
    string so context-sensitive folds (for example Greek final sigma) still
    agree with :func:`normalize_text`.
    """
    original = str(value or '')
    form = 'NFKD' if fold_accents else 'NFKC'
    base = unicodedata.normalize(form, original)
    starts = []
    ends = []
    pieces = []
    for start, end in _normalization_clusters(original):
        piece = unicodedata.normalize(form, original[start:end])
        pieces.append(piece)
        starts.extend([start] * len(piece))
        ends.extend([end] * len(piece))
    if ''.join(pieces) != base:
        # Extremely defensive fallback: still return the correct normalized
        # string for matching, with cue-wide context rather than a wrong span.
        starts = [0] * len(base)
        ends = [len(original)] * len(base)

    folded = base.casefold()
    folded_starts = []
    folded_ends = []
    per_character = []
    for index, char in enumerate(base):
        folded_char = char.casefold()
        per_character.append(folded_char)
        folded_starts.extend([starts[index]] * len(folded_char))
        folded_ends.extend([ends[index]] * len(folded_char))
    if len(''.join(per_character)) != len(folded):
        folded_starts = [0] * len(folded)
        folded_ends = [len(original)] * len(folded)

    if fold_accents:
        kept = [index for index, char in enumerate(folded) if not unicodedata.combining(char)]
        folded = ''.join(folded[index] for index in kept)
        folded_starts = [folded_starts[index] for index in kept]
        folded_ends = [folded_ends[index] for index in kept]
    return folded, folded_starts, folded_ends


def _original_span(starts, ends, start, end, text_length):
    if not starts:
        return 0, 0
    start = max(0, min(start, len(starts) - 1))
    end = max(start + 1, min(end, len(starts)))
    original_start = starts[start]
    original_end = ends[end - 1]
    return max(0, original_start), min(text_length, original_end)


def _context(text, start, end, radius=38):
    text = str(text or '')
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = re.sub(r'\s+', ' ', text[left:right]).strip()
    return ('…' if left else '') + snippet + ('…' if right < len(text) else '')


def _edit_distance(left, right, limit):
    """Optimal-string-alignment Damerau-Levenshtein distance."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous_previous = None
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        row_minimum = i
        for j, b in enumerate(right, 1):
            cost = 0 if a == b else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if (previous_previous is not None and i > 1 and j > 1
                    and a == right[j - 2] and b == left[i - 2]):
                value = min(value, previous_previous[j - 2] + 1)
            current.append(value)
            row_minimum = min(row_minimum, value)
        if row_minimum > limit:
            return limit + 1
        previous_previous, previous = previous, current
    return previous[-1]


def _fuzzy_latin(token, text_normalized):
    if len(token) < 2:
        position = text_normalized.find(token)
        return (1.0, position, position + len(token)) if position >= 0 else None
    if len(token) == 2:
        position = text_normalized.find(token)
        return (1.0, position, position + len(token)) if position >= 0 else None
    allowed = 1 if len(token) <= 7 else (2 if len(token) <= 12 else 3)
    best = None
    for match in _WORD.finditer(text_normalized):
        candidate = match.group()
        if _CJK_CHAR.search(candidate):
            continue
        distance = _edit_distance(token, candidate, allowed)
        if distance <= allowed:
            score = 1.0 - distance / max(len(token), len(candidate))
            choice = (score, match.start(), match.end())
            if best is None or choice[0] > best[0]:
                best = choice
    return best


def _fuzzy_cjk(run, text_normalized):
    if len(run) == 1:
        position = text_normalized.find(run)
        return (1.0, position, position + 1) if position >= 0 else None
    allowed = 1 if len(run) <= 5 else (2 if len(run) <= 10 else 3)
    best = None
    for match in _CJK_RUN.finditer(text_normalized):
        candidate = match.group()
        lengths = {max(1, len(run) - 1), len(run), len(run) + 1}
        for length in lengths:
            if length > len(candidate):
                continue
            for offset in range(0, len(candidate) - length + 1):
                window = candidate[offset:offset + length]
                distance = _edit_distance(run, window, allowed)
                if distance <= allowed:
                    score = 1.0 - distance / max(len(run), len(window))
                    choice = (score, match.start() + offset, match.start() + offset + length)
                    if best is None or choice[0] > best[0]:
                        best = choice
    return best


def fuzzy_match(query, text):
    """Return ``(score, original_start, original_end)`` or ``None``.

    The input text is mapped back to original Unicode offsets so the UI can
    show context around the actual match.
    """
    if not query or not text:
        return None
    normalized, starts, ends = _normalized_with_map(text, fold_accents=True)
    query_normalized = normalize_text(query, fold_accents=True)
    if not query_normalized:
        return None
    position = normalized.find(query_normalized)
    if position >= 0:
        start, end = _original_span(starts, ends, position, position + len(query_normalized), len(text))
        return 1.0, start, end
    units = [match.group() for match in _UNIT.finditer(query_normalized)]
    if not units:
        return None
    scores = []
    match_starts = []
    match_ends = []
    for unit in units:
        result = _fuzzy_cjk(unit, normalized) if _CJK_CHAR.search(unit) else _fuzzy_latin(unit, normalized)
        if not result:
            return None
        score, start, end = result
        scores.append(score)
        match_starts.append(start)
        match_ends.append(end)
    start, end = _original_span(starts, ends, min(match_starts), max(match_ends), len(text))
    return sum(scores) / len(scores), start, end


def _make_hit(row, query, mode, scope='all'):
    en = str(row['en'] or '')
    zh = str(row['zh'] or '')
    matched = []
    spans = {}
    fields = (('en', en), ('zh', zh)) if scope == 'all' else ((('en', en),) if scope == 'source' else (('zh', zh),))
    for field_name, text in fields:
        if not text:
            continue
        if mode == 'exact':
            normalized, starts, ends = _normalized_with_map(text)
            query_normalized = normalize_text(query)
            position = normalized.find(query_normalized)
            if position < 0:
                continue
            start, end = _original_span(starts, ends, position, position + len(query_normalized), len(text))
            score = 1.0
        else:
            result = fuzzy_match(query, text)
            if not result:
                continue
            score, start, end = result
        matched.append(field_name)
        spans[field_name] = (start, end, score)
    if not matched:
        return None
    en_span = spans.get('en')
    zh_span = spans.get('zh')
    return {
        'session_id': row['session_id'],
        'session_title': row['session_title'],
        'segment_id': row['id'],
        'seq': row['seq'],
        'start_ms': row['start_ms'],
        'end_ms': row['end_ms'],
        'timestamp': cue_time(row['start_ms']),
        'en': en,
        'zh': zh,
        'en_snippet': _context(en, *en_span[:2]) if en_span else '',
        'zh_snippet': _context(zh, *zh_span[:2]) if zh_span else '',
        'matched': matched,
        'score': round(max(span[2] for span in spans.values()), 4),
    }


def _allowed_distance(unit):
    if _CJK_CHAR.search(unit):
        return 1 if len(unit) <= 5 else (2 if len(unit) <= 10 else 3)
    if len(unit) <= 2:
        return 0
    return 1 if len(unit) <= 7 else (2 if len(unit) <= 12 else 3)


def _anchor_groups(query):
    """Safe OR-of-pieces prefilter for fuzzy candidates.

    A transposition can touch characters on both sides of a piece boundary.
    With ``k`` allowed edits, split each query unit into ``2k + 1`` contiguous
    pieces; at most ``2k`` pieces can be touched (a transposition touches at
    most two), so at least one untouched piece must occur literally.  Only
    groups with more than ``2k`` pieces of at least two characters are used;
    otherwise no prefilter is safe and the unit is left unrestricted.
    """
    normalized = normalize_text(query, fold_accents=True)
    groups = []
    for unit in (match.group() for match in _UNIT.finditer(normalized)):
        allowed = _allowed_distance(unit)
        touched_limit = 2 * allowed
        if not allowed:
            pieces = [unit] if len(unit) >= 2 else []
        else:
            pieces = []
            start = 0
            parts = touched_limit + 1
            for index in range(parts):
                remaining = parts - index
                length = len(unit) - start if index == parts - 1 else max(1, round((len(unit) - start) / remaining))
                pieces.append(unit[start:start + length])
                start += length
            pieces = [piece for piece in pieces if len(piece) >= 2]
        if len(pieces) > touched_limit and pieces:
            groups.append(pieces)
    return groups


def _fuzzy_candidates(connection, query, scope, cancelled):
    fields = ('en', 'zh') if scope == 'all' else (('en',) if scope == 'source' else ('zh',))
    groups = _anchor_groups(query)
    if not groups:
        return None
    conditions = []
    args = []
    for pieces in groups:
        alternatives = []
        for field_name in fields:
            for piece in pieces:
                alternatives.append(f'instr(search_norm_fold(g.{field_name}), ?) > 0')
                args.append(piece)
        conditions.append('(' + ' OR '.join(alternatives) + ')')
    sql = f'''
        SELECT g.*, s.title AS session_title
        FROM segments g JOIN sessions s ON s.id=g.session_id
        WHERE {' AND '.join(conditions)}
        ORDER BY s.created_at DESC, g.seq'''
    return connection.execute(sql, args)


def _connect(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _exact_search(connection, query, scope, limit, cancelled):
    condition = []
    args = []
    fields = ('en', 'zh') if scope == 'all' else (('en',) if scope == 'source' else ('zh',))
    for field_name in fields:
        condition.append(f'instr(search_norm(g.{field_name}), ?) > 0')
        args.append(query)
    args.append(limit + 1)
    sql = f'''
        SELECT g.*, s.title AS session_title
        FROM segments g JOIN sessions s ON s.id=g.session_id
        WHERE ({' OR '.join(condition)})
        ORDER BY s.created_at DESC, g.seq
        LIMIT ?'''
    rows = connection.execute(sql, args).fetchall()
    if cancelled():
        raise SearchCancelled
    return rows


def _fuzzy_search(connection, query, scope, limit, cancelled):
    cursor = _fuzzy_candidates(connection, query, scope, cancelled)
    if cursor is None:
        sql = '''
            SELECT g.*, s.title AS session_title
            FROM segments g JOIN sessions s ON s.id=g.session_id
            ORDER BY s.created_at DESC, g.seq'''
        cursor = connection.execute(sql)
    hits = []
    scanned = 0
    while True:
        rows = cursor.fetchmany(256)
        if not rows:
            break
        for row in rows:
            if cancelled():
                raise SearchCancelled
            scanned += 1
            hit = _make_hit(row, query, 'fuzzy', scope)
            if hit:
                hits.append(hit)
                if len(hits) > limit:
                    return hits, scanned
    return hits, scanned


def search_corpus(db_path, query, mode='exact', scope='all', limit=200, cancel=None):
    """Search every saved cue in ``db_path``.

    Only a bounded number of results is returned; ``truncated`` tells the UI
    that additional matches exist.  The caller owns threading.
    """
    started = time.perf_counter()
    if hasattr(db_path, 'path'):
        db_path = db_path.path
    query = str(query or '').strip()
    mode = mode if mode in _MODES else 'exact'
    scope = scope if scope in _SCOPES else 'all'
    limit = max(1, min(int(limit), 500))
    cancel_event = cancel if cancel is not None else threading.Event()

    def cancelled():
        if hasattr(cancel_event, 'is_set'):
            return bool(cancel_event.is_set())
        if callable(cancel_event):
            return bool(cancel_event())
        return False

    if not query or not normalize_text(query):
        return SearchResponse(query, mode, scope, duration_ms=0.0)

    connection = _connect(db_path)
    scanned = 0
    hits = []
    truncated = False
    try:
        def search_norm(value):
            return normalize_text(value)
        def search_norm_fold(value):
            return normalize_text(value, fold_accents=True)
        connection.create_function('search_norm', 1, search_norm, deterministic=True)
        connection.create_function('search_norm_fold', 1, search_norm_fold, deterministic=True)
        connection.set_progress_handler(lambda: 1 if cancelled() else 0, 1000)
        try:
            if mode == 'exact':
                rows = _exact_search(connection, normalize_text(query), scope, limit, cancelled)
                scanned = len(rows)
                truncated = len(rows) > limit
                for row in rows[:limit]:
                    hit = _make_hit(row, query, 'exact', scope)
                    if hit:
                        hits.append(hit)
            else:
                fuzzy_hits, scanned = _fuzzy_search(connection, query, scope, limit, cancelled)
                truncated = len(fuzzy_hits) > limit
                hits = fuzzy_hits[:limit]
        except sqlite3.OperationalError as error:
            if 'interrupt' in str(error).lower():
                raise SearchCancelled from error
            raise
    except SearchCancelled:
        return SearchResponse(query, mode, scope, hits=[], truncated=False,
            scanned=scanned, duration_ms=(time.perf_counter() - started) * 1000,
            cancelled=True)
    finally:
        connection.close()
    return SearchResponse(query, mode, scope, hits=hits, truncated=truncated,
        scanned=scanned, duration_ms=(time.perf_counter() - started) * 1000)
