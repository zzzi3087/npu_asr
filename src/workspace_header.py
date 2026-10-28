"""Event-driven header reflow for title, live ASR selection and actions."""
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QWidget,QGridLayout,QSizePolicy


class WorkspaceHeader(QWidget):
    def __init__(self,title,controls,status,parent=None,selection=None):
        super().__init__(parent)
        self.setObjectName('workspaceHeader')
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.title_group=title;self.controls=controls;self.status=status;self.selection=selection
        self.narrow=None;self.layout_mode=None
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0)
        self.grid.setHorizontalSpacing(10);self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(0,1)
        self.adapt()

    def adapt(self):
        actions_width=self.controls.sizeHint().width()
        title_width=max(180,self.title_group.minimumSizeHint().width())
        selection_width=self.selection.sizeHint().width()+10 if self.selection else 0
        mode='wide' if self.width()>=title_width+actions_width+selection_width+10 else (
            'compact' if self.width()>=title_width+actions_width+10 else 'stacked')
        if self.layout_mode==mode:return
        self.layout_mode=mode;self.narrow=mode!='wide'
        for widget in (self.title_group,self.controls,self.status,self.selection):
            if widget is not None:self.grid.removeWidget(widget)
        if mode=='wide':
            self.grid.addWidget(self.title_group,0,0)
            if self.selection:self.grid.addWidget(self.selection,0,1,Qt.AlignmentFlag.AlignVCenter)
            self.grid.addWidget(self.controls,0,2,Qt.AlignmentFlag.AlignVCenter)
            status_row=1
        else:
            self.grid.addWidget(self.title_group,0,0,1,3 if mode=='stacked' else 1)
            next_row=1
            if mode=='compact':self.grid.addWidget(self.controls,0,1,1,2)
            if self.selection:
                self.grid.addWidget(self.selection,next_row,0,1,3,Qt.AlignmentFlag.AlignLeft)
                next_row+=1
            if mode=='stacked':
                self.grid.addWidget(self.controls,next_row,0,1,3);next_row+=1
            status_row=next_row
        self.grid.addWidget(self.status,status_row,0,1,3)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.adapt()

    def event(self,event):
        result=super().event(event)
        if hasattr(self,'grid') and event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.adapt()
        return result
