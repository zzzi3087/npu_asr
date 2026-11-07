"""Subtitle export formatting and safe batch filenames.

The HTTP/network layers are deliberately absent.  This module only reads a
History object and writes local UTF-8 files.  Plain ``.txt`` output contains
captions only: no session title, date, cue number, timestamp, status or other
generated metadata.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from storage import cue_time


@dataclass(frozen=True)
class ExportFormat:
    key: str
    label: str
    extension: str
    dialog_filter: str


FORMATS = (
    ExportFormat('srt', 'SRT 字幕', 'srt', 'SRT 字幕 (*.srt)'),
    ExportFormat('vtt', '网页字幕 VTT', 'vtt', '网页字幕 VTT (*.vtt)'),
    ExportFormat('txt', '纯文本 TXT · 无时间戳', 'txt', '纯文本 TXT (*.txt)'),
    ExportFormat('csv', '表格 CSV · 完整记录', 'csv', '表格 CSV (*.csv)'),
    ExportFormat('json', '完整记录 JSON', 'json', '完整记录 JSON (*.json)'),
)
FORMAT_BY_KEY = {item.key: item for item in FORMATS}
CONTENT_MODES = (
    ('bilingual', '双语 · 原文 + 译文（缺译文时保留原文）'),
    ('source', '仅原文 · 空原文则省略该句'),
    ('translation', '仅译文 · 缺译文则省略该句'),
)
CONTENT_LABELS = dict(CONTENT_MODES)
CONTENT_AWARE_FORMATS = {'srt', 'vtt', 'txt'}

# Windows reserves these names regardless of an attached extension.  Keep this
# list lowercase and compare against the part before the first dot.
WINDOWS_RESERVED = {
    'con', 'prn', 'aux', 'nul', 'clock$', 'conin$', 'conout$',
    *(f'com{i}' for i in range(1, 10)),
    *(f'lpt{i}' for i in range(1, 10)),
    *(f'com{i}' for i in '¹²³'),
    *(f'lpt{i}' for i in '¹²³'),
}
_INVALID_FILENAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')
_WHITESPACE = re.compile(r'\s+')


def export_format(key):
    return FORMAT_BY_KEY.get(str(key).lower())


def format_for_suffix(suffix):
    key = str(suffix).lower().lstrip('.')
    return FORMAT_BY_KEY.get(key) or (ExportFormat('md', 'Markdown 旧版', 'md', '') if key == 'md' else None)


def caption_lines(row, content='bilingual'):
    """Return subtitle lines without labels or placeholders.

    ``source`` and ``translation`` are genuine language-only modes: they never
    substitute the other language.  A cue with no text for the selected mode is
    omitted by the renderers.  Bilingual mode keeps the documented fallback to
    source when translation is missing, and does not duplicate identical text.
    """
    source = str(row.get('en') or '')
    translation = str(row.get('zh') or '')
    if content == 'source':
        return [source] if source else []
    if content == 'translation':
        return [translation] if translation else []
    lines = [source] if source else []
    if translation and translation != source:
        lines.append(translation)
    if not lines and translation:
        lines.append(translation)
    return lines


def _cue_end(rows, index):
    row = rows[index]
    end = max(int(row['start_ms']) + 200, int(row['end_ms']))
    if index + 1 < len(rows):
        following = int(rows[index + 1]['start_ms'])
        if following > int(row['start_ms']):
            end = min(end, following)
    return end


def _timestamped_text(rows, content, separator=',', header=''):
    chunks = [header] if header else []
    cues = []
    for row in rows:
        lines = caption_lines(row, content)
        if lines:
            cues.append((row, lines))
    cue_rows = [row for row, _lines in cues]
    for index, (row, lines) in enumerate(cues):
        text = '\n'.join(lines)
        chunks.append(f'{index + 1}\n'
            f'{cue_time(row["start_ms"], separator)} --> {cue_time(_cue_end(cue_rows, index), separator)}\n'
            f'{text}\n')
    return '\n'.join(chunks)


def _plain_text(rows, content):
    blocks = []
    for row in rows:
        lines = caption_lines(row, content)
        if lines:
            blocks.append('\n'.join(lines))
    # One blank line separates cues.  No title, date, timestamp, cue number,
    # status marker, BOM or other generated record is added.
    return '\n\n'.join(blocks) + ('\n' if blocks else '')


def _legacy_markdown(session, rows, content):
    """Keep direct .md exports from older callers readable and timestamped."""
    blocks = [session['title'], session['created_at'], '']
    for row in rows:
        blocks.append(f'[{cue_time(row["start_ms"])}]' + (' [边界待核]' if row['uncertain'] else ''))
        blocks.extend(caption_lines(row, content))
        blocks.append('')
    return '\n'.join(blocks)


def _csv_text(rows):
    def excel_text(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(['序号', '开始毫秒', '结束毫秒', '英文', '中文', '状态', '边界待核'])
    for row in rows:
        writer.writerow([row['seq'], row['start_ms'], row['end_ms'],
            excel_text(row['en']), excel_text(row['zh']), row['status'], bool(row['uncertain'])])
    return buffer.getvalue()


def render_text(format_key, session, rows, content='bilingual'):
    """Render one session and return ``(text, encoding)``."""
    if content not in CONTENT_LABELS:
        content = 'bilingual'
    if format_key == 'srt':
        return _timestamped_text(rows, content), 'utf-8-sig'
    if format_key == 'vtt':
        return _timestamped_text(rows, content, separator='.', header='WEBVTT\n'), 'utf-8'
    if format_key == 'txt':
        return _plain_text(rows, content), 'utf-8'
    if format_key == 'csv':
        return _csv_text(rows), 'utf-8-sig'
    if format_key == 'json':
        payload = {'version': 1, 'session': session, 'segments': rows}
        return json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8'
    if format_key == 'md':
        return _legacy_markdown(session, rows, content), 'utf-8'
    raise ValueError('请选择 TXT、SRT、VTT、CSV 或 JSON 格式')


def _utf16_units(text):
    return len(text.encode('utf-16-le')) // 2


def _trim_utf16(text, max_units):
    if _utf16_units(text) <= max_units:
        return text
    encoded = text.encode('utf-16-le')[:max_units * 2]
    try:
        trimmed = encoded.decode('utf-16-le')
    except UnicodeDecodeError:
        # A supplementary character was split across the byte boundary.
        trimmed = encoded[:-2].decode('utf-16-le')
    return trimmed.rstrip(' .')


def sanitize_filename(title, fallback='字幕', max_units=120):
    """Return a Windows-safe single filename stem (no extension)."""
    text = _INVALID_FILENAME.sub(' ', str(title or ''))
    text = _WHITESPACE.sub(' ', text).strip(' .')
    if not text or text in ('.', '..'):
        text = fallback
    text = _trim_utf16(text, max_units).strip(' .') or fallback
    # Device names are reserved even when followed by an extension.
    first_part = text.split('.', 1)[0].strip().casefold()
    if first_part in WINDOWS_RESERVED:
        text = '_' + text
    return text or fallback


def _name_key(name):
    return unicodedata.normalize('NFC', str(name)).casefold()


def _unique_name_parts(base, extension, used):
    number = 1
    while True:
        stem = base if number == 1 else f'{base} ({number})'
        name = f'{stem}.{extension}'
        if _name_key(name) not in used:
            yield name
        number += 1


def _title_stem(title, extension):
    """Drop one matching extension so ``aux.txt`` does not become ``aux.txt.txt``."""
    text = str(title or '').rstrip(' .')
    suffix = '.' + str(extension).lstrip('.')
    if text.casefold().endswith(suffix.casefold()):
        text = text[:-len(suffix)]
    return text


def _write_unique(directory, title, extension, text, encoding, used, ordinal):
    base = sanitize_filename(_title_stem(title, extension), fallback=f'字幕-{ordinal}')
    for name in _unique_name_parts(base, extension, used):
        path = Path(directory) / name
        key = _name_key(name)
        if key in used or path.exists():
            used.add(key)
            continue
        try:
            with path.open('x', encoding=encoding, newline='') as file:
                file.write(text)
        except FileExistsError:
            used.add(key)
            continue
        except BaseException:
            # Never leave a half-written file behind after a failed export.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        used.add(key)
        return path


def export_single_file(history, session_id, path, content='bilingual'):
    """Export one session to an explicitly chosen path (legacy API).

    The caller chose this exact path, so normal overwrite/confirmation rules of
    the native save dialog apply.  Batch exports use the no-overwrite writer.
    """
    session = history.session(session_id)
    if not session:
        raise ValueError('请选择一条历史记录')
    path = Path(path)
    target = format_for_suffix(path.suffix)
    if not target:
        raise ValueError('请选择 TXT、SRT、VTT、CSV 或 JSON 格式')
    rows = history.rows(session_id)
    text, encoding = render_text(target.key, session, rows, content)
    path.write_text(text, encoding=encoding)
    return len(rows)


def export_sessions(history, session_ids, directory, format_key='srt', content='bilingual'):
    """Export each selected session into its own uniquely named file.

    Returns a plain dictionary so a dialog can report per-file failures and
    counts without tying the storage layer to Qt.
    """
    target = export_format(format_key)
    if not target:
        return {'directory': str(directory), 'format': format_key, 'content': content,
            'files': [], 'failures': [], 'segment_count': 0,
            'fatal': '未知的导出格式'}
    if content not in CONTENT_LABELS:
        content = 'bilingual'
    directory = Path(directory)
    result = {'directory': str(directory), 'format': target.key, 'content': content,
        'files': [], 'failures': [], 'segment_count': 0, 'fatal': ''}
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        result['fatal'] = f'无法创建导出文件夹：{error}'
        return result
    try:
        used = {_name_key(entry.name) for entry in directory.iterdir()}
    except OSError:
        used = set()
    seen = set()
    for ordinal, session_id in enumerate(session_ids, 1):
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        session = history.session(session_id)
        if not session:
            result['failures'].append({'session_id': session_id, 'title': session_id,
                'error': '记录已不存在，可能已被删除'})
            continue
        rows = history.rows(session_id)
        try:
            text, encoding = render_text(target.key, session, rows, content)
            path = _write_unique(directory, session['title'], target.extension,
                text, encoding, used, ordinal)
        except (OSError, ValueError, TypeError) as error:
            result['failures'].append({'session_id': session_id, 'title': session['title'],
                'error': str(error) or error.__class__.__name__})
            continue
        result['files'].append({'session_id': session_id, 'title': session['title'],
            'path': str(path), 'count': len(rows)})
        result['segment_count'] += len(rows)
    return result


def result_summary(result):
    """Compact, user-facing summary for a batch export result."""
    files = result.get('files', [])
    failures = result.get('failures', [])
    total = result.get('segment_count', 0)
    if result.get('fatal'):
        return result['fatal']
    parts = [f'已导出 {len(files)} 个文件，共 {total} 段字幕。']
    if files:
        parts.append('保存到：' + result.get('directory', ''))
    if failures:
        parts.append(f'有 {len(failures)} 个文件未导出：')
        for item in failures[:8]:
            parts.append(f'· {item.get("title", "记录")}：{item.get("error", "未知错误")}')
        if len(failures) > 8:
            parts.append(f'· 另有 {len(failures) - 8} 个失败')
    return '\n'.join(parts)
