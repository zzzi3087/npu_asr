"""End-to-end export and full-corpus search checks using only temporary data.

No real history database, credentials, audio device, network or model is read.
The UI runs offscreen.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPORARY = tempfile.TemporaryDirectory(prefix='library-export-search-', ignore_cleanup_errors=True)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PARAKEET_DATA_DIR'] = str(Path(TEMPORARY.name) / 'data')
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import app as app_module
from storage import History, default_settings, export_session, save_settings
from subtitle_export import (export_sessions, sanitize_filename, result_summary)
from subtitle_search import SearchResponse, search_corpus

qt = QApplication([])


def settle(ms=40):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(predicate, timeout=10.0, step=40):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        settle(step)
        if predicate():
            return True
    return False


def add_cue(history, sid, en, zh='', start=0, status='done'):
    ident = history.add(sid, en, start, start + 900, status)
    history.update(ident, 'done' if zh else status, zh)
    return ident


class Messages:
    def __init__(self):
        self.items = []

    def info(self, _parent, _title, text, *args, **kwargs):
        self.items.append(('info', text))

    def warning(self, _parent, _title, text, *args, **kwargs):
        self.items.append(('warning', text))


# ---------------------------------------------------------------------------
# Pure export formatting and filename behavior.
def test_export_formatting():
    root = Path(TEMPORARY.name) / 'formatting'
    history = History(root / 'history.sqlite3')
    sid = history.create_session('META-TITLE-XYZ', default_settings()['profiles'][0])
    one = add_cue(history, sid, 'Hello one', '你好一', 5000)
    add_cue(history, sid, 'Hello two', '', 8000)
    history.update(one, 'done', '你好一')

    out = root / 'txt'
    result = export_sessions(history, [sid], out, 'txt', 'bilingual')
    assert not result['failures'] and len(result['files']) == 1, result
    path = Path(result['files'][0]['path'])
    text = path.read_text('utf-8')
    assert text == 'Hello one\n你好一\n\nHello two\n', repr(text)
    assert 'META-TITLE-XYZ' not in text
    assert '2026-' not in text and '00:00:05' not in text and '[' not in text
    assert '无译文' not in text and 'None' not in text
    assert path.read_bytes()[:3] != b'\xef\xbb\xbf'

    source = export_sessions(history, [sid], root / 'source', 'txt', 'source')['files'][0]
    source_text = Path(source['path']).read_text('utf-8')
    assert 'Hello one\n\nHello two\n' == source_text
    assert '你好一' not in source_text
    translated = export_sessions(history, [sid], root / 'translated', 'txt', 'translation')['files'][0]
    translated_text = Path(translated['path']).read_text('utf-8')
    assert '你好一\n' == translated_text
    assert 'Hello two' not in translated_text

    language_sid = history.create_session('Language-only semantics', default_settings()['profiles'][0])
    add_cue(history, language_sid, '', '只有译文', 1000)
    add_cue(history, language_sid, 'Only source', '', 2000)
    language_source = export_sessions(history, [language_sid], root / 'language-source', 'txt', 'source')['files'][0]
    assert Path(language_source['path']).read_text('utf-8') == 'Only source\n'
    language_translation = export_sessions(history, [language_sid], root / 'language-translation', 'txt', 'translation')['files'][0]
    assert Path(language_translation['path']).read_text('utf-8') == '只有译文\n'
    language_bilingual = export_sessions(history, [language_sid], root / 'language-bilingual', 'txt', 'bilingual')['files'][0]
    assert Path(language_bilingual['path']).read_text('utf-8') == '只有译文\n\nOnly source\n'

    srt = export_sessions(history, [sid], root / 'srt', 'srt', 'bilingual')
    srt_text = Path(srt['files'][0]['path']).read_text('utf-8-sig')
    assert '00:00:05,000 --> 00:00:05,900' in srt_text
    assert 'Hello one\n你好一' in srt_text
    assert srt_text.index('Hello one') < srt_text.index('Hello two')
    translated_srt = export_sessions(history, [sid], root / 'srt-translated', 'srt', 'translation')
    translated_srt_text = Path(translated_srt['files'][0]['path']).read_text('utf-8-sig')
    assert '你好一' in translated_srt_text and 'Hello two' not in translated_srt_text
    assert '2\n00:00:08,000' not in translated_srt_text

    vtt = export_sessions(history, [sid], root / 'vtt', 'vtt', 'source')
    assert Path(vtt['files'][0]['path']).read_text('utf-8').startswith('WEBVTT\n')
    csv_result = export_sessions(history, [sid], root / 'csv', 'csv', 'source')
    csv_text = Path(csv_result['files'][0]['path']).read_text('utf-8-sig')
    assert csv_text.startswith('序号,开始毫秒,结束毫秒,英文,中文,状态,边界待核')
    json_result = export_sessions(history, [sid], root / 'json', 'json', 'source')
    payload = json.loads(Path(json_result['files'][0]['path']).read_text('utf-8'))
    assert payload['version'] == 1 and payload['session']['id'] == sid and len(payload['segments']) == 2

    legacy = root / 'legacy.txt'
    assert export_session(history, sid, legacy) == 2
    assert legacy.read_text('utf-8').startswith('Hello one\n你好一\n')

    original_con = root / 'existing'
    original_con.mkdir()
    (original_con / '_CON.txt').write_text('keep me', 'utf-8')
    first = history.create_session('CON', default_settings()['profiles'][0])
    add_cue(history, first, 'first', '', 0)
    second = history.create_session('CON', default_settings()['profiles'][0])
    add_cue(history, second, 'second', '', 0)
    batch = export_sessions(history, [first, second], original_con, 'txt', 'source')
    names = sorted(Path(item['path']).name for item in batch['files'])
    assert names == ['_CON (2).txt', '_CON (3).txt'], names
    assert (original_con / '_CON.txt').read_text('utf-8') == 'keep me'
    assert result_summary(batch).startswith('已导出 2 个文件，共 2 段字幕。')

    case_dir = root / 'case'
    case_dir.mkdir()
    (case_dir / 'Report.txt').write_text('existing', 'utf-8')
    upper = history.create_session('Report', default_settings()['profiles'][0])
    lower = history.create_session('report', default_settings()['profiles'][0])
    add_cue(history, upper, 'upper', '', 0)
    add_cue(history, lower, 'lower', '', 0)
    case_result = export_sessions(history, [upper, lower], case_dir, 'txt', 'source')
    case_names = sorted(Path(item['path']).name for item in case_result['files'])
    assert case_names == ['Report (2).txt', 'report (3).txt'], case_names

    unicode_dir = root / 'unicode-normalization'
    unicode_dir.mkdir()
    (unicode_dir / 'Café.txt').write_text('existing composed', 'utf-8')
    decomposed = history.create_session('Café', default_settings()['profiles'][0])
    add_cue(history, decomposed, 'decomposed', '', 0)
    unicode_result = export_sessions(history, [decomposed], unicode_dir, 'txt', 'source')
    assert (unicode_dir / 'Café.txt').read_text('utf-8') == 'existing composed'
    assert Path(unicode_result['files'][0]['path']).name != 'Café.txt'

    suffix_dir = root / 'same-suffix'
    aux = history.create_session('aux.txt', default_settings()['profiles'][0])
    nul = history.create_session('NUL.txt', default_settings()['profiles'][0])
    add_cue(history, aux, 'aux', '', 0)
    add_cue(history, nul, 'nul', '', 0)
    suffix_result = export_sessions(history, [aux, nul], suffix_dir, 'txt', 'source')
    suffix_names = sorted(Path(item['path']).name for item in suffix_result['files'])
    assert suffix_names == ['_NUL.txt', '_aux.txt'], suffix_names
    assert not any(name.endswith('.txt.txt') for name in suffix_names)

    long_stem = sanitize_filename('汉' * 200 + '😀' * 20)
    assert len(long_stem.encode('utf-16-le')) // 2 <= 120
    assert sanitize_filename('CON.txt') == '_CON.txt'
    assert sanitize_filename('aux ') == '_aux'
    assert sanitize_filename('a/b:c*?') == 'a b c'
    history.close()
    return dict(txt_exact=True, genuine_language_only=True, formats=True, collisions=True,
        reserved_names=True, same_suffix_not_doubled=True, casefold_collisions=True,
        unicode_normalization_collisions=True, no_overwrite=True, unicode_length=True)


# ---------------------------------------------------------------------------
# Old schema remains searchable/exportable after the additive migration.
def test_legacy_database():
    root = Path(TEMPORARY.name) / 'legacy-db'
    root.mkdir()
    path = root / 'history.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
            ended_at TEXT, provider TEXT NOT NULL, model TEXT NOT NULL);
        CREATE TABLE segments (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            seq INTEGER NOT NULL, created_at TEXT NOT NULL, start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL, en TEXT NOT NULL, zh TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', uncertain INTEGER NOT NULL DEFAULT 0,
            UNIQUE(session_id, seq));
    ''')
    db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
        ('legacy', 'Legacy Session', '2024-01-02T03:04:05.000+00:00', '2024-01-02T03:05:05.000+00:00', 'p', 'm'))
    db.execute('INSERT INTO segments (id,session_id,seq,created_at,start_ms,end_ms,en,zh,status) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        ('legacy-seg', 'legacy', 1, '2024-01-02T03:04:06.000+00:00', 1000, 2000,
         'legacy needle C++ 100%', '旧记录译文', 'done'))
    db.commit()
    db.close()
    history = History(path)
    response = search_corpus(history.path, 'legacy needle C++ 100%', 'exact')
    assert len(response.hits) == 1 and response.hits[0]['session_title'] == 'Legacy Session'
    result = export_sessions(history, ['legacy'], root / 'out', 'txt', 'bilingual')
    text = Path(result['files'][0]['path']).read_text('utf-8')
    assert text == 'legacy needle C++ 100%\n旧记录译文\n'
    assert search_corpus(history.path, '舊記錄', 'exact') .hits == []
    history.close()
    return dict(legacy_schema_opened=True, legacy_search=True, legacy_export=True)


# ---------------------------------------------------------------------------
# Search semantics: exact punctuation/wildcards, translated text, typo/missing
# character matching for English and CJK.
def test_search_semantics():
    root = Path(TEMPORARY.name) / 'semantics'
    history = History(root / 'history.sqlite3')
    profile = default_settings()['profiles'][0]
    first = history.create_session('Search title alpha', profile)
    add_cue(history, first, 'C++ uses 100% of match_test [alpha] & final.', 'C++ 使用 100% 的 match_test [alpha]。', 1000)
    add_cue(history, first, 'The experiment searches for dark matter.', '实验探索暗物质与轴子的起源。', 3000)
    add_cue(history, first, 'A quantum computer computes quickly.', '量子计算机快速进行计算。', 5000)
    add_cue(history, first, 'A cafe with crème brûlée.', '咖啡馆与焦糖布丁。', 7000)
    add_cue(history, first, 'receive the message today.', '今天收到消息。', 9000)
    add_cue(history, first, 'from the start of the line.', '从行首开始。', 11000)
    add_cue(history, first, 'CJK boundary swap fixture.', '实验探索暗物质与轴子。', 13000)
    add_cue(history, first, 'Mixed Café 量子计算机 100% content.', '混合 Café 量子计算机 100%。', 15000)
    add_cue(history, first, 'Café au lait', '分解重音咖啡。', 17000)
    add_cue(history, first, '각 test', '分解韩文测试。', 19000)
    add_cue(history, first, 'x' * 100 + ' Café au lait padded ' + 'y' * 100, '长上下文分解重音。', 21000)
    second = history.create_session('Search title beta', profile)
    add_cue(history, second, 'Only a translated phrase is here.', '这里藏着独特译句。', 1000)

    expected = {
        '100%': first, 'C++': first, 'match_test': first, '_': first,
        '[alpha]': first, '100% of': first, 'unique-no-match': None,
    }
    for query, sid in expected.items():
        response = search_corpus(history.path, query, 'exact')
        if sid is None:
            assert response.hits == [], (query, response.hits)
        else:
            assert any(hit['session_id'] == sid for hit in response.hits), (query, response.hits)
            assert all(hit['timestamp'].startswith('00:00:') for hit in response.hits)
            assert all(hit['en_snippet'] or hit['zh_snippet'] for hit in response.hits)
    translated = search_corpus(history.path, '独特译句', 'exact', scope='translation')
    assert len(translated.hits) == 1 and translated.hits[0]['session_title'] == 'Search title beta'
    assert translated.hits[0]['matched'] == ['zh']
    source_only = search_corpus(history.path, '独特译句', 'exact', scope='source')
    assert source_only.hits == []

    fuzzy_english = search_corpus(history.path, 'experimnt', 'fuzzy', scope='source')
    assert fuzzy_english.hits and fuzzy_english.hits[0]['session_id'] == first
    fuzzy_phrase = search_corpus(history.path, 'quantum computr', 'fuzzy')
    assert fuzzy_phrase.hits and '量子计算机' in fuzzy_phrase.hits[0]['zh']
    fuzzy_cjk = search_corpus(history.path, '暗物制', 'fuzzy', scope='translation')
    assert fuzzy_cjk.hits and fuzzy_cjk.hits[0]['segment_id']
    fuzzy_extra = search_corpus(history.path, '量子计算鸡', 'fuzzy')
    assert fuzzy_extra.hits and fuzzy_extra.hits[0]['session_id'] == first
    fuzzy_accent = search_corpus(history.path, 'creme brulee', 'fuzzy')
    assert fuzzy_accent.hits and fuzzy_accent.hits[0]['session_id'] == first

    # Transpositions can destroy two adjacent anchor pieces; these must pass
    # through the real full-corpus search, not only fuzzy_match().
    transposed = search_corpus(history.path, 'recieve', 'fuzzy', scope='source')
    assert any('receive the message' in hit['en'] for hit in transposed.hits), transposed.hits
    transposed_short = search_corpus(history.path, 'form', 'fuzzy', scope='source')
    assert any('from the start' in hit['en'] for hit in transposed_short.hits), transposed_short.hits
    cjk_swap = search_corpus(history.path, '实验探索物暗质与轴子', 'fuzzy', scope='translation')
    assert any('实验探索暗物质与轴子' in hit['zh'] for hit in cjk_swap.hits), cjk_swap.hits
    mixed_swap = search_corpus(history.path, 'Cafe 量子计机算 100%', 'fuzzy', scope='source')
    assert any('Mixed Café 量子计算机 100%' in hit['en'] for hit in mixed_swap.hits), mixed_swap.hits

    # Whole-string canonical normalization: decomposed e+acute and Hangul jamo
    # must match composed query text while preserving original snippet offsets.
    composed_accent = search_corpus(history.path, 'Café au lait padded', 'exact', scope='source')
    assert composed_accent.hits and composed_accent.hits[0]['en'].startswith('x' * 100)
    assert composed_accent.hits[0]['en_snippet'].startswith('…')
    assert composed_accent.hits[0]['en_snippet'].endswith('…')
    assert 'Café au lait padded' in composed_accent.hits[0]['en_snippet']
    composed_simple = search_corpus(history.path, 'Café au lait', 'exact', scope='source')
    assert any(hit['en'] == 'Café au lait' for hit in composed_simple.hits)
    composed_hangul = search_corpus(history.path, '각', 'exact', scope='source')
    assert composed_hangul.hits and composed_hangul.hits[0]['en'] == '각 test'
    exact_punctuation = search_corpus(history.path, '100%', 'exact')
    assert exact_punctuation.hits and all('100%' in (hit['en'] + hit['zh']) for hit in exact_punctuation.hits)
    wildcard_literal = search_corpus(history.path, '%', 'exact')
    assert wildcard_literal.hits and all('%' in (hit['en'] + hit['zh']) for hit in wildcard_literal.hits)
    underscore_literal = search_corpus(history.path, '_', 'exact')
    assert underscore_literal.hits and all('_' in (hit['en'] + hit['zh']) for hit in underscore_literal.hits)
    history.close()
    return dict(exact_literal=True, punctuation_significant=True, translation_scope=True,
        fuzzy_english_typo=True, fuzzy_english_missing=True, fuzzy_transposition=True,
        fuzzy_cjk=True, fuzzy_cjk_transposition=True, fuzzy_mixed_script=True,
        fuzzy_mixed_unicode=True, fuzzy_accent=True, whole_string_nfkc=True,
        hangul_jamo_composition=True, wildcard_literal=True, snippet_offsets=True)


# ---------------------------------------------------------------------------
# UI integration: search hit outside the latest 100 rows, scroll target, paused
# reader position, stale/deleted result handling, batch export dialog.
def test_ui_integration():
    messages = Messages()
    original_info, original_warning = QMessageBox.information, QMessageBox.warning
    QMessageBox.information = messages.info
    QMessageBox.warning = messages.warning
    original_question = QMessageBox.question
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    settings = default_settings()
    settings.update(translate=False, close_to_tray=False, minimize_to_tray=False)
    settings['desktop_captions']['enabled'] = False
    save_settings(settings)
    window = app_module.Window(start_workers=False)
    window.show()
    settle(100)
    try:
        profile = window.profile()
        target_sid = window.history.create_session('长记录 · 定位测试', profile)
        target_id = None
        for index in range(500):
            en = f'Row {index:03d} general subtitle content.'
            zh = f'第 {index:03d} 句普通字幕内容。'
            if index == 150:
                en = 'Row 150 contains the unique needle target.'
                zh = '第 150 句包含独特暗物制定位词。'
            ident = add_cue(window.history, target_sid, en, zh, index * 1000)
            if index == 150:
                target_id = ident
        window.current_session = window.view_session = target_sid
        window.row_limit = 100
        window.render_now()
        settle(120)
        visible_before = {row['seq'] for row in window.browser.visible_rows()}
        assert 150 not in visible_before and min(visible_before) >= 401

        window.open_search()
        settle(60)
        dialog = window.search_dialog
        assert dialog.isVisible()
        dialog.query.setText('needle target')
        dialog.mode.setCurrentIndex(dialog.mode.findData('exact'))
        dialog._start_search()
        assert wait_until(lambda: dialog._last_response is not None and dialog.results.count() > 0, 10)
        assert '长记录' in dialog.results.item(0).text()
        assert '00:02:30,000' in dialog.results.item(0).text()
        item = dialog.results.item(0)
        QTest.mouseClick(dialog.results.viewport(), Qt.MouseButton.LeftButton,
            pos=dialog.results.visualItemRect(item).center())
        assert wait_until(lambda: window.view_session == target_sid and
            any(row.get('id') == target_id for row in window.browser.visible_rows()), 5)
        settle(120)
        assert not window.browser.following
        assert window.browser.last_revealed_key == target_id
        assert 150 in {row['seq'] for row in window.browser.visible_rows()}
        scroll_position = window.browser.verticalScrollBar().value()
        assert scroll_position > 0, scroll_position

        # Live updates must not yank the paused reader back to the latest tail.
        window.capturing = True
        for index in range(500, 506):
            add_cue(window.history, target_sid, f'Row {index:03d} new live content.', f'新内容 {index}。', index * 1000)
        window.render_now()
        settle(100)
        assert not window.browser.following
        assert 150 in {row['seq'] for row in window.browser.visible_rows()}
        assert 500 not in {row['seq'] for row in window.browser.visible_rows()}
        assert abs(window.browser.verticalScrollBar().value() - scroll_position) <= 3
        window.capturing = False
        window.current_session = None

        # A stale completed worker result must be dropped.
        while True:
            settle(40)
            if dialog._last_response is not None and dialog.results.count():
                break
        previous_text = dialog.results.item(0).text()
        stale = SearchResponse('stale', 'exact', 'all', hits=[{
            'session_id': target_sid, 'session_title': 'STALE', 'segment_id': 'stale',
            'seq': 0, 'start_ms': 0, 'end_ms': 1, 'timestamp': '00:00:00,000',
            'en': 'STALE', 'zh': '', 'en_snippet': 'STALE', 'zh_snippet': '',
            'matched': ['en'], 'score': 1.0}])
        dialog._finished(dialog._request_id - 1, stale)
        assert dialog.results.item(0).text() == previous_text

        # New query clears old rows immediately and results remain cancelable.
        dialog.query.setText('new-needle-that-does-not-exist')
        assert dialog.results.count() == 0
        dialog._start_search()
        assert dialog.results.count() == 0
        assert wait_until(lambda: dialog._last_response is not None and
            dialog._last_response.query == 'new-needle-that-does-not-exist', 10)
        assert dialog.results.count() == 0 and '没有匹配' in dialog.status.text()

        # Deleting the session invalidates a previously visible result set.
        dialog.query.setText('needle target')
        dialog._start_search()
        assert wait_until(lambda: dialog.results.count() > 0, 10)
        window.delete_session(target_sid)
        settle(200)
        assert dialog.results.count() == 0
        assert '变化' in dialog.status.text()
        window.open_search_hit(target_sid, target_id)
        assert '已不存在' in window.notice.text()
        assert window.view_session is None and window.export_button.isEnabled()

        # Batch export dialog: choose records, no overwrite, per-file summary.
        keep_sid = window.history.create_session('CON', profile)
        add_cue(window.history, keep_sid, 'keep one', '保留一', 0)
        same_sid = window.history.create_session('CON', profile)
        add_cue(window.history, same_sid, 'keep two', '保留二', 0)
        unicode_sid = window.history.create_session('字幕/测试:版本?', profile)
        add_cue(window.history, unicode_sid, 'Unicode prize 😀', 'Unicode 译文', 0)
        out = Path(TEMPORARY.name) / 'dialog-export'
        out.mkdir()
        (out / '_CON.txt').write_text('existing sentinel', 'utf-8')
        window.open_export()
        settle(50)
        exporter = window.export_dialog
        assert exporter.isVisible()
        exporter.set_all_checked(False)
        by_sid = {exporter.records.item(i).data(Qt.ItemDataRole.UserRole): exporter.records.item(i)
            for i in range(exporter.records.count())}
        for sid in (keep_sid, same_sid, unicode_sid):
            by_sid[sid].setCheckState(Qt.CheckState.Checked)
        exporter.directory.setText(str(out))
        exporter.format_combo.setCurrentIndex(exporter.format_combo.findData('txt'))
        assert '省略' in exporter.content_combo.itemText(exporter.content_combo.findData('translation'))
        exporter.content_combo.setCurrentIndex(exporter.content_combo.findData('bilingual'))
        exporter.run_export()
        settle(80)
        names = sorted(path.name for path in out.iterdir())
        assert '_CON.txt' in names and '_CON (2).txt' in names and '_CON (3).txt' in names, names
        assert any(name.startswith('字幕 测试 版本') and name.endswith('.txt') for name in names), names
        assert (out / '_CON.txt').read_text('utf-8') == 'existing sentinel'
        unicode_file = next(path for path in out.iterdir() if path.name.startswith('字幕 测试 版本'))
        assert 'Unicode prize 😀\nUnicode 译文\n' == unicode_file.read_text('utf-8')
        assert messages.items and '已导出' in messages.items[-1][1]
        return dict(search_dialog=True, old_hit_outside_100=True, target_scrolled=True,
            paused_position_preserved=True, stale_result_dropped=True,
            new_search_clears=True, deleted_result_invalidated=True,
            batch_dialog=True, dialog_no_overwrite=True, unicode_filename=True,
            per_file_summary=True, keyboard_and_click_activation=True)
    finally:
        QMessageBox.information, QMessageBox.warning = original_info, original_warning
        QMessageBox.question = original_question
        if not window._closed:
            window.request_quit()
            settle(800)


# ---------------------------------------------------------------------------
# A deliberately large corpus exercises debouncing/asynchronous execution and
# a bounded result set without blocking the UI method that starts the search.
def test_scale():
    history = History(Path(TEMPORARY.name) / 'scale.sqlite3')
    profile = default_settings()['profiles'][0]
    sid = history.create_session('Scale session', profile)
    db = history.db
    from storage import utc_now
    with db:
        for index in range(2500):
            db.execute('INSERT INTO segments (id,session_id,seq,created_at,start_ms,end_ms,en,zh,status) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (f'scale-{index}', sid, index + 1, utc_now(), index * 1000, index * 1000 + 900,
                 f'Scale row {index:04d} ordinary transcript text.', f'规模第{index}句普通字幕。', 'done'))
    # search_corpus stays off the UI thread in the dialog; run it directly here
    # as a deterministic upper-bound check for the same query path.
    started = time.perf_counter()
    response = search_corpus(history.path, 'Scale row 2499', 'exact')
    elapsed = time.perf_counter() - started
    assert len(response.hits) == 1 and elapsed < 5.0, elapsed
    started = time.perf_counter()
    fuzzy = search_corpus(history.path, 'Scale row 2489', 'fuzzy')
    fuzzy_elapsed = time.perf_counter() - started
    assert fuzzy.hits and fuzzy_elapsed < 8.0, fuzzy_elapsed

    from search_dialog import SearchDialog
    dialog = SearchDialog(history)
    dialog.show()
    settle(60)
    dialog.query.setText('Scale row 2499')
    started = time.perf_counter()
    dialog._start_search()
    start_elapsed = time.perf_counter() - started
    assert start_elapsed < 0.25, start_elapsed
    assert wait_until(lambda: dialog._last_response is not None and dialog.results.count() == 1, 10)
    assert '1 条结果' in dialog.status.text()
    dialog.close()
    settle(80)
    history.close()
    return dict(scale_cues=2500, exact_ms=round(elapsed * 1000, 1),
        fuzzy_ms=round(fuzzy_elapsed * 1000, 1), async_start_ms=round(start_elapsed * 1000, 1),
        result_bound=True)


def main():
    report = {}
    report['export'] = test_export_formatting()
    report['legacy'] = test_legacy_database()
    report['search'] = test_search_semantics()
    report['ui'] = test_ui_integration()
    report['scale'] = test_scale()
    report['temporary_data_only'] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
