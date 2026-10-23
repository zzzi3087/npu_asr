"""Parakeet native desktop subtitle workspace."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')
import html
import queue
import sys
import uuid
from PySide6.QtCore import Qt,QTimer,QDateTime,QTimeZone,QLockFile,QEvent,QSize,Signal
from PySide6.QtGui import QFont,QKeySequence,QShortcut,QTextCursor,QTextBlockFormat,QTextCharFormat,QColor
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QToolButton,
    QVBoxLayout,QHBoxLayout,QComboBox,QLineEdit,QListWidget,QListWidgetItem,QTextBrowser,
    QProgressBar,QSplitter,QMenu,QFileDialog,QMessageBox,QInputDialog,QSizePolicy,QCheckBox)
from storage import DATA,History,load_settings,save_settings,export_session,cue_time
from translation import Translator,get_key,validate_profile
from audio_worker import AudioWorker
from settings_dialog import SettingsDialog
from desktop_captions import DesktopCaptions
from desktop_caption_config import normalize_desktop_captions
from switch_button import SwitchButton
from audio_sources import source_label
from history_row import HistoryRow
from workspace_header import WorkspaceHeader
from app_paths import RESOURCE_DIR
from caption_visibility import caption_visibility
from tray_controller import TrayController
from file_queue import FileQueue,LABELS as FILE_LABELS
from PySide6.QtGui import QIcon

from theme import STYLE,apply_theme,normalize_colors,effective_colors,normalize_theme
from system_theme import SystemTheme
from typography import normalize_typography,make_font,render_captions


def local_date(value, pattern='MM月dd日  HH:mm'):
    date=QDateTime.fromString(value,Qt.DateFormat.ISODateWithMs)
    return date.toTimeZone(QTimeZone.systemTimeZone()).toString(pattern)


class Window(QMainWindow):
    exited=Signal()

    def __init__(self,autostart=False,start_workers=True):
        super().__init__()
        self.setWindowTitle('实时字幕')
        self.setWindowIcon(QIcon(str(RESOURCE_DIR/'assets'/'app.ico')))
        self.resize(1220,830)
        self.setMinimumSize(860,620)
        self.settings=load_settings()
        self.system_theme=SystemTheme(self)
        self.system_theme.changed.connect(self.on_system_theme)
        self.colors=effective_colors(self.settings.get('colors'),self.settings.get('theme'),self.system_theme.dark)
        self.appearance={key:self.settings[key] for key in ('typography','english_size','chinese_size')}
        self.history=History(recover=True)
        self.current_session=self.view_session=None
        self.capturing=self.closing=self.ready=False
        self._exit_requested=self._closed=self._in_tray=self._tray_dialog_hidden=False
        self.tray=None
        self.file_queue=FileQueue(self)
        self.file_dialog=None;self._tray_file_hidden=False
        self.file_queue.dispatch.connect(self.begin_file)
        self.file_queue.changed.connect(self.on_file_queue_changed)
        self.file_queue.error.connect(self.notify)
        self.active_source=None
        self.downloading=False
        self.translation_available=None;self.translation_error=''
        self.autostart=autostart
        self.active_jobs=set()
        self.translation_sessions={}
        self.history_rows={}
        self.history_menu_sid=None
        self.source_choices={self.settings['audio_source']['kind']:dict(self.settings['audio_source'])}
        self.session_limit=self.row_limit=100
        self.render_pending=False
        self.workbench_dirty=False
        self.last_settings_page=1
        self._caption_key=self._partial_key=self._empty_key=None
        self.dialog=None
        self.desktop=DesktopCaptions(self.settings.get('desktop_captions'))
        self.desktop.position_changed.connect(self.desktop_position_changed)
        self.desktop.enabled_requested.connect(lambda enabled:self.change_desktop(enabled=enabled))
        self.desktop.locked_requested.connect(lambda locked:self.change_desktop(locked=locked))
        self.desktop.settings_requested.connect(lambda:self.open_settings(page='desktop'))
        self.last_partial=''
        self.worker=AudioWorker(self.settings) if start_workers else None
        self.translator=Translator()
        self.translator.result.connect(self.on_translation)
        self.translator.start()
        self.build_ui()
        self.tray=TrayController(self)
        self.refresh_sessions()
        self.apply_display()
        if self.worker:
            self.worker.event.connect(self.on_event)
            self.worker.start()
        else:
            self.on_event('ready',{})

    def profile(self):
        return next(p for p in self.settings['profiles'] if p['id']==self.settings['selected_profile'])

    def build_ui(self):
        root=QWidget()
        self.setCentralWidget(root)
        outer=QHBoxLayout(root)
        outer.setContentsMargins(0,0,0,0)
        split=QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split)
        sidebar=QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(360)
        side=QVBoxLayout(sidebar)
        self.sidebar_layout=side
        side.setContentsMargins(18,12,18,8)
        side.setSpacing(15)
        self.new_button=QPushButton('＋  新建字幕')
        self.new_button.clicked.connect(self.new_session_view)
        side.addWidget(self.new_button)
        self.files_button=QPushButton('音频文件 · 队列')
        self.files_button.setToolTip('添加本地音频、查看预处理和 NPU 转录进度')
        self.files_button.clicked.connect(self.open_files)
        side.addWidget(self.files_button)
        label=QLabel('历史记录')
        label.setObjectName('eyebrow')
        side.addWidget(label)
        self.search=QLineEdit()
        self.search.setPlaceholderText('搜索标题、中英文…')
        self.search.setClearButtonEnabled(True)
        self.search_timer=QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(self.refresh_sessions)
        self.search.textChanged.connect(lambda:self.search_timer.start())
        side.addWidget(self.search)
        self.sessions=QListWidget()
        self.sessions.setObjectName('historyList')
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sessions.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sessions.itemClicked.connect(self.select_session)
        self.sessions.itemSelectionChanged.connect(self.sync_history_selection)
        self.history_menu=QMenu(self)
        self.history_rename=self.history_menu.addAction('重命名')
        self.history_delete=self.history_menu.addAction('删除')
        self.history_rename.triggered.connect(lambda:self.rename_session(self.history_menu_sid))
        self.history_delete.triggered.connect(lambda:self.delete_session(self.history_menu_sid))
        side.addWidget(self.sessions,1)
        self.more_sessions=QPushButton('加载更多历史')
        self.more_sessions.setObjectName('quietButton')
        self.more_sessions.clicked.connect(self.load_more_sessions)
        side.addWidget(self.more_sessions)
        self.retry_button=QPushButton('重试 / 补译当前会话')
        self.retry_button.setObjectName('quietButton')
        self.retry_button.clicked.connect(self.retry_current)
        side.addWidget(self.retry_button)
        split.addWidget(sidebar)
        content=QWidget()
        main=QVBoxLayout(content)
        self.main_layout=main
        main.setContentsMargins(32,12,32,6)
        main.setSpacing(18)
        title_group=QWidget()
        title_column=QVBoxLayout(title_group)
        title_column.setContentsMargins(0,0,0,0);title_column.setSpacing(2)
        self.title=QLabel('实时字幕')
        self.title.setObjectName('heading')
        self.subtitle=QLabel('本地 NPU 转录，云端多语言翻译')
        self.subtitle.setObjectName('muted')
        self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        title_column.addWidget(self.title)
        title_column.addWidget(self.subtitle)
        controls_widget=QWidget();controls_widget.setObjectName('headerControls')
        controls=QHBoxLayout(controls_widget)
        controls.setContentsMargins(0,0,0,0);controls.setSpacing(6)
        self.badge=QLabel('NPU 加载中')
        self.badge.setObjectName('badge')
        self.badge.setFixedHeight(28)
        self.actions_layout=controls
        self.source_summary=QToolButton()
        self.source_summary.setObjectName('sourceButton')
        self.source_summary.setAccessibleName('选择音频来源')
        self.source_summary.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.source_menu=QMenu(self.source_summary)
        self.source_menu.aboutToShow.connect(self.populate_source_menu)
        self.source_summary.setMenu(self.source_menu)
        controls.addWidget(self.source_summary)
        controls.addStretch(1)
        self.desktop_button=SwitchButton('桌面字幕')
        self.desktop_button.setAccessibleName('桌面字幕显示开关')
        self.desktop_button.setToolTip('仅显示或隐藏桌面字幕 · Ctrl+Shift+D；外观与位置在设置 → 桌面字幕')
        self.desktop_button.toggled.connect(lambda enabled:self.change_desktop(enabled=enabled))
        controls.addWidget(self.desktop_button)
        self.export_button=QToolButton()
        self.export_button.setText('导出  ↗')
        self.export_button.clicked.connect(self.export_current)
        controls.addWidget(self.export_button)
        self.settings_button=QToolButton()
        self.settings_button.setText('设置')
        self.settings_button.setToolTip('打开设置 · Ctrl+,')
        self.settings_button.clicked.connect(self.open_settings)
        controls.addWidget(self.settings_button)
        status_widget=QWidget()
        top=QHBoxLayout(status_widget);top.setContentsMargins(0,0,0,0);top.setSpacing(8)
        self.state=QLabel('正在准备 NPU…')
        self.state.setObjectName('eyebrow')
        self.state.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.state.setWordWrap(True)
        top.addWidget(self.state,1)
        top.addWidget(self.badge)
        self.segment_count=QLabel('')
        self.segment_count.setObjectName('muted')
        top.addWidget(self.segment_count)
        self.start=QPushButton('开始收音')
        self.start.setObjectName('primary')
        self.start.setMinimumWidth(112)
        self.start.setEnabled(False)
        self.start.clicked.connect(self.toggle)
        top.addWidget(self.start)
        self.header=WorkspaceHeader(title_group,controls_widget,status_widget)
        main.addWidget(self.header)
        self.meter=QProgressBar()
        self.meter.setRange(0,100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(3)
        main.addWidget(self.meter)
        self.more_rows=QPushButton('显示更早的字幕')
        self.more_rows.setObjectName('quietButton')
        self.more_rows.clicked.connect(self.load_more_rows)
        self.more_rows.hide()
        self.caption_controls=QWidget();self.caption_controls.setObjectName('captionVisibility')
        display_row=QHBoxLayout(self.caption_controls);display_row.setContentsMargins(0,0,0,0);display_row.setSpacing(14)
        display_row.addWidget(self.more_rows);display_row.addStretch(1)
        self.hide_source=QCheckBox('不显示原文');self.hide_translation=QCheckBox('不显示译文')
        for control in (self.hide_source,self.hide_translation):
            control.setToolTip('同时控制主页历史字幕和桌面字幕；缺少译文时自动保留原文。')
            control.toggled.connect(self.change_caption_visibility);display_row.addWidget(control)
        self.sync_caption_visibility();main.addWidget(self.caption_controls)
        self.browser=QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        main.addWidget(self.browser,1)
        bottom=QWidget()
        bottom_layout=QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0,0,0,0);bottom_layout.setSpacing(4)
        preview=QFrame()
        preview.setObjectName('preview')
        preview_layout=QVBoxLayout(preview)
        self.preview_layout=preview_layout
        preview_layout.setContentsMargins(12,6,12,6)
        caption=QLabel('实时预览')
        caption.setObjectName('eyebrow')
        preview_layout.addWidget(caption)
        self.partial=QTextBrowser()
        self.partial.setPlainText('点击开始收音。')
        self.partial.setFrameShape(QFrame.Shape.NoFrame)
        self.partial.document().setDocumentMargin(0)
        self.partial.setMinimumHeight(32)
        self.partial.setMaximumHeight(240)
        preview_layout.addWidget(self.partial)
        bottom_layout.addWidget(preview)
        self.footer=QFrame();self.footer.setObjectName('workspaceFooter')
        footer=QHBoxLayout(self.footer);footer.setContentsMargins(0,0,0,0);footer.setSpacing(8)
        self.provider_label=QLabel()
        self.provider_label.setObjectName('muted')
        self.provider_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        footer.addWidget(self.provider_label,1)
        self.notice=QLabel('')
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.notice.hide();footer.addWidget(self.notice,1)
        self.notice_timer=QTimer(self);self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.clear_notice)
        self.diagnostics=QLabel('音频不保存')
        self.diagnostics.setObjectName('muted')
        footer.addWidget(self.diagnostics)
        self.translation_warning=QLabel('翻译 API 不可用')
        self.translation_warning.setObjectName('translationWarning')
        self.translation_warning.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.translation_warning.hide();footer.addWidget(self.translation_warning)
        bottom_layout.addWidget(self.footer)
        main.addWidget(bottom)
        split.addWidget(content)
        split.setStretchFactor(0,0)
        split.setStretchFactor(1,1)
        split.setSizes([255,965])
        QShortcut(QKeySequence('Ctrl+E'),self,self.export_current)
        QShortcut(QKeySequence('Ctrl+,'),self,self.open_settings)
        QShortcut(QKeySequence('Ctrl+Shift+D'),self,lambda:self.change_desktop(enabled=not self.desktop.options['enabled']))
        QShortcut(QKeySequence('Ctrl+Shift+L'),self,lambda:self.change_desktop(locked=not self.desktop.options['locked']))
        self.sync_desktop_controls()
        self.refresh_source_summary()
        self.refresh_provider()
        self.render_now()

    def notify(self,text):
        self.notice.setText(text)
        self.notice.setToolTip(text)
        self.provider_label.hide();self.notice.show();self.notice_timer.start(6000)

    def clear_notice(self):
        self.notice.hide();self.provider_label.show()

    def apply_display(self):
        self.appearance={key:self.settings[key] for key in ('typography','english_size','chinese_size')}
        self.sync_caption_visibility()
        self.refresh_source_summary()
        self.desktop.configure(self.settings.get('desktop_captions'))
        self.sync_desktop_controls()
        self.apply_colors(effective_colors(self.settings.get('colors'),self.settings.get('theme'),self.system_theme.dark))
        visible=self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,self.settings['topmost'])
        self.setWindowOpacity(self.settings['opacity']/100)
        if visible:self.show()
        self.render()

    def sync_caption_visibility(self):
        for control,visible in zip((self.hide_source,self.hide_translation),caption_visibility(self.settings)):
            control.blockSignals(True);control.setChecked(not visible);control.blockSignals(False)

    def change_caption_visibility(self,*_):
        old=caption_visibility(self.settings)
        self.settings.update(show_source=not self.hide_source.isChecked(),show_translation=not self.hide_translation.isChecked())
        try:save_settings(self.settings)
        except OSError:
            self.settings.update(zip(('show_source','show_translation'),old));self.sync_caption_visibility()
            self.notify('字幕显示选项未能保存，请检查本机存储空间');return
        # A settings dialog can remain open while the global controls change.
        if self.dialog:
            self.dialog.settings.update(show_source=self.settings['show_source'],show_translation=self.settings['show_translation'])
        self.render_now(force=True)

    def set_translation_status(self,available,detail=''):
        self.translation_available=available
        self.translation_error='' if available is not False else str(detail or '请检查翻译服务地址、模型、密钥或网络')
        self.refresh_translation_warning()

    def refresh_translation_warning(self,rows=None):
        detail=self.translation_error
        if self.translation_available is None and rows and rows[-1]['status']=='failed':
            detail=rows[-1].get('error') or '上次翻译未完成，可在左下角重试或补译'
        visible=bool(self.settings['translate'] and detail)
        self.translation_warning.setVisible(visible)
        self.translation_warning.setToolTip((detail+'；点击左下角重试或补译。') if visible else '')

    def sync_desktop_controls(self):
        options=self.desktop.options
        for control,key in ((self.desktop_button,'enabled'),):
            control.blockSignals(True);control.setChecked(options[key]);control.blockSignals(False)
        self.sync_tray()

    def sync_tray(self):
        if self.tray:self.tray.refresh()

    def hide_to_tray(self):
        if self.closing or not self.tray or not self.tray.available:return False
        if self.dialog and self.dialog.isVisible():
            self._tray_dialog_hidden=True;self.dialog.hide()
        if self.file_dialog and self.file_dialog.isVisible():
            self._tray_file_hidden=True;self.file_dialog.hide()
        self._in_tray=True;self.hide();self.sync_tray()
        return True

    def restore_from_tray(self):
        if self.closing:return
        self._in_tray=False;self.showNormal();self.raise_();self.activateWindow()
        if self._tray_dialog_hidden and self.dialog:
            self._tray_dialog_hidden=False;self.dialog.show()
        if self._tray_file_hidden and self.file_dialog:
            self._tray_file_hidden=False;self.file_dialog.show()
        if self.dialog and self.dialog.isVisible():
            self.dialog.raise_();self.dialog.activateWindow()
        elif self.workbench_dirty:self.render_now(force=True)

    def request_quit(self):
        self._exit_requested=True
        if self.dialog:
            job=self.dialog.save_job
            if job and job.isRunning():
                job.finished.connect(self.request_quit,Qt.ConnectionType.SingleShotConnection)
                return
            if job and self.dialog.result()==SettingsDialog.DialogCode.Accepted:return
            self.dialog.reject()
            # The settings exec() caller performs cleanup before closing us.
            return
        self.close()

    def refresh_source_summary(self):
        if self.file_queue.active:
            job=self.file_queue.find(self.file_queue.active)
            self.source_summary.setText('音频来源：本地文件')
            self.source_summary.setToolTip(job['name']+'；收音设备选择在文件任务结束后生效');return
        self.source_choices[self.settings['audio_source']['kind']]=dict(self.settings['audio_source'])
        source=self.active_source if self.capturing and self.active_source else self.settings['audio_source']
        self.source_summary.setText('音频来源：'+('桌面音频' if source['kind']=='desktop' else '麦克风'))
        detail=source_label(source)+'；点击切换麦克风或桌面音频，具体设备在设置 → 收音设置中选择'
        if self.capturing and source!=self.settings['audio_source']:detail+='（新选择在下次收音生效）'
        self.source_summary.setToolTip(detail)

    def populate_source_menu(self):
        self.source_menu.clear()
        for kind,label in [('microphone','麦克风'),('desktop','桌面音频')]:
            action=self.source_menu.addAction(label)
            action.setCheckable(True);action.setChecked(self.settings['audio_source']['kind']==kind)
            action.triggered.connect(lambda checked=False,k=kind:self.select_audio_source(k))
        self.source_menu.addSeparator()
        self.source_menu.addAction('选择具体设备…').triggered.connect(lambda:self.open_settings(page='audio'))

    def select_audio_source(self,kind):
        if kind not in ('microphone','desktop'):return
        old=dict(self.settings['audio_source'])
        source=dict(self.source_choices.get(kind,{'kind':kind,'device':''}))
        if source==old:return
        self.settings['audio_source']=source
        try:save_settings(self.settings)
        except OSError:
            self.settings['audio_source']=old;self.notify('音频来源未能保存，请检查本机存储空间');return
        self.refresh_source_summary()
        self.notify(('已选择'+source_label(source))+'；'+('下次收音生效' if self.capturing else '点击开始收音即可使用'))

    def change_desktop(self,**changes):
        if self.closing:return
        if self.dialog:
            try:options=self.dialog.desktop_editor.values()
            except ValueError:options=dict(self.desktop.options)
            self.dialog.desktop_editor.set_values(dict(options,**changes),emit=True)
            return
        options=normalize_desktop_captions(dict(self.settings['desktop_captions'],**changes))
        self.settings['desktop_captions']=options
        save_settings(self.settings)
        self.desktop.configure(options)
        self.sync_desktop_controls()
        self.refresh_desktop()

    def desktop_position_changed(self,position):
        if self.dialog:
            self.dialog.desktop_editor.set_position(position)
        else:
            self.settings['desktop_captions'].update(position)
            save_settings(self.settings)

    def preview_desktop(self,options):
        self.desktop.configure(options,preview=True)
        self.sync_desktop_controls()
        self.refresh_desktop()

    def refresh_desktop(self):
        if self.closing:return
        self.desktop.set_visibility(*caption_visibility(self.settings))
        if not self.desktop.options['enabled']:return
        sid=self.current_session if self.capturing else self.view_session
        rows=self.history.rows(sid,self.desktop.options['sentence_count']) if sid else []
        self.desktop.set_pairs([(row['en'],row['zh']) for row in rows])
        self.refresh_desktop_partial()

    def refresh_desktop_partial(self):
        if not self.closing:self.desktop.set_partial(self.last_partial,self.capturing)

    def on_system_theme(self,dark):
        if self.closing:return
        if self.dialog is not None:
            self.dialog.theme_editor.system_changed(dark)
        elif normalize_theme(self.settings.get('theme'))['follow_system']:
            self.apply_colors(effective_colors(self.settings.get('colors'),self.settings.get('theme'),dark),force=False)

    def apply_colors(self,colors,force=True):
        self.colors=normalize_colors(colors)
        t=normalize_typography(self.appearance.get('typography'))
        apply_theme(QApplication.instance(),self.colors,t)
        font=make_font(t,'ui',t['ui_size'])
        if self.browser.document().defaultFont()!=font:self.browser.document().setDefaultFont(font)
        self.main_layout.setContentsMargins(t['content_margin'],min(12,t['content_margin']),t['content_margin'],6)
        self.main_layout.setSpacing(t['layout_gap'])
        self.sidebar_layout.setSpacing(max(4,t['layout_gap']-3))
        self.actions_layout.setSpacing(max(4,min(8,t['layout_gap']//2)))
        self.preview_layout.setSpacing(4)
        self.notice.setStyleSheet(f'color:{self.colors["success"]};padding:0;')
        self.translation_warning.setStyleSheet(f'color:{self.colors["warning"]};padding:0;')
        self.render_now(force=force)

    def preview_typography(self,appearance):
        self.appearance=dict(appearance)
        self.apply_colors(self.colors)

    def change_size(self,amount):
        for field in ('english_size','chinese_size'):
            self.settings[field]=max(12,min(72,self.settings[field]+amount))
        save_settings(self.settings)
        self.apply_display()

    def refresh_provider(self):
        p=self.profile()
        model=self.settings.get('asr_model','parakeet')
        target=self.settings.get('target_language','简体中文')
        self.provider_label.setText(model+' → '+target+' · '+(f'{p["name"]} · {p["model"] or "请填写模型"}' if self.settings['translate'] else '翻译已关闭'))
        self.provider_label.setToolTip(self.provider_label.text())

    def new_session_view(self):
        if self.capturing or self.file_queue.active:
            self.view_session=self.current_session
            self.render()
            return
        self.current_session=self.view_session=None
        self.last_partial=''
        self.sessions.clearSelection()
        self.partial.setText('准备就绪。点击开始收音。' if self.ready else '正在加载 NPU…')
        self.render()

    def refresh_sessions(self):
        records=self.history.sessions(self.search.text(),self.session_limit)
        ids=[record['id'] for record in records]
        old_ids=[self.sessions.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sessions.count())]
        scroll=self.sessions.verticalScrollBar().value()
        self.sessions.blockSignals(True)
        if ids!=old_ids:
            self.history_menu.close()
            self.sessions.clear();self.history_rows={}
            for sid in ids:
                item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,sid)
                item.setSizeHint(QSize(0,68));self.sessions.addItem(item)
                row=HistoryRow(sid)
                row.activated.connect(self.select_session_id)
                row.menu_requested.connect(self.open_history_menu)
                self.sessions.setItemWidget(item,row);self.history_rows[sid]=(item,row)
        for record in records:
            item,row=self.history_rows[record['id']]
            if row.update_record(record['title'],f'{local_date(record["created_at"])}  ·  {record["count"]} 段'):
                item.setToolTip(record['title'])
                item.setData(Qt.ItemDataRole.AccessibleTextRole,record['title'])
                item.setSizeHint(QSize(0,max(68,row.sizeHint().height())))
            item.setSelected(record['id']==self.view_session)
        self.sessions.blockSignals(False)
        self.sync_history_selection()
        self.sessions.verticalScrollBar().setValue(scroll)
        self.more_sessions.setVisible(len(records)>=self.session_limit)
        self.retry_button.setEnabled(bool(self.view_session))

    def sync_history_selection(self):
        for item,row in self.history_rows.values():row.set_selected(item.isSelected())

    def open_history_menu(self,sid,button):
        if not self.history.session(sid):return
        self.history_menu_sid=sid
        self.history_delete.setEnabled(not ((self.capturing or self.file_queue.active) and sid==self.current_session))
        self.history_menu.popup(button.mapToGlobal(button.rect().bottomRight()))

    def select_session_id(self,sid):
        entry=self.history_rows.get(sid)
        if entry:
            self.sessions.setCurrentItem(entry[0]);self.select_session(entry[0])

    def load_more_sessions(self):
        self.session_limit+=100
        self.refresh_sessions()

    def load_more_rows(self):
        self.row_limit+=100
        self.render()

    def select_session(self,item):
        self.view_session=item.data(Qt.ItemDataRole.UserRole)
        self.row_limit=100
        self.retry_button.setEnabled(True)
        self.render()

    def open_files(self):
        if self.closing:return
        if self._in_tray:self.restore_from_tray()
        if self.file_dialog is None:
            from file_queue_dialog import FileQueueDialog
            self.file_dialog=FileQueueDialog(self)
        self.file_dialog.show();self.file_dialog.raise_();self.file_dialog.activateWindow()

    def on_file_queue_changed(self):
        if self._closed:return
        waiting=sum(job['status']=='queued' for job in self.file_queue.jobs)
        total=waiting+int(bool(self.file_queue.active))
        label='音频文件 · '+(f'{total} 项任务' if total else '队列')
        if self.files_button.text()!=label:self.files_button.setText(label)
        self.sync_tray()

    def begin_file(self,job):
        if not self.worker or self.capturing or self.closing:
            self.file_queue.update(job['id'],'done',{'status':'interrupted','error':'NPU 工作线程当前不可用，可稍后重试'});return
        self.file_queue.set_available(False)
        sid=self.history.create_session('文件 · '+job['name'],self.profile())
        job['session']=sid;self.file_queue.save()
        self.current_session=self.view_session=sid;self.row_limit=100;self.last_partial=''
        self.start.setText('取消文件转录');self.start.setEnabled(True)
        self.new_button.setText('返回文件字幕');self.refresh_source_summary()
        self.state.setText('准备 NPU · '+job['name']);self.badge.setText('文件处理中')
        self.worker.file_cancel.clear();self.worker.download_cancel.clear()
        self.worker.commands.put(('file',{'id':job['id'],'path':job['path'],'options':dict(job['options'])}))
        self.refresh_sessions();self.render();self.sync_tray()

    def cancel_file(self):
        if not self.worker or not self.file_queue.active:return
        self.worker.file_cancel.set();self.worker.download_cancel.set()
        self.start.setEnabled(False);self.state.setText('正在取消文件任务，等待当前 NPU 推理结束…')
        self.sync_tray()

    def on_file_event(self,event):
        if self._closed:return
        ident,kind,value=event['id'],event['kind'],event['value']
        job=self.file_queue.find(ident)
        if not job or ident!=self.file_queue.active:return
        sid=job.get('session')
        if kind=='committed' and sid:
            metadata=dict(value,asr_model=job['options'].get('asr_model','parakeet'),
                target_language=self.settings.get('target_language','简体中文'))
            metadata.setdefault('source_language','English' if metadata['asr_model']=='parakeet' else job['options'].get('source_language','auto'))
            self.history.add(sid,value['text'],value.get('audio_start_ms',0),value.get('audio_end_ms',1000),
                'queued' if self.settings['translate'] else 'disabled',value.get('uncertain',False),metadata)
            self.pump(sid);self.refresh_sessions();self.render()
        elif kind=='metrics':
            self.diagnostics.setText(f'NPU {value["infer_ms"]:.0f} ms · 本地文件')
        elif kind=='progress':
            self.file_queue.update(ident,kind,value)
            if not self.closing and not self.worker.file_cancel.is_set():
                self.state.setText(FILE_LABELS.get(value['stage'],'处理中')+f' · {value["percent"]:.0f}% · '+job['name'])
                self.meter.setValue(round(value['percent']))
                self.badge.setText('●  NPU 识别中' if value['stage']=='transcribing' else '文件处理中')
            self.update_partial()
        elif kind=='analysis':self.file_queue.update(ident,kind,value)
        elif kind=='done':
            self.history.finish(sid);self.file_queue.update(ident,kind,value)
            self.last_partial='';self.start.setText('开始收音');self.start.setEnabled(self.ready and not self.closing)
            self.new_button.setText('＋  新建字幕');self.meter.setValue(0);self.refresh_source_summary()
            if not self.closing:
                self.state.setText(FILE_LABELS.get(value['status'],'文件任务已结束')+' · '+job['name'])
                self.state.setToolTip(value.get('error',''))
                self.badge.setText('●  NPU 就绪' if self.worker.engine_key else '需重新加载模型')
                self.file_queue.set_available(self.ready and not self.capturing)
            self.refresh_sessions();self.render();self.sync_tray()

    def toggle(self):
        if not self.worker:return
        if self.file_queue.active:
            self.cancel_file();return
        if self.downloading:
            self.worker.download_cancel.set();self.autostart=False;self.start.setEnabled(False)
            self.state.setText('正在取消下载…');return
        if self.capturing:
            self.worker.stop_capture.set()
            self.start.setEnabled(False)
            self.state.setText('正在处理末尾音频…')
            return
        if not self.ready:return
        sid=uuid.uuid4().hex
        stamp=QDateTime.currentDateTimeUtc().toTimeZone(QTimeZone.systemTimeZone()).toString('MM月dd日 HH:mm')
        self.history.create_session('字幕 · '+stamp,self.profile(),sid)
        self.current_session=self.view_session=sid
        self.row_limit=100
        self.capturing=True
        self.file_queue.set_available(False)
        self.active_source=dict(self.settings['audio_source']);self.refresh_source_summary()
        self.last_partial=''
        self.new_button.setText('返回实时字幕')
        self.start.setText('停止收音')
        self.refresh_sessions()
        self.render()
        self.worker.commands.put(('start',{'audio_source':dict(self.settings['audio_source']),'quiet':self.settings['quiet'],
            'interval':self.settings['interval'],'segmentation':self.settings.get('segmentation','balanced'),
            'asr_model':self.settings['asr_model'],'source_language':self.settings['source_language'],
            'feature_backend':self.settings.get('feature_backend','numpy'),'domain_bias':self.settings.get('domain_bias')}))

    def on_event(self,kind,value):
        if kind=='file_event':
            self.on_file_event(value);return
        if kind=='download':
            self.downloading=True;self.start.setText('取消下载');self.start.setEnabled(True)
            total=max(1,value['total']);percent=min(100,int(value['completed']*100/total))
            verb='校验' if value['stage'] in ('verify','verified') else '下载'
            self.state.setText(f'{verb} {value["model"]} · {percent}%')
            self.state.setToolTip(value['file'])
            self.meter.setValue(percent)
            self.diagnostics.setText(f'模型 {value["completed"]/1048576:.1f} / {total/1048576:.1f} MB')
        elif kind=='download_done':
            self.downloading=False;self.meter.setValue(0)
            self.start.setText('取消文件转录' if self.file_queue.active else ('停止收音' if self.capturing else '开始收音'))
            self.start.setEnabled((self.ready or bool(self.file_queue.active)) and not self.closing)
            self.diagnostics.setText('音频不保存')
        elif kind=='download_cancelled':
            self.downloading=False;self.state.setText('下载已取消 · 下次可继续');self.notify(str(value))
        elif kind=='status':
            self.state.setText(value)
        elif kind=='ready':
            self.ready=True
            self.badge.setText('●  NPU 就绪')
            self.state.setText('准备就绪')
            self.state.setToolTip('')
            self.partial.setText('点击开始收音。')
            self.start.setEnabled(True)
            if isinstance(value,dict) and value.get('load_error'):
                self.state.setText('模型加载失败 · 可点击开始收音重试，或在设置中切换模型')
                self.state.setToolTip(value['load_error'])
                self.notify('完整原因见状态文字提示及本机 model-load-error.log（设置 → 存储与配置可打开目录）')
                self.badge.setText('需重新加载模型')
                self.autostart=False
                if value.get('download_cancelled'):
                    self.state.setText('下载已取消 · 点击开始收音继续');self.notify(value['load_error'])
            if self.autostart:
                self.autostart=False
                self.toggle()
            self.file_queue.set_available(bool(self.worker) and not self.capturing and not self.closing)
        elif kind=='listening':
            self.state.setText('●  正在收音')
            self.state.setToolTip('')
            self.badge.setText('●  NPU 识别中')
            self.partial.setText('正在聆听…')
        elif kind=='level':
            if not self._in_tray:self.meter.setValue(value)
        elif kind=='partial':
            self.last_partial=value['text']
            self.update_partial()
            self.refresh_desktop_partial()
        elif kind=='committed':
            if not self.current_session:return
            self.history.add(self.current_session,value['text'],value.get('audio_start_ms',0),value.get('audio_end_ms',1000),
                'queued' if self.settings['translate'] else 'disabled',value.get('uncertain',False),
                metadata=dict(value,target_language=self.settings.get('target_language','简体中文')))
            self.pump(self.current_session)
            self.refresh_sessions()
            self.render()
        elif kind=='metrics':
            level=(f' · {value["input_dbfs"]:.0f} → {value["output_dbfs"]:.0f} dBFS' if 'input_dbfs' in value else '')
            self.diagnostics.setText(f'NPU {value["infer_ms"]:.0f} ms · 增益 {value.get("gain_db",0):+.1f} dB'+level)
            self.diagnostics.setToolTip(self.diagnostics.text())
        elif kind=='stopped':
            self.capturing=False
            self.refresh_source_summary()
            self.history.finish(self.current_session)
            self.start.setEnabled(self.ready and not self.closing)
            self.start.setText('开始收音')
            self.new_button.setText('＋  新建字幕')
            self.meter.setValue(0)
            self.last_partial=''
            if not self.state.text().startswith(('错误','下载已取消')):self.state.setText('已停止')
            self.badge.setText('●  NPU 就绪' if not self.worker or self.worker.engine_key else '需重新加载模型')
            self.refresh_sessions()
            self.render()
            self.file_queue.set_available(bool(self.worker) and self.ready and not self.closing)
        elif kind=='error':
            detail=str(value)
            summary=detail.splitlines()[0] if detail else '请检查收音与模型设置'
            self.state.setText('错误 · '+summary[:85]+('…' if len(summary)>85 else ''))
            self.state.setToolTip(detail)
            self.notify(summary[:100]);self.notice.setToolTip(detail)
        if kind in ('ready','listening','stopped','error','download','download_done','download_cancelled'):self.sync_tray()

    def update_partial(self):
        if self._in_tray:
            self.workbench_dirty=True;return
        t=normalize_typography(self.appearance.get('typography'))
        self.partial.setFixedHeight(t['preview_height'])
        if (self.capturing or self.file_queue.active) and self.view_session==self.current_session:
            text=self.last_partial or '正在聆听…'
            if self.file_queue.active:
                job=self.file_queue.find(self.file_queue.active)
                text=FILE_LABELS.get(job['status'],'文件处理中')+' · '+job['name']
            source,translation=caption_visibility(self.settings)
            if not source and not (translation and self.translation_available is False):text='原文已隐藏。'
        elif self.capturing or self.file_queue.active:
            text='正在查看历史。实时收音和自动保存仍在继续。'
        else:
            text='已停止收音。' if self.view_session else '点击开始收音。'
        key=(text,self.colors['preview_text'],tuple((k,v) for k,v in t.items() if k.startswith('preview_')))
        if key==self._partial_key:return
        self._partial_key=key
        self.partial.setStyleSheet(f'color:{self.colors["preview_text"]};font-size:{t["preview_size"]}px;')
        self.partial.setPlainText(text)
        self.partial.document().setDocumentMargin(0)
        cursor=QTextCursor(self.partial.document());cursor.select(QTextCursor.SelectionType.Document)
        block=QTextBlockFormat();block.setLineHeight(t['preview_line']*100,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)
        char=QTextCharFormat();char.setFont(make_font(t,'preview',t['preview_size']));char.setForeground(QColor(self.colors['preview_text']))
        cursor.mergeCharFormat(char)

    def pump(self,sid):
        if self.closing or not self.settings['translate']:return
        queued=self.history.queued(sid,limit=32)
        if not queued:
            self.translation_sessions.pop(sid,None);return
        self.translation_sessions[sid]=None
        try:
            profile=dict(self.profile(),source_language=self.settings.get('source_language','auto'),target_language=self.settings.get('target_language','简体中文'))
            validate_profile(profile)
            key=get_key(profile)
        except Exception:
            # Fail all outstanding rows once, rather than leaving a long file stuck.
            self.history.fail_queued(sid,'翻译设置不完整，请在设置菜单填写服务地址、模型和密钥')
            self.translation_sessions.pop(sid,None)
            self.set_translation_status(False,'翻译设置不完整，请检查服务地址、模型和密钥')
            return
        for row in queued:
            if row['id'] in self.active_jobs:continue
            row_profile=dict(profile,source_language=row.get('source_language') or 'auto')
            try:self.translator.enqueue(sid,row['id'],row['en'],self.history.context(row['id']),row_profile,key)
            except queue.Full:break
            self.active_jobs.add(row['id'])
            self.history.update(row['id'],'pending')
            self.history.translation_source(row['id'],row_profile)

    def pump_waiting(self):
        # File ASR can finish many sessions faster than the network translator.
        # Keep all of them reachable after current_session advances to another file.
        for sid in list(self.translation_sessions):
            self.pump(sid)
            if self.translator.jobs.full():break

    def on_translation(self,sid,ident,text,success):
        if sid.startswith('test:'):
            if self.dialog and self.dialog.isVisible():self.dialog.test_result(text,success)
            else:self.notify(('连接正常 · ' if success else '连接失败 · ')+text)
            return
        self.active_jobs.discard(ident)
        if not self.history.segment(ident):
            # A deleted session must not strand other work behind a formerly full queue.
            for active_sid in {self.current_session,self.view_session}:
                if active_sid:self.pump(active_sid)
            self.pump_waiting()
            return
        self.history.update(ident,'done' if success else 'failed',text if success else '', '' if success else text)
        self.set_translation_status(success,'' if success else text)
        self.pump(sid)
        if self.current_session and self.current_session!=sid:self.pump(self.current_session)
        self.pump_waiting()
        self.refresh_desktop()
        if sid==self.view_session:self.render()

    def retry_current(self):
        if not self.view_session:
            self.notify('请先选择一条历史记录')
            return
        if not self.settings['translate']:
            self.notify('请先在设置中启用中文翻译')
            return
        for row in self.history.retryable(self.view_session):self.history.update(row['id'],'queued')
        self.set_translation_status(None)
        self.pump(self.view_session)
        self.render()
        self.notify('已提交补译；将使用当前设置中的服务与模型')

    def open_settings(self,*_,page=None):
        if self._in_tray:self.restore_from_tray()
        if self.dialog and self.dialog.isVisible():
            if page=='desktop':self.dialog.tabs.setCurrentIndex(self.dialog.desktop_tab)
            elif page=='audio':self.dialog.tabs.setCurrentIndex(1)
            self.dialog.raise_()
            return
        self.dialog=SettingsDialog(self.settings,self)
        self.dialog.theme_editor.dark=self.system_theme.dark
        self.dialog.theme_editor.refresh_mode(emit=False)
        self.dialog.tabs.setCurrentIndex(self.last_settings_page)
        if page=='audio':self.dialog.tabs.setCurrentIndex(1)
        self.dialog.test_requested.connect(self.test_connection)
        self.dialog.theme_preview.connect(self.apply_colors)
        self.dialog.typography_preview.connect(self.preview_typography)
        self.dialog.desktop_preview.connect(self.preview_desktop)
        if page=='desktop':
            self.dialog.tabs.setCurrentIndex(self.dialog.desktop_tab)
            self.preview_desktop(self.dialog.desktop_editor.values())
        if self.worker:self.dialog.gain_preview.connect(self.worker.update_gain)
        if self.dialog.exec()==SettingsDialog.DialogCode.Accepted:
            self.settings=self.dialog.settings
            self.set_translation_status(None)
            if self.worker:self.worker.update_gain(self.settings.get('agc'))
            affected=set()
            for sid,ident,*_ in self.translator.cancel_queued():
                if sid.startswith('test:'):continue
                self.active_jobs.discard(ident)
                self.history.update(ident,'queued' if self.settings['translate'] else 'disabled')
                affected.add(sid)
            self.apply_display()
            self.refresh_provider()
            self.notify('设置已保存')
            if self.current_session:self.pump(self.current_session)
            for sid in affected:self.pump(sid)
        else:
            self.apply_display()
            if self.worker:self.worker.update_gain(self.settings.get('agc'))
        self.last_settings_page=self.dialog.tabs.currentIndex()
        dialog=self.dialog;self.dialog=None
        # exec() hides a QDialog but does not destroy parent-owned widgets.
        # Otherwise every later global theme change repolishes all past dialogs.
        dialog.secrets.clear();dialog.key.clear();dialog.deleteLater()
        self._tray_dialog_hidden=False
        if self._exit_requested:QTimer.singleShot(0,self.close)

    def test_connection(self,profile,key):
        try:self.translator.enqueue('test:'+uuid.uuid4().hex,'test','This is a test of real-time bilingual subtitles.',[],profile,key)
        except queue.Full:self.dialog.test_result('翻译队列较忙，请稍后重试',False)

    def rename_current(self):
        self.rename_session(self.view_session)

    def rename_session(self,sid):
        row=self.history.session(sid) if sid else None
        if not row:return
        text,ok=QInputDialog.getText(self,'重命名记录','记录标题',text=row['title'])
        if ok and text.strip():
            self.history.rename(sid,text.strip()[:160])
            self.refresh_sessions()
            self.render()

    def delete_session(self,sid):
        row=self.history.session(sid) if sid else None
        if not row:return
        if (self.capturing or self.file_queue.active) and sid==self.current_session:
            self.notify('请先停止收音或取消文件任务，再删除当前会话');return
        answer=QMessageBox.question(self,'删除历史记录','删除“'+row['title']+'”及其全部字幕？此操作无法撤销。',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Yes:return
        for _,ident,*_ in self.translator.cancel_queued(session=sid):self.active_jobs.discard(ident)
        self.history.delete_session(sid)
        self.translation_sessions.pop(sid,None)
        if self.current_session==sid:self.current_session=None
        if self.view_session==sid:
            self.view_session=self.current_session if self.capturing else None
            if not self.capturing:self.last_partial=''
        for active_sid in {self.current_session,self.view_session}:
            if active_sid:self.pump(active_sid)
        self.refresh_sessions();self.render();self.notify('已删除历史记录')

    def export_current(self):
        row=self.history.session(self.view_session) if self.view_session else None
        if not row:
            self.notify('请先选择一条历史记录，或开始一段字幕')
            return
        stamp=local_date(row['created_at'],'yyyyMMdd-HHmmss')
        path,chosen=QFileDialog.getSaveFileName(self,'导出当前会话','实时字幕-'+stamp+'.srt',
            '双语字幕 SRT (*.srt);;纯文本 TXT (*.txt);;网页字幕 VTT (*.vtt);;表格 CSV (*.csv);;完整记录 JSON (*.json)')
        if not path:return
        if not os.path.splitext(path)[1]:
            path+='.'+next((s for s in ('srt','txt','vtt','csv','json') if '*.'+s in chosen),'txt')
        try:
            count=export_session(self.history,self.view_session,path)
            self.notify(f'已导出 {count} 段字幕 · '+path)
        except (OSError,ValueError):
            QMessageBox.warning(self,'导出未完成','无法写入文件，请检查保存路径或文件是否正在被其他程序占用。')

    def render(self):
        if not self.render_pending:
            self.render_pending=True
            QTimer.singleShot(100,self.render_now)

    def render_now(self,force=False):
        self.render_pending=False
        if self.closing:return
        self.refresh_desktop()
        if self._in_tray or self.isMinimized() or (self.dialog and self.dialog.isVisible() and not force):
            self.workbench_dirty=True
            return
        self.workbench_dirty=False
        c=self.colors
        sid=self.view_session
        session=self.history.session(sid) if sid else None
        self.retry_button.setEnabled(bool(session))
        if not session:
            self.refresh_translation_warning()
            self._caption_key=None
            self.title.setText('实时字幕')
            self.subtitle.setText('本地 NPU 转录，云端多语言翻译')
            self.subtitle.setToolTip('')
            key=tuple(sorted(c.items()))
            if self._empty_key!=key:
                self._empty_key=key
                self.browser.setHtml(f'<div style="margin-top:85px;text-align:center"><p style="font-size:54px;color:{c["accent"]}">◌</p><p style="font-size:23px;color:{c["heading"]}">准备好开始了</p><p style="color:{c["muted"]};font-size:14px">在顶部选择音频来源，点击开始收音。<br><br>原文在上，译文在下，历史自动保存。</p></div>')
            self.segment_count.setText('')
            self.more_rows.hide()
            self.export_button.setEnabled(False)
            self.update_partial()
            return
        self.export_button.setEnabled(True)
        self._empty_key=None
        self.title.setText('实时字幕' if sid==self.current_session else '历史字幕')
        self.subtitle.setText(session['title']+'  ·  '+local_date(session['created_at'],'yyyy年MM月dd日 HH:mm'))
        self.subtitle.setToolTip(self.subtitle.text())
        rows=self.history.rows(sid,self.row_limit)
        self.refresh_translation_warning(rows)
        count=self.history.db.execute('SELECT COUNT(*) FROM segments WHERE session_id=?',(sid,)).fetchone()[0]
        self.segment_count.setText(f'{count} 段 · 自动保存')
        self.more_rows.setVisible(count>len(rows))
        bar=self.browser.verticalScrollBar()
        position=bar.value()
        follow=position>=bar.maximum()-30
        key=(sid,tuple(tuple(row.get(k) for k in ('id','en','zh','start_ms','uncertain','timing','status','error')) for row in rows),
            tuple(sorted(c.items())),repr(self.appearance),caption_visibility(self.settings))
        if key!=self._caption_key:
            self._caption_key=key
            render_captions(self.browser,rows,c,dict(self.settings,**self.appearance))
        bar.setValue(bar.maximum() if follow else position)
        self.update_partial()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange:
            if self.isMinimized() and not getattr(self,'closing',False) and getattr(self,'settings',{}).get('minimize_to_tray',True):
                QTimer.singleShot(0,self.hide_to_tray)
            elif getattr(self,'workbench_dirty',False) and not self.isMinimized():self.render()

    def closeEvent(self,event):
        if self._closed:event.accept();return
        if not self._exit_requested and not self.closing and self.settings.get('close_to_tray',True):
            if self.hide_to_tray():event.ignore();return
        inventory=getattr(QApplication.instance(),'_audio_inventory',None)
        running=(self.worker and self.worker.isRunning()) or self.translator.isRunning() or (inventory and inventory.isRunning())
        if running:
            if not self.closing:
                self.closing=True
                self.file_queue.pause(True);self.file_queue.set_available(False)
                if self.file_dialog:self.file_dialog.hide()
                if self.worker:
                    self.worker.quit_requested.set()
                    self.worker.download_cancel.set()
                    self.worker.stop_capture.set()
                    self.worker.file_cancel.set()
                    self.worker.commands.put(None)
                self.translator.stop()
                self.sync_tray()
            self.state.setText('正在保存并退出…')
            event.ignore()
            QTimer.singleShot(200,self.close)
        else:
            self.closing=True
            self.desktop.shutdown()
            if self.tray:self.tray.shutdown()
            self.history.finish(self.current_session)
            self.history.close()
            self._closed=True;self.exited.emit()
            event.accept()


def main():
    application=QApplication(sys.argv)
    application.setApplicationName('实时字幕');application.setApplicationDisplayName('实时字幕')
    application.setFont(QFont('Microsoft YaHei UI',10))
    application.setStyleSheet(STYLE)
    application.setQuitOnLastWindowClosed(False)
    DATA.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(DATA/'application.lock'))
    if not lock.tryLock(0):
        from instance_activation import activate_existing
        if activate_existing():return 0
        QMessageBox.information(None,'实时字幕','程序正在运行或启动中，请点击系统托盘图标恢复窗口；退出后可重新打开。')
        return 0
    window=Window('--autostart' in sys.argv,start_workers='--ui-test' not in sys.argv)
    from instance_activation import InstanceActivation
    activation=InstanceActivation(window)
    window.exited.connect(activation.close);window.exited.connect(application.quit)
    window.show()
    return application.exec()


if __name__=='__main__':sys.exit(main())
