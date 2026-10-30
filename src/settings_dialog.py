import copy
import json
import uuid
from PySide6.QtCore import Signal, QUrl, Qt, QThread,QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QWidget,
    QComboBox,QLineEdit,QPlainTextEdit,QLabel,QPushButton,QCheckBox,QSpinBox,QDoubleSpinBox,QDialogButtonBox,QMessageBox,QFileDialog,QScrollArea,QSizePolicy,QAbstractSpinBox)
from storage import DATA, save_settings
from translation import get_key,save_key,validate_profile,endpoint
from theme_editor import ThemeEditor
from typography_editor import TypographyEditor
from config_io import export_configuration,import_configuration
from audio_gain import normalize_agc
from languages import LANGUAGES,TARGETS,ASR_MODELS,PARAKEET_MODELS,supported_language,source_languages,source_language_for_model
from domain_editor import DomainEditor
from desktop_caption_editor import DesktopCaptionEditor
from settings_pages import SettingsPages,Foldout
from audio_source_editor import AudioSourceEditor
from app_paths import VERSION


class SettingsSave(QThread):
    """Validate optional BPE context and persist credentials/config off the UI thread."""
    def __init__(self,settings,secrets,parent=None):
        super().__init__(parent);self.settings=settings;self.secrets=secrets;self.error='';self.error_page=None
    def run(self):
        try:
            from domain_editor import validate_context
            self.error_page=6
            validate_context(self.settings['domain_bias'],self.settings['asr_model'])
            self.error_page=None
            for p in self.settings['profiles']:
                if self.secrets.get(p['id']):save_key(p,self.secrets[p['id']])
            save_settings(self.settings)
        except Exception as error:
            self.error=str(error) if isinstance(error,(ValueError,KeyError)) else '保存失败，请检查凭据管理器和存储目录'
        finally:self.secrets.clear()


def scroll_page(widget):
    area=QScrollArea();area.setWidgetResizable(True);area.setWidget(widget)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
    return area


