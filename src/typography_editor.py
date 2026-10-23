from PySide6.QtCore import Signal,QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLabel,QScrollArea,QFontComboBox,
    QSpinBox,QDoubleSpinBox,QCheckBox,QPushButton,QSizePolicy)
from typography import LIMITS,normalize_typography


class TypographyEditor(QWidget):
    changed=Signal(object)
    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Ignored)
        self.loading=True;self.fields={}
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(150)
        self.timer.timeout.connect(lambda:self.changed.emit(self.values()))
        root=QVBoxLayout(self)
        note=QLabel('字体、字号和间距会实时预览；行高按字号的倍数设置。未安装的字体会由系统替代。')
        note.setObjectName('muted');note.setWordWrap(True);root.addWidget(note)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea {border:0;}')
        content=QWidget();form=QFormLayout(content);form.setVerticalSpacing(10)
        self.add_heading(form,'界面文字')
        self.add_font(form,'ui_font','界面字体');self.add_number(form,'ui_size','界面字号',' px')
        self.add_number(form,'ui_letter','界面字间距',' px')
        for kind,label in [('english','原文字幕'),('chinese','译文字幕')]:
            self.add_heading(form,label)
            self.add_font(form,kind+'_font','字体')
            spin=QSpinBox();spin.setRange(12,72);spin.setSuffix(' px');self.register(form,kind+'_size','字号',spin)
            self.add_number(form,kind+'_line','行高',' 倍')
            self.add_number(form,kind+'_letter','字间距',' px')
            self.add_number(form,kind+'_word','词间距',' px')
            bold=QCheckBox('粗体');self.register(form,kind+'_bold','字重',bold)
        self.add_heading(form,'实时预览')
        self.add_font(form,'preview_font','预览字体');self.add_number(form,'preview_size','预览字号',' px')
        self.add_number(form,'preview_line','预览行高',' 倍');self.add_number(form,'preview_letter','预览字间距',' px')
        self.add_number(form,'preview_height','预览区域高度',' px')
        self.add_heading(form,'间距与留白')
        for key,label in [('paragraph_gap','字幕段间距'),('bilingual_gap','中英间距'),('meta_gap','时间与正文间距'),
                          ('padding_x','字幕左右留白'),('padding_y','字幕上下留白'),('content_margin','主区域边距'),
                          ('layout_gap','界面模块间距'),('control_padding','输入控件内边距')]:
            self.add_number(form,key,label,' px')
        reset=QPushButton('恢复默认字体与间距')
        reset.clicked.connect(lambda:self.set_values({'english_size':24,'chinese_size':22},emit=True))
        form.addRow(reset);scroll.setWidget(content);root.addWidget(scroll,1)
        self.set_values(settings)

    def add_heading(self,form,text):
        label=QLabel(text);label.setObjectName('sectionHeading');form.addRow(label)

    def register(self,form,key,label,widget):
        self.fields[key]=widget;widget.setAccessibleName(label+' · '+key)
        if isinstance(widget,QFontComboBox):widget.currentFontChanged.connect(self.schedule)
        elif isinstance(widget,QCheckBox):widget.toggled.connect(self.schedule)
        else:widget.valueChanged.connect(self.schedule)
        form.addRow(label,widget)

    def add_font(self,form,key,label):self.register(form,key,label,QFontComboBox())

    def add_number(self,form,key,label,suffix):
        lo,hi,default=LIMITS[key]
        widget=QDoubleSpinBox() if isinstance(default,float) else QSpinBox()
        widget.setRange(lo,hi);widget.setSuffix(suffix)
        if isinstance(widget,QDoubleSpinBox):widget.setDecimals(2);widget.setSingleStep(.05 if key.endswith('line') else .1)
        self.register(form,key,label,widget)

    def schedule(self,*_):
        if not self.loading:self.timer.start()

    def set_values(self,settings,emit=False):
        self.timer.stop();self.loading=True
        t=normalize_typography(settings.get('typography'))
        for key,field in self.fields.items():
            value=settings.get(key,24 if key=='english_size' else 22) if key.endswith('_size') and key in ('english_size','chinese_size') else t[key]
            if isinstance(field,QFontComboBox):field.setCurrentFont(QFont(value))
            elif isinstance(field,QCheckBox):field.setChecked(value)
            else:field.setValue(value)
        self.loading=False
        if emit:self.changed.emit(self.values())

    def values(self):
        result={};t={}
        for key,field in self.fields.items():
            if isinstance(field,QFontComboBox):value=field.currentFont().family()
            elif isinstance(field,QCheckBox):value=field.isChecked()
            else:value=field.value()
            if key in ('english_size','chinese_size'):result[key]=value
            else:t[key]=value
        result['typography']=normalize_typography(t,strict=True)
        return result
