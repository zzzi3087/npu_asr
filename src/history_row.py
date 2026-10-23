"""Compact history entries; one shared menu is owned by the main window."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QFrame,QLabel,QToolButton,QHBoxLayout,QVBoxLayout,QSizePolicy


class HistoryRow(QFrame):
    activated=Signal(str)
    menu_requested=Signal(str,object)

    def __init__(self,sid,parent=None):
        super().__init__(parent)
        self.sid=sid;self.full_title='';self.selected=False;self.record_key=None
        self.setObjectName('historyRow')
        layout=QHBoxLayout(self);layout.setContentsMargins(10,9,5,9);layout.setSpacing(3)
        text=QVBoxLayout();text.setSpacing(4)
        self.title=QLabel();self.title.setObjectName('historyTitle')
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.detail=QLabel();self.detail.setObjectName('historyDetail')
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        for label in (self.title,self.detail):label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text.addWidget(self.title);text.addWidget(self.detail);layout.addLayout(text,1)
        self.menu_button=QToolButton();self.menu_button.setText('...');self.menu_button.setObjectName('historyMenu')
        self.menu_button.setFixedSize(28,28);self.menu_button.setToolTip('重命名或删除此记录')
        self.menu_button.clicked.connect(lambda:self.menu_requested.emit(self.sid,self.menu_button))
        layout.addWidget(self.menu_button,0,Qt.AlignmentFlag.AlignTop)

    def update_record(self,title,detail):
        key=(title,detail,self.title.font().key(),self.detail.font().key())
        if key==self.record_key:return False
        self.record_key=key
        self.full_title=title;self.setToolTip(title)
        self.menu_button.setAccessibleName('记录操作：'+title)
        if self.detail.text()!=detail:self.detail.setText(detail)
        self.elide_title()
        return True

    def elide_title(self):
        text=self.title.fontMetrics().elidedText(self.full_title,Qt.TextElideMode.ElideRight,max(1,self.title.width()))
        if self.title.text()!=text:self.title.setText(text)

    def set_selected(self,selected):
        if self.selected==selected:return
        self.selected=selected;self.setProperty('selected',selected)
        for widget in (self,self.title,self.detail):
            widget.style().unpolish(widget);widget.style().polish(widget);widget.update()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.elide_title()

    def changeEvent(self,event):
        super().changeEvent(event)
        if hasattr(self,'title'):self.elide_title()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.activated.emit(self.sid)
        super().mousePressEvent(event)
