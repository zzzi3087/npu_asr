"""User-facing single and batch subtitle export dialog."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDateTime, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout)

from subtitle_export import (CONTENT_MODES, CONTENT_AWARE_FORMATS,
    FORMATS, export_sessions, result_summary)


def display_date(value):
    date = QDateTime.fromString(str(value or ''), Qt.DateFormat.ISODateWithMs)
    if not date.isValid():
        return ''
    return date.toLocalTime().toString('yyyy-MM-dd HH:mm')


class ExportDialog(QDialog):
    exported = Signal(str)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self._preferred_sid = None
        self.setWindowTitle('导出字幕')
        self.setMinimumSize(640, 560)
        self.resize(700, 620)

        layout = QVBoxLayout(self)
        intro = QLabel('每段历史记录导出为一个文件。文件名会自动避开重复、Windows 保留名和已有文件，不会静默覆盖。')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        selector = QHBoxLayout()
        self.summary = QLabel('')
        self.summary.setObjectName('muted')
        selector.addWidget(self.summary, 1)
        self.select_all = QPushButton('全选')
        self.select_none = QPushButton('全不选')
        self.select_all.clicked.connect(lambda: self.set_all_checked(True))
        self.select_none.clicked.connect(lambda: self.set_all_checked(False))
        selector.addWidget(self.select_all)
        selector.addWidget(self.select_none)
        layout.addLayout(selector)

        self.records = QListWidget()
        self.records.setObjectName('exportRecords')
        self.records.setAccessibleName('选择要导出的历史记录')
        self.records.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.records.setAlternatingRowColors(True)
        self.records.itemChanged.connect(lambda _item: self.update_summary())
        layout.addWidget(self.records, 1)

        self.empty = QLabel('还没有可导出的记录。开始收音或完成文件字幕后，记录会出现在这里。')
        self.empty.setObjectName('muted')
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        destination = QHBoxLayout()
        destination.addWidget(QLabel('保存到'))
        self.directory = QLineEdit()
        self.directory.setReadOnly(True)
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self.directory.setText(documents or str(Path.home()))
        destination.addWidget(self.directory, 1)
        self.browse = QPushButton('选择文件夹…')
        self.browse.clicked.connect(self.choose_directory)
        destination.addWidget(self.browse)
        layout.addLayout(destination)

        options = QHBoxLayout()
        options.addWidget(QLabel('格式'))
        self.format_combo = QComboBox()
        self.format_combo.setAccessibleName('导出格式')
        for item in FORMATS:
            self.format_combo.addItem(item.label, item.key)
        self.format_combo.currentIndexChanged.connect(self.format_changed)
        options.addWidget(self.format_combo, 1)
        options.addWidget(QLabel('内容'))
        self.content_combo = QComboBox()
        self.content_combo.setAccessibleName('导出字幕内容')
        for key, label in CONTENT_MODES:
            self.content_combo.addItem(label, key)
        options.addWidget(self.content_combo, 1)
        layout.addLayout(options)

        self.format_note = QLabel('')
        self.format_note.setObjectName('muted')
        self.format_note.setWordWrap(True)
        layout.addWidget(self.format_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton('取消')
        self.cancel.clicked.connect(self.reject)
        self.export_button = QPushButton('导出所选记录')
        self.export_button.setObjectName('primary')
        self.export_button.clicked.connect(self.run_export)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)
        self.format_changed()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, preferred_sid=None):
        if preferred_sid is not None:
            self._preferred_sid = preferred_sid
        records = self.history.all_sessions()
        previous = {item.data(Qt.ItemDataRole.UserRole) for item in
            (self.records.item(i) for i in range(self.records.count()))
            if item.checkState() == Qt.CheckState.Checked}
        self.records.blockSignals(True)
        self.records.clear()
        any_checked = False
        for index, record in enumerate(records):
            date = display_date(record.get('created_at'))
            suffix = f'  ·  {date}' if date else ''
            current = '  ·  当前' if record['id'] == self._preferred_sid else ''
            item = QListWidgetItem(f'{record["title"]}  ·  {record["count"]} 段{suffix}{current}')
            item.setData(Qt.ItemDataRole.UserRole, record['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = record['id'] in previous
            if not previous and not any_checked:
                checked = record['id'] in ({self._preferred_sid} if self._preferred_sid else {record['id']})
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            if record['id'] == self._preferred_sid:
                item.setToolTip('当前查看的记录')
            self.records.addItem(item)
            any_checked = any_checked or checked
        self.records.blockSignals(False)
        self.empty.setVisible(not records)
        self.export_button.setEnabled(bool(records))
        self.select_all.setEnabled(bool(records))
        self.select_none.setEnabled(bool(records))
        self.summary.setText('没有记录' if not records else f'{len(records)} 条记录')
        self.update_summary()

    def selected_ids(self):
        result = []
        for index in range(self.records.count()):
            item = self.records.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.records.blockSignals(True)
        for index in range(self.records.count()):
            self.records.item(index).setCheckState(state)
        self.records.blockSignals(False)
        self.update_summary()

    def update_summary(self):
        if not self.records.count():
            return
        selected = len(self.selected_ids())
        self.summary.setText(f'已选择 {selected} / {self.records.count()} 条记录')

    def format_changed(self, *_):
        key = self.format_combo.currentData()
        supports_content = key in CONTENT_AWARE_FORMATS
        self.content_combo.setEnabled(supports_content)
        if key == 'txt':
            self.format_note.setText('TXT 是纯 UTF-8 文本，只包含字幕内容：不含标题、日期、时间戳、序号或状态。每段字幕之间空一行。')
        elif key in ('csv', 'json'):
            self.format_note.setText('CSV 与 JSON 保留完整字段和全部译文，内容选项不改变其结构。')
        else:
            self.format_note.setText('字幕格式按所选内容输出。仅原文/仅译文不会混入另一种语言；所选语言为空时省略该句。双语模式缺译文时保留原文，不写入占位文字。')

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, '选择导出文件夹', self.directory.text())
        if path:
            self.directory.setText(path)

    def run_export(self):
        session_ids = self.selected_ids()
        directory = self.directory.text().strip()
        if not session_ids:
            QMessageBox.information(self, '导出字幕', '请至少选择一条历史记录。')
            return
        if not directory:
            self.choose_directory()
            directory = self.directory.text().strip()
            if not directory:
                return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = export_sessions(self.history, session_ids, directory,
                self.format_combo.currentData(), self.content_combo.currentData())
        finally:
            QApplication.restoreOverrideCursor()
        summary = result_summary(result)
        self.exported.emit(summary)
        if result.get('fatal') or result.get('failures'):
            QMessageBox.warning(self, '导出未全部完成', summary)
            return
        QMessageBox.information(self, '导出完成', summary)
        self.accept()
