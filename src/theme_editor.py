"""Editable global color roles with safe preview and preset recovery."""
import re,copy,uuid
from PySide6.QtCore import Signal,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QLineEdit,QScrollArea,QGridLayout,QColorDialog,QFrame,QSizePolicy,QInputDialog,QMessageBox)
from theme import COLOR_ROLES,DEFAULT_COLORS,PRESETS,normalize_colors


class ColorField(QWidget):
    changed=Signal(str)

    def __init__(self,label,color,parent=None):
        super().__init__(parent)
        self.label=label
        self.color=color
        row=QHBoxLayout(self)
        row.setContentsMargins(0,0,0,0)
        self.swatch=QPushButton()
        self.swatch.setFixedSize(38,32)
        self.swatch.setAccessibleName(label+' · 选择颜色')
        self.swatch.setToolTip('选择'+label)
        self.swatch.clicked.connect(self.pick)
        self.hex=QLineEdit(color)
        self.hex.setFixedWidth(108)
        self.hex.setMaxLength(7)
        self.hex.setAccessibleName(label+' · 十六进制颜色')
        self.hex.setPlaceholderText('#RRGGBB')
        self.hex.textChanged.connect(self.edited)
        row.addWidget(self.swatch)
        row.addWidget(self.hex)
        self.refresh_swatch()

    def refresh_swatch(self):
        # A two-tone outline keeps white/black swatches visible on any theme.
        self.swatch.setStyleSheet(f'background:{self.color}; border:2px solid palette(mid); border-radius:6px; padding:0;')

    def edited(self,value):
        if re.fullmatch(r'#[0-9a-fA-F]{6}',value):
            self.color=value.lower()
            self.refresh_swatch()
            self.changed.emit(self.color)

    def set_color(self,color):
        self.color=color
        self.hex.blockSignals(True)
        self.hex.setText(color)
        self.hex.blockSignals(False)
        self.refresh_swatch()

    def pick(self):
        color=QColorDialog.getColor(QColor(self.color),self,self.label,
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if color.isValid():
            self.set_color(color.name())
            self.changed.emit(self.color)


class ThemeEditor(QWidget):
    changed=Signal(object)
    preview_changed=Signal(object)

    def __init__(self,colors,parent=None,themes=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Ignored)
        self.colors=normalize_colors(colors)
        self.fields={}
        self.filter_rows=[]
        self.saved_themes=copy.deepcopy(themes or [])
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180)
        self.timer.timeout.connect(lambda:self.preview_changed.emit(dict(self.colors)))
        root=QVBoxLayout(self)
        root.setContentsMargins(16,16,16,12)
        root.setSpacing(10)
        note=QLabel('点击色块打开取色器，或填写 #RRGGBB。实时预览整个工作台；保存后保留，取消撤销。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        root.addWidget(note)
        presets=QHBoxLayout()
        self.preset=QComboBox()
        self.preset.addItem('选择预设配色…')
        self.preset.addItems(PRESETS)
        self.preset.activated.connect(self.choose_preset)
        presets.addWidget(self.preset,1)
        reset=QPushButton('恢复默认配色')
        reset.clicked.connect(lambda:self.set_colors(DEFAULT_COLORS))
        presets.addWidget(reset)
        root.addLayout(presets)
        library=QHBoxLayout()
        self.library=QComboBox();self.library.setMinimumWidth(120)
        self.library.activated.connect(self.load_palette)
        library.addWidget(self.library,1)
        for label,callback in [('另存配色',self.save_palette_dialog),('更新',self.update_palette),('删除',self.delete_palette)]:
            button=QPushButton(label);button.clicked.connect(callback);library.addWidget(button)
        root.addLayout(library)
        self.refresh_library()
        self.search=QLineEdit()
        self.search.setPlaceholderText('搜索颜色，例如：背景、字幕、文字、边框')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter)
        root.addWidget(self.search)
        sample=QFrame()
        sample.setObjectName('themeSample')
        sample_layout=QVBoxLayout(sample)
        sample_layout.setContentsMargins(12,8,12,8)
        self.sample_en=QLabel('Every voice deserves to be heard.')
        self.sample_zh=QLabel('让每一句话，都清晰可见。')
        sample_layout.addWidget(self.sample_en)
        sample_layout.addWidget(self.sample_zh)
        root.addWidget(sample)
        scroll=QScrollArea()
        self.scroll=scroll
        scroll.setObjectName('colorScroll')
        scroll.setWidgetResizable(True)
        content=QWidget()
        grid=QGridLayout(content)
        grid.setContentsMargins(2,0,10,8)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(0,1)
        group='';row=0
        for category,key,label,_ in COLOR_ROLES:
            if category!=group:
                heading=QLabel(category)
                heading.setObjectName('sectionHeading')
                grid.addWidget(heading,row,0,1,2)
                self.filter_rows.append((category,heading,None))
                row+=1;group=category
            name=QLabel(label)
            field=ColorField(label,self.colors[key])
            field.changed.connect(lambda color,k=key:self.change_color(k,color))
            grid.addWidget(name,row,0)
            grid.addWidget(field,row,1)
            self.fields[key]=field
            self.filter_rows.append((category+' '+label,name,field))
            row+=1
        grid.setRowStretch(row,1)
        scroll.setWidget(content)
        root.addWidget(scroll,1)
        self.validation=QLabel('')
        self.validation.setObjectName('colorValidation')
        root.addWidget(self.validation)
        self.update_sample()

    def filter(self,text):
        for name,label,field in self.filter_rows:
            visible=not text or text.strip() in name
            label.setVisible(visible)
            if field:field.setVisible(visible)

    def set_themes(self,themes):
        self.saved_themes=copy.deepcopy(themes)
        self.refresh_library()

    def refresh_library(self,ident=None):
        self.library.blockSignals(True);self.library.clear();self.library.addItem('我的配色方案…',None)
        for scheme in self.saved_themes:self.library.addItem(scheme['name'],scheme['id'])
        self.library.setCurrentIndex(max(0,self.library.findData(ident)))
        self.library.blockSignals(False)

    def save_palette(self,name,replace=False):
        name=name.strip()
        if not name or len(name)>80:raise ValueError('配色名称须为 1–80 个字符')
        colors=self.validated_colors()
        existing=next((s for s in self.saved_themes if s['name']==name),None)
        if existing and not replace:raise ValueError('已有同名配色，请更改名称或点击“更新”')
        if existing:existing['colors']=colors;ident=existing['id']
        else:
            if len(self.saved_themes)>=100:raise ValueError('最多保存 100 套自定义配色')
            ident=uuid.uuid4().hex;self.saved_themes.append({'id':ident,'name':name,'colors':colors})
        self.refresh_library(ident)
        self.validation.setText('配色已加入草稿；点击“保存设置”后保留')
        return ident

    def save_palette_dialog(self):
        name,okay=QInputDialog.getText(self,'保存自定义配色','配色名称')
        if okay:
            try:self.save_palette(name)
            except ValueError as error:self.validation.setText(str(error))

    def load_palette(self,index):
        ident=self.library.itemData(index)
        scheme=next((s for s in self.saved_themes if s['id']==ident),None)
        if scheme:self.set_colors(scheme['colors'])

    def update_palette(self):
        ident=self.library.currentData()
        scheme=next((s for s in self.saved_themes if s['id']==ident),None)
        if not scheme:self.validation.setText('请先选择要更新的自定义配色');return
        try:self.save_palette(scheme['name'],replace=True)
        except ValueError as error:self.validation.setText(str(error))

    def delete_palette(self):
        ident=self.library.currentData()
        scheme=next((s for s in self.saved_themes if s['id']==ident),None)
        if scheme and QMessageBox.question(self,'删除配色','删除自定义配色“'+scheme['name']+'”？当前画面颜色仍会保留。')==QMessageBox.StandardButton.Yes:
            self.saved_themes=[s for s in self.saved_themes if s['id']!=ident]
            self.refresh_library()

    def update_sample(self):
        self.sample_en.setStyleSheet(f'font-size:17px;color:{self.colors["english"]};')
        self.sample_zh.setStyleSheet(f'font-size:17px;color:{self.colors["chinese"]};')

    def change_color(self,key,color):
        self.colors[key]=color
        self.preset.setCurrentIndex(0)
        self.validation.setText('')
        self.update_sample()
        self.changed.emit(dict(self.colors))
        self.timer.start()

    def set_colors(self,colors):
        self.timer.stop()
        self.colors=normalize_colors(colors)
        for key,field in self.fields.items():field.set_color(self.colors[key])
        self.preset.setCurrentIndex(0)
        self.validation.setText('')
        self.update_sample()
        self.changed.emit(dict(self.colors))
        self.preview_changed.emit(dict(self.colors))

    def choose_preset(self,index):
        if index:self.set_colors(PRESETS[self.preset.itemText(index)])

    def validated_colors(self):
        for key,field in self.fields.items():
            if not re.fullmatch(r'#[0-9a-fA-F]{6}',field.hex.text()):
                self.search.clear()
                self.scroll.ensureWidgetVisible(field)
                field.hex.setFocus()
                self.validation.setText('请填写完整的 #RRGGBB：'+field.label)
                raise ValueError(self.validation.text())
        return dict(self.colors)