class SettingsDialog(QDialog):
    test_requested = Signal(object,str)
    theme_preview = Signal(object)
    typography_preview = Signal(object)
    gain_preview = Signal(object)
    desktop_preview = Signal(object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle('设置 · 实时字幕')
        self.setMinimumSize(860,620)
        self.resize(1000,740)
        self.settings = copy.deepcopy(settings)
        self.secrets = {}
        self.extra_text = {}
        self.current = None
        self.loading = False
        self.save_job=None
        root = QVBoxLayout(self)
        root.setContentsMargins(24,20,24,20)
        heading = QLabel('工作台设置')
        heading.setObjectName('dialogHeading')
        heading_row=QHBoxLayout();heading_row.addWidget(heading,1)
        self.app_version_label=QLabel('当前版本：'+VERSION)
        self.app_version_label.setObjectName('muted')
        self.app_version_label.setAccessibleName('当前程序版本')
        self.app_version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        heading_row.addWidget(self.app_version_label)
        root.addLayout(heading_row)
        tabs = SettingsPages()
        self.tabs=tabs
        root.addWidget(tabs,1)
        service = QWidget()
        form = QFormLayout(service)
        form.setContentsMargins(16,20,16,16)
        form.setVerticalSpacing(13)
        self.profile = QComboBox()
        self.profile.currentIndexChanged.connect(self.switch_profile)
        add = QPushButton('＋ 新服务')
        add.clicked.connect(self.new_profile)
        line = QHBoxLayout()
        line.addWidget(self.profile,1)
        line.addWidget(add)
        form.addRow('服务配置',line)
        self.name = QLineEdit()
        form.addRow('服务名称',self.name)
        self.protocol = QComboBox()
        self.protocol.addItem('OpenAI · Chat Completions','openai')
        self.protocol.addItem('Anthropic · Messages','anthropic')
        form.addRow('接口格式',self.protocol)
        self.url = QLineEdit()
        self.url.setPlaceholderText('https://your-api.example/v1 或完整接口地址')
        form.addRow('API 地址',self.url)
        self.model = QLineEdit()
        self.model.setPlaceholderText('填写该服务提供的模型 ID')
        form.addRow('模型 ID',self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('API 密钥',self.key)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(20,24,20,20)
        advanced_form.setVerticalSpacing(18)
        self.auth = QComboBox()
        for text,value in [('自动选择','auto'),('Authorization: Bearer','bearer'),('x-api-key','x-api-key'),('api-key','api-key'),('无需密钥（本地服务）','none')]:
            self.auth.addItem(text,value)
        advanced_form.addRow('认证方式',self.auth)
        self.version = QLineEdit()
        advanced_form.addRow('Anthropic 版本',self.version)
        self.workspace = QLineEdit()
        self.workspace.setPlaceholderText('可选 · anthropic-workspace-id')
        advanced_form.addRow('Anthropic 工作区',self.workspace)
        self.extra = QPlainTextEdit()
        self.extra.setMinimumHeight(150)
        self.extra.setMaximumHeight(220)
        self.extra.setPlaceholderText('{"max_tokens": 1200}')
        advanced_form.addRow('额外参数 · JSON',self.extra)
        advanced_note = QLabel('可填写服务支持的采样、输出长度等参数。model、messages、system 和 stream 由程序管理。')
        advanced_note.setWordWrap(True)
        advanced_note.setObjectName('muted')
        advanced_form.addRow(advanced_note)
        self.enabled = QCheckBox('启用云端中文翻译')
        self.enabled.setText('启用云端字幕翻译')
        self.enabled.setChecked(settings['translate'])
        form.insertRow(0,self.enabled)
        self.progressive=QCheckBox('临时字幕与预译 · 确认后替换')
        self.progressive.setChecked(settings.get('progressive_translation',True))
        self.progressive.setToolTip('有效句号立即临时显示并尝试预译，句号改变时可修订或撤销。自然从句在约 3 秒且前缀稳定后显示，无标点约 6 秒兜底。原文显示独立于翻译是否可用；确认后的整句译文成功后替换。下次收音生效。')
        form.insertRow(1,self.progressive)
        self.progressive_limit=QSpinBox();self.progressive_limit.setRange(1,10)
        self.progressive_limit.setValue(settings.get('progressive_translation_limit',2));self.progressive_limit.setSuffix(' 次')
        self.progressive_limit.setEnabled(self.progressive.isChecked())
        self.progressive.toggled.connect(self.progressive_limit.setEnabled)
        self.progressive_limit.setToolTip('每句最多额外预译的次数，默认 2 次；只限制翻译请求，不限制临时原文的显示修订。整句确认后的翻译不计入。提高次数会增加翻译 API 请求。')
        form.insertRow(2,'每句最多预译',self.progressive_limit)
        self.progressive_interval=QDoubleSpinBox();self.progressive_interval.setRange(.5,5.)
        self.progressive_interval.setSingleStep(.5);self.progressive_interval.setDecimals(1);self.progressive_interval.setSuffix(' 秒')
        self.progressive_interval.setValue(settings.get('progressive_translation_interval',1.))
        self.progressive_interval.setEnabled(self.progressive.isChecked())
        self.progressive.toggled.connect(self.progressive_interval.setEnabled)
        self.progressive_interval.setToolTip('默认 1 秒，只限制预译请求的最短间隔，不限制临时原文的显示修订。不额外运行 ASR；实际预译还取决于识别更新和翻译服务。')
        form.insertRow(3,'预译最短间隔',self.progressive_interval)
        self.test = QPushButton('测试连接')
        self.test.clicked.connect(self.test_connection)
        self.test_status = QLabel('密钥只保存到 Windows 凭据管理器。更换域名不会沿用旧密钥。')
        self.test_status.setWordWrap(True)
        self.test_status.setObjectName('muted')
        row = QHBoxLayout()
        row.addWidget(self.test)
        row.addWidget(self.test_status,1)
        form.addRow(row)
        tabs.addTab(scroll_page(service),'翻译服务')
        display = QWidget()
        appearance = QFormLayout(display);self.appearance=appearance
        appearance.setContentsMargins(20,24,20,20)
        appearance.setVerticalSpacing(20)
        self.audio_source_editor=AudioSourceEditor(settings.get('audio_source'))
        appearance.addRow(self.audio_source_editor)
        self.asr_model=QComboBox()
        for name,value in ASR_MODELS:self.asr_model.addItem(name,value)
        self.asr_model.setCurrentIndex(max(0,self.asr_model.findData(settings.get('asr_model','parakeet'))))
        self.asr_model.setToolTip('仅控制实时收音字幕；本地音频文件请在“音频文件 · 队列”中单独选择模型。')
        appearance.addRow('实时字幕模型',self.asr_model)
        self.source_language=QComboBox()
        for value,label,_ in LANGUAGES:self.source_language.addItem(label,value)
        self.source_language.setCurrentIndex(max(0,self.source_language.findData(settings.get('source_language','auto'))))
        self.source_language.setToolTip('实时收音的源语言；文件转录语言在本地音频界面独立选择。')
        appearance.addRow('实时转录语言',self.source_language)
        self.target_language=QComboBox();self.target_language.setEditable(True)
        for value,label in TARGETS:self.target_language.addItem(label,value)
        selected=settings.get('target_language','简体中文')
        index=self.target_language.findData(selected)
        if index>=0:self.target_language.setCurrentIndex(index)
        else:self.target_language.setEditText(selected)
        form.insertRow(1,'翻译目标语言',self.target_language)
        model_note=QLabel();self.model_note=model_note
        model_note.setWordWrap(True);model_note.setObjectName('muted');appearance.addRow(model_note)
        self.feature_backend=QComboBox()
        self.feature_backend.addItem('CPU · 默认', 'numpy')
        self.feature_backend.addItem('GPU · FP32 · 实验', 'gpu')
        self.feature_backend.setCurrentIndex(max(0,self.feature_backend.findData(settings.get('feature_backend','numpy'))))
        feature_note=QLabel('GPU 选项只处理 Parakeet v2 的频谱特征。全部转录网络仍使用 NPU。整机省电效果需结合实际负载测试。下次收音生效。')
        feature_note.setWordWrap(True);feature_note.setObjectName('muted');self.feature_note=feature_note
        self.typography_editor=TypographyEditor(settings)
        self.typography_editor.changed.connect(self.typography_preview.emit)
        self.en_size=self.typography_editor.fields['english_size']
        self.zh_size=self.typography_editor.fields['chinese_size']
        self.opacity = QSpinBox()
        self.opacity.setRange(40,100)
        self.opacity.setSuffix(' %')
        self.opacity.setValue(settings['opacity'])
        self.topmost = QCheckBox('工作台窗口始终置顶')
        self.topmost.setChecked(settings['topmost'])
        workbench=QWidget();workbench_layout=QVBoxLayout(workbench);window_form=QFormLayout()
        window_form.addRow('工作台不透明度',self.opacity);window_form.addRow(self.topmost)
        workbench_layout.addLayout(window_form);workbench_layout.addWidget(self.typography_editor,1)
        gain_page=QWidget();gain_form=QFormLayout(gain_page);gain_form.setVerticalSpacing(14)
        self.quiet = QCheckBox('轻声检测增强（下次收音生效）')
        self.quiet.setChecked(settings['quiet'])
        gain_form.addRow(self.quiet)
        self.interval = QComboBox()
        for title,value in [('低延迟 · 自适应 0.5 秒','0.5'),('快速 · 自适应 0.75 秒','0.75'),('流畅 · 1 秒','1.0'),('省电 · 1.5 秒','1.5'),('低占用 · 2 秒','2.0')]:
            self.interval.addItem(title,float(value))
        self.interval.setCurrentIndex(max(0,self.interval.findData(settings['interval'])))
        self.interval.setToolTip('0.5/0.75 秒模式仅用于 Parakeet：每句最多提前复核两次（独立于翻译预译次数），推理繁忙时自动放慢。0.5 秒以句尾后一秒内显示/确认为优化目标，不保证每句达到，也不包含云端翻译；可能增加耗电。原省电档保持不变。保存后下次收音生效。')
        appearance.addRow('预览更新间隔',self.interval)
        self.segmentation=QComboBox()
        for title,value in [('均衡 · 标点与停顿','balanced'),('整句优先 · 更少切分','sentence'),('低延迟 · 更快提交','fast')]:
            self.segmentation.addItem(title,value)
        self.segmentation.setCurrentIndex(max(0,self.segmentation.findData(settings.get('segmentation','balanced'))))
        self.segmentation.setToolTip('完整句子优先提交，未完成短语跨停顿拼接。整句优先保留更长上下文；低延迟更早确认停顿。保存后下次收音生效。')
        appearance.addRow('分句策略',self.segmentation)
        self.confirmation_wait=QDoubleSpinBox();self.confirmation_wait.setRange(.5,6.)
        self.confirmation_wait.setSingleStep(.5);self.confirmation_wait.setDecimals(1);self.confirmation_wait.setSuffix(' 秒')
        self.confirmation_wait.setValue(settings.get('confirmation_wait',3.))
        self.confirmation_wait.setToolTip('仅实时 Parakeet v2/v3：默认 3 秒。稳定句子提前提交；降低会更早采用仍可能修正的词，并标记边界待核。是等待目标，不包含识别刷新、推理和翻译耗时；没有可靠句界时仍会保护未完成短语。保存后下次收音生效。')
        appearance.addRow('Parakeet 字幕确认等待上限',self.confirmation_wait)
        agc=normalize_agc(settings.get('agc'))
        self.agc_enabled=QCheckBox('自动动态增益');self.agc_enabled.setChecked(agc['enabled'])
        gain_form.addRow(self.agc_enabled)
        gain_advanced=QWidget();gain_advanced_form=QFormLayout(gain_advanced)
        self.agc_fields={}
        for key,label,lo,hi in [('target_db','目标语音电平',-36,-16),('max_gain_db','最大自动增益',0,48),
            ('manual_db','手动增益偏置',-24,24),('ceiling_db','输出峰值上限',-6,-.2)]:
            field=QDoubleSpinBox();field.setRange(lo,hi);field.setDecimals(1);field.setSuffix(' dBFS' if key in ('target_db','ceiling_db') else ' dB')
            field.setKeyboardTracking(False)
            field.setValue(agc[key]);self.agc_fields[key]=field
            (gain_form if key=='manual_db' else gain_advanced_form).addRow(label,field)
        gain_form.addRow(Foldout('自动增益高级参数',gain_advanced))
        self.agc_enabled.toggled.connect(lambda:self.gain_preview.emit(self.agc_values()))
        for field in self.agc_fields.values():field.valueChanged.connect(lambda:self.gain_preview.emit(self.agc_values()))
        gain_note=QLabel('自动与手动增益实时作用于音频，取消会恢复。轻声检测增强在下次收音生效。静音时不主动抬升增益，突发大声会限制峰值；增益不能恢复已丢失的信噪比。')
        gain_note.setWordWrap(True);gain_note.setObjectName('muted');gain_form.addRow(gain_note)
        note = QLabel('音频来源、模型、源语言、更新间隔与分句策略在保存后下次收音生效。翻译目标语言在“翻译服务”；音量增益在本页的折叠面板中。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        appearance.addRow(note)
        appearance.addRow('Parakeet 音频特征计算',self.feature_backend);appearance.addRow(feature_note)
        appearance.insertRow(1,Foldout('音量增益',gain_page))
        tabs.addTab(scroll_page(display),'收音设置')
        tabs.addTab(workbench,'工作台字体')
        self.theme_editor=ThemeEditor(settings.get('colors'),themes=settings.get('saved_themes',[]),options=settings.get('theme'))
        self.theme_editor.preview_changed.connect(self.theme_preview.emit)
        tabs.addTab(self.theme_editor,'工作台配色')
        tabs.addTab(scroll_page(advanced),'接口高级参数')
        history = QWidget()
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(22,26,22,22)
        label = QLabel('历史记录自动保存')
        label.setObjectName('sectionHeading')
        history_layout.addWidget(label)
        description = QLabel('确认的原文、译文、时间轴和翻译状态保存在本机。重启后可搜索、补译和导出。\n\n原始麦克风音频不录制、不保存。启用云端翻译时，仅发送待翻译文本及少量上下文。')
        description.setWordWrap(True)
        history_layout.addWidget(description)
        folder = QLabel(str(DATA))
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder.setWordWrap(True)
        folder.setObjectName('muted')
        history_layout.addWidget(folder)
        open_folder = QPushButton('打开本机存储文件夹')
        open_folder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA))))
        history_layout.addWidget(open_folder)
        config_title=QLabel('配置备份与迁移');config_title.setObjectName('sectionHeading')
        history_layout.addWidget(config_title)
        config_note=QLabel('导出字体、间距、配色方案、收音与服务参数，不含 API 密钥和字幕历史。导入先载入草稿，保存后生效；密钥仍保留在本机凭据管理器。')
        config_note.setWordWrap(True);config_note.setObjectName('muted');history_layout.addWidget(config_note)
        config_row=QHBoxLayout()
        export_button=QPushButton('导出配置…');export_button.clicked.connect(self.export_config)
        import_button=QPushButton('导入配置…');import_button.clicked.connect(self.import_config)
        config_row.addWidget(export_button);config_row.addWidget(import_button);history_layout.addLayout(config_row)
        self.config_status=QLabel('');self.config_status.setWordWrap(True);self.config_status.setObjectName('muted')
        history_layout.addWidget(self.config_status);history_layout.addStretch()
        tabs.addTab(scroll_page(history),'存储与配置')
        self.domain_editor=DomainEditor(settings.get('domain_bias'))
        tabs.addTab(scroll_page(self.domain_editor),'领域词表')
        self.desktop_editor=DesktopCaptionEditor(settings.get('desktop_captions'))
        self.desktop_editor.changed.connect(self.desktop_preview.emit)
        self.finished.connect(self.desktop_editor.timer.stop)
        self.desktop_tab=tabs.addTab(scroll_page(self.desktop_editor),'桌面字幕')
        background=QWidget();background_layout=QVBoxLayout(background);background_layout.setContentsMargins(16,20,16,16)
        self.close_to_tray=QCheckBox('关闭主窗口时继续在托盘运行')
        self.close_to_tray.setChecked(settings.get('close_to_tray',True));background_layout.addWidget(self.close_to_tray)
        self.minimize_to_tray=QCheckBox('最小化时隐藏到托盘，不在任务栏显示')
        self.minimize_to_tray.setChecked(settings.get('minimize_to_tray',True));background_layout.addWidget(self.minimize_to_tray)
        tray_note=QLabel('隐藏窗口后，当前收音、翻译、历史保存和桌面字幕继续运行。\n\n点击托盘图标可恢复窗口；右键菜单可停止收音、显示或隐藏桌面字幕、打开设置或完全退出。\n\n完全退出请在托盘菜单选择“退出”。若系统托盘不可用，关闭窗口会正常退出。')
        tray_note.setWordWrap(True);tray_note.setObjectName('muted');background_layout.addWidget(tray_note)
        background_layout.addStretch();tabs.addTab(background,'后台与托盘')
        tabs.finalize(['收音设置','翻译服务','接口高级参数','桌面字幕','后台与托盘','工作台字体','工作台配色','领域词表','存储与配置'])
        tabs.setCurrentIndex(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存设置')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.buttons=buttons
        self.save_status=QLabel('所有页面共用草稿；保存后保留，取消撤销。')
        self.save_status.setObjectName('muted');self.save_status.setWordWrap(True);root.addWidget(self.save_status)
        root.addWidget(buttons)
        for editor in (self.typography_editor,self.domain_editor,self.theme_editor):self.finished.connect(editor.timer.stop)
        for profile in self.settings['profiles']:
            self.profile.addItem(profile['name'],profile['id'])
        self.profile.setCurrentIndex(max(0,self.profile.findData(settings['selected_profile'])))
        if self.current is None:
            self.switch_profile()
        for field in self.findChildren(QAbstractSpinBox):field.setKeyboardTracking(False)
        for field in self.findChildren(QWidget):
            if isinstance(field,(QAbstractSpinBox,QComboBox)):
                field.setFocusPolicy(Qt.FocusPolicy.StrongFocus);field.installEventFilter(self)
            if isinstance(field,QComboBox):
                field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                field.setMinimumContentsLength(12)
        for form in self.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.asr_model.currentIndexChanged.connect(self.update_model_dependencies)
        self.protocol.currentIndexChanged.connect(self.update_dependencies)
        self.agc_enabled.toggled.connect(self.update_dependencies)
        self.update_model_dependencies()
        self.update_dependencies()

    def update_model_dependencies(self,*_):
        model=self.asr_model.currentData()
        current=source_language_for_model(model,self.source_language.currentData())
        self.source_language.blockSignals(True);self.source_language.clear()
        for value,label,_ in source_languages(model):self.source_language.addItem(label,value)
        self.source_language.setCurrentIndex(self.source_language.findData(current));self.source_language.blockSignals(False)
        parakeet=model in PARAKEET_MODELS
        for field in (self.interval,self.confirmation_wait):self.appearance.setRowVisible(field,parakeet)
        self.appearance.setRowVisible(self.feature_backend,model=='parakeet')
        self.appearance.setRowVisible(self.feature_note,model=='parakeet')
        notes={
            'parakeet':'Parakeet v2 仅支持英语；转录神经网络使用 NPU。',
            'parakeet-v3':'Parakeet v3 自动辨别 25 种欧洲语言，保留标点和词时间戳；语言选项用于分句与翻译提示。转录神经网络使用 NPU。',
            'qwen3-asr-1.7b':'Qwen 支持多语言；预览至少约 3 秒更新，实际间隔还取决于推理耗时，时间轴为近似值。转录神经网络使用 NPU。',
            'mega-asr':'Mega 支持多语言与抗噪；预览至少约 3 秒更新，实际间隔还取决于推理耗时，时间轴为近似值。转录神经网络使用 NPU。'}
        self.model_note.setText(notes.get(model,''))
        self.domain_editor.set_model(model)

    def update_dependencies(self,*_):
        anthropic=self.protocol.currentData()=='anthropic'
        self.version.setEnabled(anthropic);self.workspace.setEnabled(anthropic)
        for key,field in self.agc_fields.items():field.setEnabled(key=='manual_db' or self.agc_enabled.isChecked())

    def eventFilter(self,field,event):
        if event.type()==QEvent.Type.Wheel and not field.hasFocus():
            parent=field.parentWidget()
            while parent and not isinstance(parent,QScrollArea):parent=parent.parentWidget()
            if parent:
                delta=event.pixelDelta().y() or event.angleDelta().y()/2
                bar=parent.verticalScrollBar();bar.setValue(bar.value()-int(delta));event.accept();return True
        return super().eventFilter(field,event)

    def stash(self):
        if self.loading or not self.current:
            return
        p = self.current
        p.update(name=self.name.text().strip(),protocol=self.protocol.currentData(),base_url=self.url.text().strip(),
            model=self.model.text().strip(),auth=self.auth.currentData(),api_version=self.version.text().strip(),workspace=self.workspace.text().strip())
        self.secrets[p['id']] = self.key.text().strip()
        self.extra_text[p['id']] = self.extra.toPlainText().strip() or '{}'
        try:p['extra'] = json.loads(self.extra_text[p['id']])
        except ValueError:pass

    def switch_profile(self,*_):
        self.stash()
        ident = self.profile.currentData()
        self.current = next((p for p in self.settings['profiles'] if p['id']==ident),None)
        if not self.current:return
        self.loading = True
        p = self.current
        self.name.setText(p['name'])
        self.protocol.setCurrentIndex(max(0,self.protocol.findData(p['protocol'])))
        self.url.setText(p['base_url'])
        self.model.setText(p['model'])
        self.key.setText(self.secrets.get(p['id'],''))
        self.key.setPlaceholderText('留空保留本机已有密钥；新服务请填写密钥')
        self.auth.setCurrentIndex(max(0,self.auth.findData(p.get('auth','auto'))))
        self.version.setText(p.get('api_version','2023-06-01'))
        self.workspace.setText(p.get('workspace',''))
        self.extra.setPlainText(self.extra_text.get(p['id'],json.dumps(p.get('extra',{}),ensure_ascii=False,indent=2)))
        self.loading = False

    def new_profile(self):
        self.stash()
        p = {'id':uuid.uuid4().hex,'name':'自定义服务','protocol':'openai','base_url':'','model':'',
             'auth':'auto','api_version':'2023-06-01','workspace':'','extra':{}}
        self.settings['profiles'].append(p)
        self.profile.addItem(p['name'],p['id'])
        self.profile.setCurrentIndex(self.profile.count()-1)

    def current_valid(self):
        self.stash()
        self.current['extra'] = json.loads(self.extra.toPlainText() or '{}')
        validate_profile(self.current)
        return copy.deepcopy(self.current)

    def test_connection(self):
        try:
            profile = self.current_valid()
            profile.update(self.language_values())
            key = self.key.text().strip() or get_key(profile)
        except Exception as error:
            self.test_status.setText(str(error) if isinstance(error,(ValueError,KeyError)) else '无法读取密钥，请重新填写')
            return
        self.test.setEnabled(False)
        self.test_status.setText('正在测试…')
        self.test_requested.emit(profile,key)

    def test_result(self,text,success):
        self.test.setEnabled(True)
        self.test_status.setText(('连接成功 · ' if success else '连接失败 · ')+text)

    def agc_values(self):
        return normalize_agc(dict(enabled=self.agc_enabled.isChecked(),**{key:field.value() for key,field in self.agc_fields.items()}))

    def language_values(self):
        current=self.target_language.currentData()
        target=current if self.target_language.currentIndex()>=0 and self.target_language.currentText()==self.target_language.itemText(self.target_language.currentIndex()) else self.target_language.currentText().strip()
        if not target or len(target)>80:raise ValueError('请填写有效目标语言')
        source=self.source_language.currentData();model=self.asr_model.currentData()
        if not supported_language(model,source):raise ValueError('所选模型不支持此转录语言')
        return dict(asr_model=model,source_language=source,target_language=target,agc=self.agc_values(),feature_backend=self.feature_backend.currentData())

    def draft(self):
        self.stash()
        result=copy.deepcopy(self.settings)
        for profile in result['profiles']:
            try:
                profile['extra']=json.loads(self.extra_text.get(profile['id'],json.dumps(profile.get('extra',{}))))
                if not isinstance(profile['extra'],dict):raise ValueError('JSON object required')
            except ValueError:
                self.profile.setCurrentIndex(self.profile.findData(profile['id']));self.tabs.setCurrentIndex(4)
                raise ValueError('服务“'+profile['name']+'”的额外 JSON 参数格式无效') from None
        result.update(self.typography_editor.values())
        result.update(self.language_values())
        result['domain_bias']=self.domain_editor.values()
        result['desktop_captions']=self.desktop_editor.values()
        result['audio_source']=self.audio_source_editor.values()
        result.update(selected_profile=self.current['id'],translate=self.enabled.isChecked(),
            progressive_translation=self.progressive.isChecked(),
            progressive_translation_limit=self.progressive_limit.value(),
            progressive_translation_interval=self.progressive_interval.value(),
            close_to_tray=self.close_to_tray.isChecked(),minimize_to_tray=self.minimize_to_tray.isChecked(),
            opacity=self.opacity.value(),topmost=self.topmost.isChecked(),quiet=self.quiet.isChecked(),
            interval=self.interval.currentData(),segmentation=self.segmentation.currentData(),
            confirmation_wait=self.confirmation_wait.value(),
            colors=self.theme_editor.validated_colors(),theme=dict(self.theme_editor.options),
            saved_themes=copy.deepcopy(self.theme_editor.saved_themes))
        return result

    def export_config(self):
        path,_=QFileDialog.getSaveFileName(self,'导出工作台配置','实时字幕配置.json','工作台配置 (*.json)')
        if not path:return
        if not path.lower().endswith('.json'):path+='.json'
        try:export_configuration(self.draft(),path)
        except (OSError,ValueError,TypeError) as error:
            self.config_status.setText(str(error) if isinstance(error,ValueError) else '导出失败，请检查文件路径和设置');return
        self.config_status.setText('已导出配置（不含密钥与历史记录）')

    def load_draft(self,settings):
        self.settings=copy.deepcopy(settings);self.current=None;self.secrets={};self.extra_text={}
        self.profile.blockSignals(True);self.profile.clear()
        for profile in settings['profiles']:self.profile.addItem(profile['name'],profile['id'])
        self.profile.setCurrentIndex(self.profile.findData(settings['selected_profile']));self.profile.blockSignals(False)
        self.switch_profile()
        self.enabled.setChecked(settings['translate']);self.opacity.setValue(settings['opacity'])
        self.progressive.setChecked(settings.get('progressive_translation',True))
        self.progressive_limit.setValue(settings.get('progressive_translation_limit',2))
        self.progressive_interval.setValue(settings.get('progressive_translation_interval',1.))
        self.topmost.setChecked(settings['topmost']);self.quiet.setChecked(settings['quiet'])
        self.close_to_tray.setChecked(settings.get('close_to_tray',True));self.minimize_to_tray.setChecked(settings.get('minimize_to_tray',True))
        self.interval.setCurrentIndex(max(0,self.interval.findData(settings['interval'])))
        self.segmentation.setCurrentIndex(self.segmentation.findData(settings['segmentation']))
        self.confirmation_wait.setValue(settings.get('confirmation_wait',3.))
        self.asr_model.blockSignals(True)
        self.asr_model.setCurrentIndex(self.asr_model.findData(settings.get('asr_model','parakeet')))
        self.source_language.clear()
        for value,label,_ in LANGUAGES:self.source_language.addItem(label,value)
        self.asr_model.blockSignals(False)
        self.feature_backend.setCurrentIndex(max(0,self.feature_backend.findData(settings.get('feature_backend','numpy'))))
        self.source_language.setCurrentIndex(self.source_language.findData(settings.get('source_language','auto')))
        index=self.target_language.findData(settings.get('target_language','简体中文'))
        if index>=0:self.target_language.setCurrentIndex(index)
        else:self.target_language.setEditText(settings['target_language'])
        agc=normalize_agc(settings.get('agc'));self.agc_enabled.setChecked(agc['enabled'])
        for key,field in self.agc_fields.items():field.setValue(agc[key])
        self.typography_editor.set_values(settings,emit=True)
        self.theme_editor.set_themes(settings['saved_themes'])
        self.theme_editor.set_configuration(settings['colors'],settings.get('theme'))
        self.domain_editor.set_values(settings.get('domain_bias'))
        self.desktop_editor.set_values(settings.get('desktop_captions'),emit=True)
        self.audio_source_editor.set_values(settings.get('audio_source'))
        self.update_model_dependencies()
        self.update_dependencies()

    def import_config(self):
        path,_=QFileDialog.getOpenFileName(self,'导入工作台配置','','工作台配置 (*.json)')
        if not path:return
        try:settings=import_configuration(path)
        except (OSError,ValueError,TypeError,RecursionError) as error:
            self.config_status.setText(str(error) if isinstance(error,ValueError) else '导入失败，请检查文件与格式');return
        self.load_draft(settings)
        self.config_status.setText('已载入配置草稿。可检查服务地址及外观，点击“保存设置”后生效；取消则撤销。')

    def save(self):
        if self.save_job and self.save_job.isRunning():return
        try:
            try:self.language_values()
            except ValueError:
                self.tabs.setCurrentIndex(1 if self.asr_model.currentData()=='parakeet' and self.source_language.currentData() not in ('auto','English') else 0)
                raise
            try:self.theme_editor.validated_colors()
            except ValueError:
                self.tabs.setCurrentWidget(self.theme_editor)
                raise
            candidate=self.draft()
            if self.enabled.isChecked():
                validate_profile(next(p for p in candidate['profiles'] if p['id']==candidate['selected_profile']))
            for p in candidate['profiles']:
                if self.secrets.get(p['id']):
                    endpoint(p)
        except Exception as error:
            self.save_status.setText(str(error) if isinstance(error,(ValueError,KeyError)) else '设置未保存，请检查输入')
            return
        self.tabs.setEnabled(False);self.buttons.setEnabled(False);self.save_status.setText('正在验证并保存设置…')
        for editor in (self.typography_editor,self.theme_editor,self.desktop_editor,self.domain_editor):editor.timer.stop()
        self.save_job=SettingsSave(candidate,dict(self.secrets),self)
        self.save_job.finished.connect(self.saved)
        self.save_job.start()

    def saved(self):
        if self.save_job.error:
            self.tabs.setEnabled(True);self.buttons.setEnabled(True);self.save_status.setText(self.save_job.error)
            if self.save_job.error_page is not None:self.tabs.setCurrentIndex(self.save_job.error_page)
            return
        self.settings=self.save_job.settings;self.secrets.clear();self.key.clear();self.accept()

    def reject(self):
        if self.save_job and self.save_job.isRunning():
            self.save_status.setText('正在完成保存，请稍候…');return
        super().reject()
