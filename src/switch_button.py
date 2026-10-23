"""Accessible static toggle; redraw only on normal widget/input changes."""
from PySide6.QtCore import Qt,QSize,QRectF
from PySide6.QtGui import QPainter,QPen,QPalette
from PySide6.QtWidgets import QAbstractButton


class SwitchButton(QAbstractButton):
    def __init__(self,text,parent=None):
        super().__init__(parent);self.setText(text);self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setCursor(Qt.CursorShape.PointingHandCursor)
    def sizeHint(self):return self.minimumSizeHint()
    def minimumSizeHint(self):return QSize(self.fontMetrics().horizontalAdvance(self.text()+' · 开')+72,max(36,self.fontMetrics().height()+16))
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p=self.palette();label=self.text()+(' · 开' if self.isChecked() else ' · 关')
        painter.setPen(p.color(QPalette.ColorRole.WindowText));painter.drawText(self.rect().adjusted(4,0,-60,0),Qt.AlignmentFlag.AlignVCenter,label)
        track=QRectF(self.width()-50,(self.height()-24)/2,44,24)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(p.color(QPalette.ColorRole.Link if self.isChecked() else QPalette.ColorRole.Mid))
        painter.drawRoundedRect(track,12,12)
        painter.setBrush(p.color(QPalette.ColorRole.Window if self.isChecked() else QPalette.ColorRole.WindowText));x=track.right()-21 if self.isChecked() else track.left()+3
        painter.drawEllipse(QRectF(x,track.top()+3,18,18))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QPen(p.color(QPalette.ColorRole.Link),1,Qt.PenStyle.DashLine));painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),5,5)
