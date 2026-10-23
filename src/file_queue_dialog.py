"""Nonmodal local file queue, with per-stage progress and audio measurements."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTreeWidget,
    QTreeWidgetItem,QProgressBar,QCheckBox,QFileDialog,QHeaderView,QComboBox)
from file_queue import LABELS,FINISHED,FILE_MODELS
from languages import LANGUAGES


class FileTree(QTreeWidget):
    paths_dropped=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.setAcceptDrops(True)
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        if event.mimeData().hasUrls():
            self.paths_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]);event.acceptProposedAction()


class FileQueueDialog(QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.window=window;self.queue=window.file_queue;self.items={}
        self.setWindowTitle('本地音频文件 · 实时字幕');self.resize(960,620);self.setMinimumSize(720,480)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,16,20,16);layout.setSpacing(12)
        heading=QLabel('本地音频转录');heading.setObjectName('heading');layout.addWidget(heading)
        hint=QLabel('添加或拖入多个文件，依次检查响度、预处理和 NPU 转录。每个文件单独保存到历史；翻译在后台继续。')
        hint.setWordWrap(True);hint.setObjectName('muted');layout.addWidget(hint)
        asr=QHBoxLayout();asr.addWidget(QLabel('文件转录模型'))
        self.model_combo=QComboBox();self.model_combo.setAccessibleName('本地文件转录模型')
        for name,ident in FILE_MODELS:self.model_combo.addItem(name,ident)
        asr.addWidget(self.model_combo,2);asr.addWidget(QLabel('文件语言'))
        self.language_combo=QComboBox();self.language_combo.setAccessibleName('本地文件转录语言')
        for ident,name,_ in LANGUAGES:self.language_combo.addItem(name,ident)
        asr.addWidget(self.language_combo,1);layout.addLayout(asr)
        self.sync_asr_options()
        self.model_combo.currentIndexChanged.connect(self.change_asr_options)
        self.language_combo.currentIndexChanged.connect(self.change_asr_options)
        self.model_combo.setToolTip('只影响之后添加的文件，已有队列任务保持原模型；不改变实时字幕模型。')
        self.language_combo.setToolTip('文件转录语言独立保存；Parakeet 仅支持英语，其他语言请选择 Qwen/Mega。')
        self.options_label=QLabel();self.options_label.setWordWrap(True);layout.addWidget(self.options_label)
        options=QHBoxLayout()
        self.normalize=QCheckBox('自动调整响度');self.normalize.setChecked(self.queue.preprocessing['normalize'])
        self.normalize.setToolTip('先测量 LUFS、峰值和 RMS；偏离目标时做有峰值保护的恒定增益，目标 −20 LUFS，最多提升 24 dB。')
        self.highpass=QCheckBox('减弱低频隆隆声（80 Hz）');self.highpass.setChecked(self.queue.preprocessing['highpass'])
        self.highpass.setToolTip('可用于明显空调、风声或直流偏置；默认关闭，以免改变原音。')
        for control in (self.normalize,self.highpass):options.addWidget(control);control.toggled.connect(self.save_preprocessing)
        options.addStretch();layout.addLayout(options)
        self.summary=QLabel();self.summary.setWordWrap(True);self.summary.setObjectName('eyebrow');layout.addWidget(self.summary)
        self.tree=FileTree();self.tree.setColumnCount(4);self.tree.setHeaderLabels(['文件','状态','处理进度','模型'])
        self.tree.setRootIsDecorated(False);self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setAlternatingRowColors(True);self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1,105);self.tree.setColumnWidth(2,170);self.tree.setColumnWidth(3,135)
        self.tree.paths_dropped.connect(self.add_paths);self.tree.itemSelectionChanged.connect(self.selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_:self.open_result());layout.addWidget(self.tree,1)
        self.details=QLabel('选择任务可查看响度、峰值、采样率和预处理结果。')
        self.details.setWordWrap(True);self.details.setMinimumHeight(70);self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(self.details)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        self.add_button=QPushButton('＋ 添加文件');self.add_button.setObjectName('primary');self.add_button.clicked.connect(self.choose_files);buttons.addWidget(self.add_button)
        self.pause_button=QPushButton();self.pause_button.clicked.connect(lambda:self.queue.pause(not self.queue.paused));buttons.addWidget(self.pause_button)
        self.cancel_button=QPushButton('取消当前');self.cancel_button.clicked.connect(window.cancel_file);buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.open_button=QPushButton('查看字幕');self.open_button.clicked.connect(self.open_result);buttons.addWidget(self.open_button)
        edits=QHBoxLayout();layout.addLayout(edits)
        self.retry_button=QPushButton('重试');self.retry_button.clicked.connect(lambda:self.queue.retry(self.selected()));edits.addWidget(self.retry_button)
        self.remove_button=QPushButton('移除');self.remove_button.clicked.connect(lambda:self.queue.remove(self.selected()));edits.addWidget(self.remove_button)
        self.up_button=QPushButton('↑');self.up_button.setToolTip('提前处理选中的等待任务');self.up_button.clicked.connect(lambda:self.queue.move(self.selected(),-1));edits.addWidget(self.up_button)
        self.down_button=QPushButton('↓');self.down_button.setToolTip('延后处理选中的等待任务');self.down_button.clicked.connect(lambda:self.queue.move(self.selected(),1));edits.addWidget(self.down_button)
        edits.addStretch()
        clear=QPushButton('清除已结束项');clear.clicked.connect(self.queue.clear_finished);edits.addWidget(clear)
        self.queue.changed.connect(self.on_changed);self.refresh()

    def on_changed(self):
        if self.isVisible():self.refresh()

    def showEvent(self,event):
        super().showEvent(event);self.refresh()

    def save_preprocessing(self):
        self.queue.preprocessing.update(normalize=self.normalize.isChecked(),highpass=self.highpass.isChecked());self.queue.save()

    def sync_asr_options(self):
        key=(self.queue.asr_options['asr_model'],self.queue.asr_options['source_language'])
        if getattr(self,'_asr_key',None)==key and (self.model_combo.currentData(),self.language_combo.currentData())==key:return
        for control,key in ((self.model_combo,'asr_model'),(self.language_combo,'source_language')):
            control.blockSignals(True);control.setCurrentIndex(control.findData(self.queue.asr_options[key]));control.blockSignals(False)
        parakeet=self.queue.asr_options['asr_model']=='parakeet'
        for index in range(self.language_combo.count()):
            self.language_combo.model().item(index).setEnabled(not parakeet or self.language_combo.itemData(index) in ('auto','English'))
        self._asr_key=key

    def change_asr_options(self):
        model=self.model_combo.currentData();language=self.language_combo.currentData()
        if model=='parakeet' and language not in ('auto','English'):language='auto'
        self.queue.set_asr_options(model,language);self.sync_asr_options()

    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'选择本地音频文件','',
            '音频文件 (*.wav *.wave *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif *.amr *.mka *.webm);;含音轨的视频 (*.mp4 *.mkv *.mov *.avi);;所有文件 (*)')
        if paths:self.add_paths(paths)

    def add_paths(self,paths):
        try:
            count=self.queue.add(paths,self.window.settings)
            self.window.notify(f'已添加 {count} 个文件；重复等待任务和无效路径已跳过')
        except (ValueError,OSError) as error:self.window.notify(str(error))

    def selected(self):
        item=self.tree.currentItem();return item.data(0,Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        self.sync_asr_options()
        selected=self.selected();ids=[job['id'] for job in self.queue.jobs]
        if list(self.items)!=ids:
            self.tree.blockSignals(True);self.tree.clear();self.items={}
            for job in self.queue.jobs:
                item=QTreeWidgetItem(self.tree);item.setData(0,Qt.ItemDataRole.UserRole,job['id']);item.setText(0,job['name']);item.setToolTip(0,job['path'])
                progress=QProgressBar();progress.setRange(0,100);progress.setFixedHeight(21)
                self.tree.setItemWidget(item,2,progress);self.items[job['id']]=(item,progress)
                if job['id']==selected:self.tree.setCurrentItem(item)
            self.tree.blockSignals(False)
        for job in self.queue.jobs:
            item,progress=self.items[job['id']];item.setText(1,LABELS.get(job['status'],job['status']));item.setToolTip(1,job.get('error',''))
            item.setText(3,job['options'].get('asr_model','parakeet'))
            progress.setValue(round(job.get('percent',0)));stage=job.get('stage_percent')
            progress.setFormat('%p% · '+(f'阶段 {stage:.0f}%' if stage is not None else '时长未知'))
        pending=sum(job['status']=='queued' for job in self.queue.jobs);done=sum(job['status']=='completed' for job in self.queue.jobs)
        suffix='已暂停，将在当前任务结束后等待' if self.queue.paused else ('等待停止收音或 NPU 就绪' if pending and not self.queue.available else '按顺序处理')
        self.summary.setText(f'共 {len(self.queue.jobs)} 项 · 完成 {done} · 等待 {pending} · {suffix}')
        settings=self.window.settings
        self.options_label.setText(f'文件模型和语言独立保存，只影响新添加的文件；队列中的模型、语言和词表保持不变。译文目标：{settings["target_language"]}，翻译与增益使用设置中的选项。')
        self.pause_button.setText('继续队列' if self.queue.paused else '暂停队列')
        self.cancel_button.setEnabled(bool(self.queue.active) and not self.window.closing)
        self.selection_changed()

    def selection_changed(self):
        job=self.queue.find(self.selected())
        self.retry_button.setEnabled(bool(job and job['id']!=self.queue.active and job['status'] in FINISHED))
        self.remove_button.setEnabled(bool(job and job['id']!=self.queue.active))
        for button in (self.up_button,self.down_button):button.setEnabled(bool(job and job['status']=='queued'))
        self.open_button.setEnabled(bool(job and job.get('session') and self.window.history.session(job['session'])))
        if not job:
            self.details.setText('选择任务可查看响度、峰值、采样率和预处理结果。');return
        lines=[job['name']+' · '+LABELS.get(job['status'],'')]
        stats=job.get('analysis')
        if stats:
            def db(value,suffix):return '无法测量' if value is None else f'{value:.1f} {suffix}'
            lines.append(f'{stats["duration"]:.1f} 秒 · {stats["sample_rate"]} Hz · {stats["channels"]} 声道 · {stats["codec"]}')
            lines.append('响度 '+db(stats['lufs'],'LUFS')+' · 真峰值 '+db(stats['true_peak'],'dBTP')+' · RMS '+db(stats['rms_db'],'dBFS')+f' · 增益 {stats["gain_db"]:+.1f} dB → 16 kHz 单声道')
            lines+=stats.get('warnings',[])
        if job.get('error'):lines.append(job['error'])
        self.details.setText('\n'.join(lines))

    def open_result(self):
        job=self.queue.find(self.selected())
        if job and job.get('session'):
            self.window.restore_from_tray();self.window.select_session_id(job['session']);self.hide()
