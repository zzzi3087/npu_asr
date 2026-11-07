"""Bounded asynchronous subtitle search over the complete saved corpus."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from subtitle_search import search_corpus


class _SearchBridge(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class SearchDialog(QDialog):
    """Modeless search window; the owning Window handles opened hits."""

    hitActivated = Signal(str, str)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle('搜索字幕')
        self.setMinimumSize(620, 460)
        self.resize(820, 620)
        self._request_id = 0
        self._cancel = threading.Event()
        self._bridges = {}
        self._threads = set()
        self._opening = False
        self._last_response = None

        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜索全部保存记录中的原文和译文…')
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName('字幕全文搜索')
        layout.addWidget(self.query)

        options = QHBoxLayout()
        options.addWidget(QLabel('匹配'))
        self.mode = QComboBox()
        self.mode.addItem('精确 · 字面子串', 'exact')
        self.mode.addItem('模糊 · 错字 / 漏字', 'fuzzy')
        self.mode.setToolTip('精确：不区分拉丁字母大小写，按完整字符片段匹配；% 和 _ 按普通字符处理。\n'
            '模糊：允许少量错字、漏字或多余字符；英文按单词近似，中文按连续字符近似。')
        options.addWidget(self.mode, 1)
        options.addWidget(QLabel('范围'))
        self.scope = QComboBox()
        self.scope.addItem('原文 + 译文', 'all')
        self.scope.addItem('仅原文', 'source')
        self.scope.addItem('仅译文', 'translation')
        options.addWidget(self.scope, 1)
        layout.addLayout(options)

        self.semantics = QLabel('精确匹配按字面子串查找，不区分拉丁字母大小写，% 与 _ 不是通配符。'
            '模糊匹配也包含精确命中，并允许少量错字、漏字、多余字符或重音差异。')
        self.semantics.setObjectName('muted')
        self.semantics.setWordWrap(True)
        layout.addWidget(self.semantics)

        self.status = QLabel('输入文字后搜索全部已保存记录。')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.results = QListWidget()
        self.results.setObjectName('searchResults')
        self.results.setWordWrap(True)
        self.results.setAlternatingRowColors(True)
        self.results.setAccessibleName('字幕搜索结果')
        layout.addWidget(self.results, 1)

        buttons = QHBoxLayout()
        hint = QLabel('单击结果，或选中后按 Enter，打开对应记录并定位到该句。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        buttons.addWidget(hint, 1)
        self.open_button = QPushButton('打开所选结果')
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.activate_current)
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(260)
        self.debounce.timeout.connect(self._start_search)
        self.query.textChanged.connect(self._schedule)
        self.query.returnPressed.connect(self._start_search)
        self.mode.currentIndexChanged.connect(self._schedule)
        self.scope.currentIndexChanged.connect(self._schedule)
        self.results.itemClicked.connect(self.activate_item)
        self.results.itemActivated.connect(self.activate_item)
        self.results.currentItemChanged.connect(lambda current, _old: self.open_button.setEnabled(current is not None))

    def showEvent(self, event):
        super().showEvent(event)
        self.query.setFocus()
        self._start_search()

    def _schedule(self, *_):
        self._request_id += 1
        self._cancel.set()
        self._cancel = threading.Event()
        if not self.query.text().strip():
            self._clear_results('输入文字后搜索全部已保存记录。')
            return
        # Old rows are stale as soon as the query/mode/scope changes, not only
        # when the debounced worker eventually starts.
        self._last_response=None
        self._clear_results('正在搜索…')
        self.debounce.start()

    def _clear_results(self, status):
        self.results.clear()
        self.status.setText(status)
        self.open_button.setEnabled(False)

    def _start_search(self):
        self.debounce.stop()
        query = self.query.text().strip()
        self._request_id += 1
        request_id = self._request_id
        self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel
        if not query:
            self._clear_results('输入文字后搜索全部已保存记录。')
            return
        self.results.clear()
        self.open_button.setEnabled(False)
        self.status.setText('正在搜索全部已保存记录…')
        self._threads = {thread for thread in self._threads if thread.is_alive()}
        bridge = _SearchBridge(self)
        bridge.finished.connect(self._finished)
        bridge.failed.connect(self._failed)
        self._bridges[request_id] = bridge
        thread = threading.Thread(target=self._work,
            args=(request_id, query, self.mode.currentData(), self.scope.currentData(), cancel, bridge),
            name='subtitle-search', daemon=True)
        self._threads.add(thread)
        thread.start()

    def _work(self, request_id, query, mode, scope, cancel, bridge):
        try:
            response = search_corpus(self.history.path, query, mode, scope, limit=200, cancel=cancel)
        except Exception as error:  # surfaced in the dialog, never on the UI thread
            bridge.failed.emit(request_id, str(error) or error.__class__.__name__)
            return
        bridge.finished.emit(request_id, response)

    def _finished(self, request_id, response):
        self._bridges.pop(request_id, None)
        if request_id != self._request_id or response.cancelled:
            return
        self._last_response = response
        if self.mode.currentData() != response.mode or self.scope.currentData() != response.scope:
            return
        self.render_response(response)

    def _failed(self, request_id, error):
        self._bridges.pop(request_id, None)
        if request_id != self._request_id:
            return
        self._clear_results('搜索未能完成：' + error)

    def render_response(self, response):
        self.results.setUpdatesEnabled(False)
        try:
            self.results.clear()
            for hit in response.hits:
                title = hit.get('session_title') or '(无标题记录)'
                matched = '原文' if hit['matched'] == ['en'] else ('译文' if hit['matched'] == ['zh'] else '原文 + 译文')
                lines = [f'{title}  ·  {hit["timestamp"]}  ·  {matched}']
                if hit.get('en_snippet'):
                    lines.append('原文  ' + hit['en_snippet'])
                if hit.get('zh_snippet'):
                    lines.append('译文  ' + hit['zh_snippet'])
                item = QListWidgetItem('\n'.join(lines))
                item.setData(Qt.ItemDataRole.UserRole, {'session_id': hit['session_id'],
                    'segment_id': hit['segment_id'], 'seq': hit['seq']})
                item.setData(Qt.ItemDataRole.AccessibleTextRole,
                    f'{title}，{hit["timestamp"]}，{matched}。{hit.get("en") or ""} {hit.get("zh") or ""}')
                item.setToolTip(f'{title} · {hit["timestamp"]}\n原文：{hit.get("en") or "（缺失）"}\n译文：{hit.get("zh") or "（缺失）"}')
                item.setSizeHint(QSize(0, 76))
                self.results.addItem(item)
            if response.hits:
                self.results.setCurrentRow(0)
                suffix = ' 仅显示前 200 条，请输入更具体的关键词。' if response.truncated else ''
                self.status.setText(f'找到 {len(response.hits)} 条结果 · {response.duration_ms:.0f} ms。{suffix}')
            else:
                self.status.setText('没有匹配的字幕。可以切换为模糊匹配，或缩短关键词。')
            self.open_button.setEnabled(bool(response.hits))
        finally:
            self.results.setUpdatesEnabled(True)

    def activate_current(self):
        self.activate_item(self.results.currentItem())

    def activate_item(self, item):
        if item is None or self._opening:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        self._opening = True
        try:
            self.hitActivated.emit(str(data['session_id']), str(data['segment_id']))
            self.hide()
        finally:
            self._opening = False

    def invalidate(self, rerun=False):
        """Drop results from a changed/deleted history; optionally rerun."""
        self._request_id += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self._last_response = None
        if rerun and self.isVisible() and self.query.text().strip():
            self._start_search()
        else:
            self._clear_results('历史记录已变化，请重新搜索。')

    def closeEvent(self, event):
        self._request_id += 1
        self._cancel.set()
        super().closeEvent(event)
