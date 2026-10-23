"""Editable global color roles with safe preview and preset recovery."""
import re,copy,uuid
from PySide6.QtCore import Signal,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QLineEdit,QScrollArea,QGridLayout,QColorDialog,QFrame,QSizePolicy,QInputDialog,QMessageBox,QCheckBox)
from theme import (COLOR_ROLES,PRESETS,PRESET_DESCRIPTIONS,THEME_FAMILIES,
    normalize_colors,normalize_theme,effective_colors)
from theme_card import ThemeCard
from system_theme import system_dark


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

    def __init__(self,colors,parent=None,themes=None,options=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Ignored)
        self.manual_colors=normalize_colors(colors)
        self.options=normalize_theme(options);self.dark=system_dark()
        self.colors=effective_colors(self.manual_colors,self.options,self.dark)
        self.fields={}
        self.filter_rows=[]
        self.saved_themes=copy.deepcopy(themes or [])
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180)
        self.timer.timeout.connect(lambda:self.preview_changed.emit(dict(self.colors)))
        root=QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        self.scroll=QScrollArea();self.scroll.setObjectName('colorScroll');self.scroll.setWidgetResizable(True)
        page=QWidget();content_layout=QVBoxLayout(page)
        content_layout.setContentsMargins(16,16,16,12);content_layout.setSpacing(12)
        root.addWidget(self.scroll);self.scroll.setWidget(page)
        note=QLabel('两组明暗配色，统一背景、控件与字幕。点击预设立即预览；保存后保留，取消撤销。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        content_layout.addWidget(note)
        cards=QHBoxLayout();cards.setSpacing(10);self.cards={}
        for (name,colors),description in zip(PRESETS.items(),PRESET_DESCRIPTIONS):
            card=ThemeCard(name,description,colors)
            card.clicked.connect(lambda checked=False,n=name:self.choose_preset(n))
            self.cards[name]=card;cards.addWidget(card,1)
        content_layout.addLayout(cards)
        mode=QHBoxLayout()
        self.follow=QCheckBox('跟随系统明亮 / 深色');self.follow.setChecked(self.options['follow_system'])
        self.follow.setToolTip('跟随 Windows 的应用颜色模式；关闭后恢复上次手动配色。')
        self.follow.toggled.connect(self.mode_changed)
        mode.addWidget(self.follow);mode.addStretch()
        self.family=QComboBox();self.family.setAccessibleName('自动切换配色组')
        self.family.setToolTip('自动配色组\n中性灰蓝：雾白 / 墨夜\n柔和暖色：米纸 / 暖岩')
        for key,(label,light,dark) in THEME_FAMILIES.items():self.family.addItem(label,key)
        self.family.setCurrentIndex(self.family.findData(self.options['family']))
        self.family.currentIndexChanged.connect(self.mode_changed)
        mode.addWidget(self.family);content_layout.addLayout(mode)
        self.mode_note=QLabel();self.mode_note.setObjectName('muted');self.mode_note.setWordWrap(True)
        content_layout.addWidget(self.mode_note)
        sample=QFrame();sample.setObjectName('themeSample')
        sample_layout=QVBoxLayout(sample);sample_layout.setContentsMargins(12,8,12,8)
        self.sample_en=QLabel('The universe still holds many mysteries.')
        self.sample_zh=QLabel('宇宙仍藏着许多未解之谜。')
        sample_layout.addWidget(self.sample_en);sample_layout.addWidget(self.sample_zh)
        content_layout.addWidget(sample)
        heading=QLabel('自定义配色');heading.setObjectName('sectionHeading');content_layout.addWidget(heading)
        library=QHBoxLayout()
        self.library=QComboBox();self.library.setMinimumWidth(120)
        self.library.activated.connect(self.load_palette)
        library.addWidget(self.library,1)
        for label,callback in [('另存配色',self.save_palette_dialog),('更新',self.update_palette),('删除',self.delete_palette)]:
            button=QPushButton(label);button.clicked.connect(callback);library.addWidget(button)
        content_layout.addLayout(library)
        self.refresh_library()
        self.search=QLineEdit()
        self.search.setPlaceholderText('搜索颜色，例如：背景、字幕、文字、边框')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter)
        content_layout.addWidget(self.search)
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
        content_layout.addWidget(content)
        content_layout.addStretch()
        self.validation=QLabel('')
        self.validation.setObjectName('colorValidation')
        content_layout.addWidget(self.validation)
        self.refresh_mode(emit=False)

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
        colors=dict(self.colors) if self.follow.isChecked() else self.validated_colors()
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
        self.manual_colors=dict(self.colors)
        self.follow.blockSignals(True);self.follow.setChecked(False);self.follow.blockSignals(False)
        self.options['follow_system']=False
        self.validation.setText('')
        self.refresh_mode(emit=False,sync_fields=False)
        self.changed.emit(dict(self.colors))
        self.timer.start()

    def set_colors(self,colors):
        self.timer.stop()
        self.manual_colors=normalize_colors(colors)
        self.follow.blockSignals(True);self.follow.setChecked(False);self.follow.blockSignals(False)
        self.options['follow_system']=False
        self.validation.setText('')
        self.refresh_mode()

    def choose_preset(self,name):
        family=next(key for key,(_,light,dark) in THEME_FAMILIES.items() if name in (light,dark))
        self.family.blockSignals(True);self.family.setCurrentIndex(self.family.findData(family));self.family.blockSignals(False)
        self.options['family']=family
        self.set_colors(PRESETS[name])

    def set_configuration(self,colors,options):
        self.timer.stop();self.manual_colors=normalize_colors(colors);self.options=normalize_theme(options)
        self.follow.blockSignals(True);self.follow.setChecked(self.options['follow_system']);self.follow.blockSignals(False)
        self.family.blockSignals(True);self.family.setCurrentIndex(self.family.findData(self.options['family']));self.family.blockSignals(False)
        self.refresh_mode()

    def mode_changed(self,*_):
        self.timer.stop()
        self.options={'follow_system':self.follow.isChecked(),'family':self.family.currentData()}
        self.refresh_mode()

    def system_changed(self,dark):
        self.dark=dark
        if self.follow.isChecked():self.refresh_mode()

    def refresh_mode(self,emit=True,sync_fields=True):
        self.colors=effective_colors(self.manual_colors,self.options,self.dark)
        automatic=self.options['follow_system'];self.family.setEnabled(automatic)
        selected=next((name for name,colors in PRESETS.items() if colors==self.colors),'自定义')
        for name,card in self.cards.items():card.setChecked(name==selected)
        for key,field in self.fields.items():
            if sync_fields:field.set_color(self.colors[key])
            field.setEnabled(not automatic)
        self.mode_note.setText(('当前跟随系统：'+('深色' if self.dark else '明亮')+' · '+selected+
            '。点选卡片可固定配色；关闭跟随后可编辑颜色。') if automatic else
            '当前：'+selected+' · 手动。开启跟随系统后，在所选组内自动切换；关闭后恢复此配色。')
        self.update_sample()
        if emit:
            self.changed.emit(dict(self.colors));self.preview_changed.emit(dict(self.colors))

    def validated_colors(self):
        if self.follow.isChecked():return dict(self.manual_colors)
        for key,field in self.fields.items():
            if not re.fullmatch(r'#[0-9a-fA-F]{6}',field.hex.text()):
                self.search.clear()
                self.scroll.ensureWidgetVisible(field)
                field.hex.setFocus()
                self.validation.setText('请填写完整的 #RRGGBB：'+field.label)
                raise ValueError(self.validation.text())
        return dict(self.colors)
