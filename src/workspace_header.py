"""Two compact header rows, with one extra row when the window is narrow."""
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QWidget,QGridLayout,QSizePolicy


class WorkspaceHeader(QWidget):
    def __init__(self,title,controls,status,parent=None):
        super().__init__(parent)
        self.setObjectName('workspaceHeader')
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.title_group=title;self.controls=controls;self.status=status;self.narrow=None
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0)
        self.grid.setHorizontalSpacing(10);self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(0,1)
        self.adapt()

    def adapt(self):
        threshold=max(180,self.title_group.minimumSizeHint().width())+self.controls.sizeHint().width()+10
        narrow=self.width()<threshold
        if self.narrow==narrow:return
        self.narrow=narrow
        for widget in (self.title_group,self.controls,self.status):self.grid.removeWidget(widget)
        self.grid.addWidget(self.title_group,0,0,1,2 if narrow else 1)
        self.grid.addWidget(self.controls,1 if narrow else 0,0 if narrow else 1,1,2 if narrow else 1,
            Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(self.status,2 if narrow else 1,0,1,2)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.adapt()

    def event(self,event):
        result=super().event(event)
        if hasattr(self,'grid') and event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.adapt()
        return result
