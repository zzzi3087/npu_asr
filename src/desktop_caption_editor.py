"""Desktop caption draft controls; preview is debounced and event-driven."""
from PySide6.QtCore import Signal,QTimer
from PySide6.QtGui import QColor,QFont
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLabel,QCheckBox,QSpinBox,QDoubleSpinBox,
    QFontComboBox,QLineEdit,QPushButton,QHBoxLayout,QColorDialog)
from desktop_caption_config import normalize_desktop_captions,default_desktop_captions
from settings_pages import Foldout


class DesktopCaptionEditor(QWidget):
    changed=Signal(object)
    def __init__(self,value=None,parent=None):
        super().__init__(parent);self.loading=True;self.fields={}
        root=QVBoxLayout(self);root.setContentsMargins(20,22,20,20);root.setSpacing(14)
        note=QLabel('透明、无背景的独立字幕窗口，默认白字黑边，最近两句双语字幕，另加一行尚未确认的实时预览。最小化工作台后仍可显示。')
        note.setWordWrap(True);note.setObjectName('muted');root.addWidget(note)
        form=QFormLayout();form.setVerticalSpacing(12);root.addLayout(form)
        for key,label in [('enabled','显示桌面字幕'),('locked','锁定位置并穿透鼠标'),('topmost','始终置顶'),('bold','加粗文字'),('preview_enabled','显示一行实时预览')]:
            field=QCheckBox(label);self.fields[key]=field;form.addRow(field);field.toggled.connect(self.schedule)
        for key,label,low,high in [('sentence_count','最近句子数（每句含原文与译文）',1,5)]:
            field=QSpinBox();field.setRange(low,high)
            if key=='width':field.setSuffix(' px');field.setSingleStep(40)
            self.fields[key]=field;form.addRow(label,field);field.valueChanged.connect(self.schedule)
        for kind,label in [('source','原文'),('translation','译文'),('preview','实时预览')]:
            font=QFontComboBox();self.fields[kind+'_font']=font;form.addRow(label+'字体',font);font.currentFontChanged.connect(self.schedule)
            size=QSpinBox();size.setRange(12,96);size.setSuffix(' px');self.fields[kind+'_size']=size;form.addRow(label+'字号',size);size.valueChanged.connect(self.schedule)
        for key,label in [('source_color','原文颜色'),('translation_color','译文颜色'),('preview_color','实时预览颜色'),('outline_color','描边颜色')]:
            row=QHBoxLayout();field=QLineEdit();field.setMaxLength(7);field.setPlaceholderText('#RRGGBB');self.fields[key]=field
            button=QPushButton('选色…');button.clicked.connect(lambda _,k=key:self.pick_color(k));row.addWidget(field,1);row.addWidget(button)
            form.addRow(label,row);field.textChanged.connect(self.schedule)
        layout_options=QWidget();layout_form=QFormLayout(layout_options);layout_form.setVerticalSpacing(12)
        for key,label,low,high in [('width','字幕区域宽度',320,2400),('max_lines','每种文字最多行数',1,8)]:
            field=QSpinBox();field.setRange(low,high)
            if key=='width':field.setSuffix(' px');field.setSingleStep(40)
            self.fields[key]=field;layout_form.addRow(label,field);field.valueChanged.connect(self.schedule)
        for key,label,low,high,step in [('outline_width','描边宽度',0,8,.5),('line_height','行高倍数',1,2,.05),
            ('bilingual_gap','原文与译文间距',0,40,1),('sentence_gap','两句之间的间距',0,60,1),('preview_gap','预览与确认字幕间距',0,60,1)]:
            field=QDoubleSpinBox();field.setRange(low,high);field.setSingleStep(step);field.setDecimals(2 if key=='line_height' else 1)
            if key!='line_height':field.setSuffix(' px')
            self.fields[key]=field
            (form if key=='outline_width' else layout_form).addRow(label,field);field.valueChanged.connect(self.schedule)
        root.addWidget(Foldout('布局与间距',layout_options))
        position=QLabel('取消“锁定位置”后，可拖动字幕移动；右键可锁定或隐藏。拖动后记住位置。无字幕时，解锁会显示定位示例。')
        position.setWordWrap(True);position.setObjectName('muted');root.addWidget(position)
        buttons=QHBoxLayout()
        preview=QPushButton('预览桌面字幕');preview.clicked.connect(self.preview);buttons.addWidget(preview)
        reset=QPushButton('恢复屏幕底部居中');reset.clicked.connect(self.reset_position);buttons.addWidget(reset)
        defaults=QPushButton('恢复白字黑边');defaults.clicked.connect(self.restore_style);buttons.addWidget(defaults);root.addLayout(buttons)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setObjectName('muted');root.addWidget(self.status)
        power=QLabel('仅在识别文字、译文、样式或位置改变时更新；无滚动、淡入或常驻刷新定时器。确认字幕按行数上限省略，实时预览固定一行，完整确认内容仍保存在历史。')
        power.setWordWrap(True);power.setObjectName('muted');root.addWidget(power);root.addStretch()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(120);self.timer.timeout.connect(self.emit_preview)
        self.set_values(value)

    def set_values(self,value,emit=False):
        if hasattr(self,'timer'):self.timer.stop()
        self.loading=True;self.config=normalize_desktop_captions(value)
        for key,field in self.fields.items():
            item=self.config[key]
            if isinstance(field,QCheckBox):field.setChecked(item)
            elif isinstance(field,QFontComboBox):field.setCurrentFont(QFont(item))
            elif isinstance(field,QLineEdit):field.setText(item)
            else:field.setValue(item)
        self.loading=False
        if emit:self.emit_preview()

    def values(self):
        value=dict(self.config)
        for key,field in self.fields.items():
            if isinstance(field,QCheckBox):value[key]=field.isChecked()
            elif isinstance(field,QFontComboBox):value[key]=field.currentFont().family()
            elif isinstance(field,QLineEdit):value[key]=field.text().strip()
            else:value[key]=field.value()
        return normalize_desktop_captions(value,strict=True)

    def schedule(self,*_):
        if not self.loading:self.timer.start()

    def emit_preview(self):
        try:options=self.values()
        except ValueError as error:self.status.setText(str(error));return
        self.status.setText('正在预览；保存后保留，取消恢复原设置。');self.changed.emit(options)

    def pick_color(self,key):
        chosen=QColorDialog.getColor(QColor(self.fields[key].text()),self,'选择字幕颜色')
        if chosen.isValid():self.fields[key].setText(chosen.name())

    def preview(self):
        self.fields['enabled'].setChecked(True);self.emit_preview()

    def reset_position(self):
        self.config.update(screen='',x=None,bottom=None);self.emit_preview()

    def set_position(self,position):self.config.update(position)

    def restore_style(self):
        value=dict(self.config)
        for key in ('enabled','locked','topmost','preview_enabled'):value[key]=self.fields[key].isChecked()
        value.update({k:v for k,v in default_desktop_captions().items() if k not in ('enabled','locked','topmost','preview_enabled','screen','x','bottom')})
        self.set_values(value,emit=True)
