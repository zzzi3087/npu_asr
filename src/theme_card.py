"""Static miniature workspaces for the four presets; keyboard accessible."""
from PySide6.QtCore import Qt,QRectF,QSize
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import QAbstractButton,QSizePolicy


class ThemeCard(QAbstractButton):
    def __init__(self,name,description,colors,parent=None):
        super().__init__(parent)
        self.colors=colors;self.description=description
        self.setText(name);self.setAccessibleName(name+' · '+description)
        self.setToolTip(name+'：'+description+'。点击使用此配色，并关闭自动切换。')
        self.setCheckable(True);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setFixedHeight(120);self.setMinimumWidth(100)

    def sizeHint(self):return QSize(140,120)

    def paintEvent(self,event):
        c=self.colors;p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box=QRectF(2,2,self.width()-4,self.height()-4)
        p.setPen(QPen(QColor(c['accent'] if self.isChecked() or self.hasFocus() else c['border']),2))
        p.setBrush(QColor(c['window_bg']));p.drawRoundedRect(box,9,9)
        # A sidebar, original/translation lines and an action suggest the real UI.
        x=box.x()+10;y=box.y()+10;w=box.width()-20
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(c['sidebar_bg']))
        p.drawRoundedRect(QRectF(x,y,w*.22,48),4,4)
        p.setBrush(QColor(c['subtitle_bg']));p.drawRoundedRect(QRectF(x+w*.28,y,w*.72,48),4,4)
        for dx,dy,dw,color in ((.32,10,.54,'english'),(.32,20,.42,'chinese'),(.03,8,.15,'selected_bg'),(.70,33,.23,'accent')):
            p.setBrush(QColor(c[color]));p.drawRoundedRect(QRectF(x+w*dx,y+dy,w*dw,5),2,2)
        f=self.font();f.setBold(True);p.setFont(f);p.setPen(QColor(c['heading']))
        text_x=x
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(c['accent']))
            p.drawEllipse(QRectF(x,y+62,6,6));text_x+=13
            p.setPen(QColor(c['heading']))
        p.drawText(QRectF(text_x,y+54,w-(text_x-x),23),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.text())
        f.setBold(False);f.setPointSizeF(max(8,f.pointSizeF()-1));p.setFont(f);p.setPen(QColor(c['muted']))
        p.drawText(QRectF(x,y+79,w,19),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.description)
