"""Event-driven, transparent outlined captions. No frame loop or animation."""
import math
from collections import OrderedDict
from PySide6.QtCore import Qt,Signal,QTimer,QPointF,QEvent
from PySide6.QtGui import (QGuiApplication,QPainter,QPainterPath,QPen,QColor,QImage,QFont,QFontMetricsF,
    QTextLayout,QTextOption,QTransform)
from PySide6.QtWidgets import QWidget,QMenu
from desktop_caption_config import normalize_desktop_captions

SAMPLE_PAIRS=(('The universe still holds many mysteries.','宇宙仍有许多未解之谜。'),
              ('We search for dark matter and axions.','我们寻找暗物质与轴子。'))


class DesktopCaptions(QWidget):
    position_changed=Signal(object)
    enabled_requested=Signal(bool)
    locked_requested=Signal(bool)
    settings_requested=Signal()

    def __init__(self,options=None):
        flags=Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(None,flags)
        self.setObjectName('desktopCaptionOverlay')
        self.setWindowTitle('实时字幕 · 桌面字幕')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet('QWidget#desktopCaptionOverlay { background: transparent; border: none; }')
        self.options={};self.pairs=();self.preview=False;self._pending=False;self._building=False;self._shutdown=False
        self.partial='';self.live_active=False
        self._key=None;self._image=QImage();self._glyphs=OrderedDict();self._drag=None
        self.rebuild_count=0;self.paint_count=0
        self._watched_screens=set()
        for screen in QGuiApplication.screens():self._watch_screen(screen)
        self.configure(options)
        QGuiApplication.instance().screenAdded.connect(self._screen_changed)
        QGuiApplication.instance().screenRemoved.connect(self._screen_changed)

    def configure(self,options,preview=False):
        options=normalize_desktop_captions(options)
        old=self.options;self.options=options;self.preview=preview
        if old.get('locked')!=options['locked']:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput,options['locked'])
        if old.get('topmost')!=options['topmost']:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,options['topmost'])
        if any(old.get(k)!=options[k] for k in ('source_font','translation_font','source_size','translation_size','preview_font','preview_size','bold')):
            self._glyphs.clear()
        self.request_refresh()

    def set_pairs(self,pairs):
        pairs=tuple((str(en).strip()[:8192],str(zh).strip()[:8192]) for en,zh in pairs if str(en).strip() or str(zh).strip())
        if pairs==self.pairs:return
        self.pairs=pairs;self.request_refresh()

    def set_partial(self,text,active=True):
        text=str(text).strip()[:8192];active=bool(active)
        if (text,active)==(self.partial,self.live_active):return
        self.partial=text;self.live_active=active
        if self.options['preview_enabled']:self.request_refresh()

    def request_refresh(self):
        if self._shutdown:return
        if not self.options['enabled']:
            self.hide();return
        if not self._pending:
            self._pending=True;QTimer.singleShot(0,self._rebuild)

    def _screen(self):
        screens=QGuiApplication.screens()
        selected=next((s for s in screens if s.name()==self.options['screen']),None)
        if selected is None and self.options['x'] is not None and self.options['bottom'] is not None:
            selected=QGuiApplication.screenAt(QPointF(self.options['x'],self.options['bottom']-1).toPoint())
        return selected or QGuiApplication.primaryScreen()

    def _screen_changed(self,*_):
        for screen in QGuiApplication.screens():self._watch_screen(screen)
        if not self._shutdown:self._key=None;self.request_refresh()

    def _watch_screen(self,screen):
        if screen not in self._watched_screens:
            self._watched_screens.add(screen)
            screen.availableGeometryChanged.connect(self._screen_changed)

    def _font(self,kind):
        font=QFont(self.options[kind+'_font']);font.setPixelSize(self.options[kind+'_size']);font.setBold(self.options['bold'])
        return font

    def _glyph_path(self,raw,index):
        key=(raw.familyName(),raw.styleName(),raw.pixelSize(),raw.weight(),raw.style(),index)
        path=self._glyphs.get(key)
        if path is None:
            path=raw.pathForGlyph(index);self._glyphs[key]=path
            if len(self._glyphs)>1024:self._glyphs.popitem(last=False)
        else:self._glyphs.move_to_end(key)
        return path

    def _text_path(self,text,font,width,max_lines=None):
        if not text:return QPainterPath(),QFontMetricsF(font).height()
        text=text.replace('\r','').replace('\n','\u2028')
        def layout_text(value):
            layout=QTextLayout(value,font);option=QTextOption(Qt.AlignmentFlag.AlignHCenter)
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere);layout.setTextOption(option)
            layout.beginLayout();lines=[];y=0.
            for _ in range(max_lines or self.options['max_lines']):
                line=layout.createLine()
                if not line.isValid():break
                line.setLineWidth(width);line.setPosition(QPointF(0,y));lines.append(line)
                y+=line.height()*self.options['line_height']
            layout.endLayout();return layout,lines
        layout,lines=layout_text(text)
        if not lines:return QPainterPath(),0.
        # QTextLine offsets are UTF-16 units, including astral math/emoji glyphs.
        encoded=text.encode('utf-16-le');last=lines[-1]
        if (last.textStart()+last.textLength())*2<len(encoded):
            offset=last.textStart()*2
            prefix=encoded[:offset].decode('utf-16-le')
            tail=encoded[offset:].decode('utf-16-le').replace('\u2028',' ')
            tail=QFontMetricsF(font).elidedText(tail,Qt.TextElideMode.ElideRight,width)
            layout,lines=layout_text(prefix+tail)
        path=QPainterPath()
        for run in layout.glyphRuns():
            raw=run.rawFont()
            for index,position in zip(run.glyphIndexes(),run.positions()):
                transform=QTransform.fromTranslate(position.x(),position.y())
                path.addPath(transform.map(self._glyph_path(raw,index)))
        return path,lines[-1].y()+lines[-1].height()

    def _rebuild(self):
        self._pending=False
        if self._shutdown or not self.options['enabled']:self.hide();return
        pairs=self.pairs[-self.options['sentence_count']:]
        sample=self.preview or (not self.options['locked'] and not self.live_active)
        if not pairs and sample:pairs=SAMPLE_PAIRS[-self.options['sentence_count']:]
        live=('实时预览 · '+(self.partial or '正在聆听…')) if self.live_active else ('实时预览 · This sentence is still being recognized.' if sample else '')
        if not self.options['preview_enabled']:live=''
        if not pairs and not live:self.hide();return
        screen=self._screen();area=screen.availableGeometry()
        width=min(self.options['width'],max(160,area.width()-32));max_height=max(80,area.height()-32)
        dpr=self.devicePixelRatioF()
        drawing=tuple((k,v) for k,v in self.options.items() if k not in ('enabled','topmost','screen','x','bottom'))
        key=(pairs,live,drawing,width,max_height,dpr)
        self._building=True
        try:
            if key!=self._key:
                padding=12+self.options['outline_width'];y=padding;blocks=[]
                for n,(source,translation) in enumerate(pairs):
                    if n:y+=self.options['sentence_gap']
                    for kind,text in [('source',source),('translation',translation)]:
                        if kind=='translation':y+=self.options['bilingual_gap']
                        path,height=self._text_path(text,self._font(kind),width-2*padding)
                        blocks.append((path,QColor(self.options[kind+'_color']),y));y+=height
                if live:
                    if pairs:y+=self.options['preview_gap']
                    path,height=self._text_path(live,self._font('preview'),width-2*padding,max_lines=1)
                    blocks.append((path,QColor(self.options['preview_color']),y));y+=height
                natural_height=math.ceil(y+padding);scale=min(1.,max_height/natural_height)
                height=math.ceil(natural_height*scale)
                image=QImage(math.ceil(width*dpr),math.ceil(height*dpr),QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr);image.fill(Qt.GlobalColor.transparent)
                painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.translate(width*(1-scale)/2,0);painter.scale(scale,scale)
                pen=QPen(QColor(self.options['outline_color']),2*self.options['outline_width'],Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)
                for path,color,top in blocks:
                    painter.save();painter.translate(padding,top)
                    if self.options['outline_width']:painter.strokePath(path,pen)
                    painter.fillPath(path,color);painter.restore()
                painter.resetTransform()
                if not self.options['locked']:
                    painter.setPen(QPen(QColor(self.options['outline_color']),1,Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRect(1,1,width-3,height-3)
                painter.end();self._image=image;self._key=key;self.rebuild_count+=1
                self.resize(width,height);self.update()
            x=self.options['x'];bottom=self.options['bottom']
            x=area.x()+(area.width()-self.width())//2 if x is None else x
            bottom=area.bottom()+1-64 if bottom is None else bottom
            x=max(area.left(),min(x,area.right()+1-self.width()))
            top=max(area.top(),min(bottom-self.height(),area.bottom()+1-self.height()))
            self.move(int(x),int(top))
            if not self.isVisible():self.show()
        finally:self._building=False

    def paintEvent(self,event):
        self.paint_count+=1
        painter=QPainter(self);painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(),Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not self._image.isNull():painter.drawImage(QPointF(0,0),self._image)

    def event(self,event):
        result=super().event(event)
        if event.type()==QEvent.Type.DevicePixelRatioChange and hasattr(self,'_pending'):self.request_refresh()
        return result

    def mousePressEvent(self,event):
        if not self.options['locked'] and event.button()==Qt.MouseButton.LeftButton:
            self._drag=event.globalPosition().toPoint()-self.pos();event.accept()

    def mouseMoveEvent(self,event):
        if self._drag is not None:self.move(event.globalPosition().toPoint()-self._drag);event.accept()

    def mouseReleaseEvent(self,event):
        if self._drag is not None and event.button()==Qt.MouseButton.LeftButton:
            self._drag=None;screen=QGuiApplication.screenAt(self.geometry().center()) or self._screen()
            position=dict(x=self.x(),bottom=self.y()+self.height(),screen=screen.name())
            self.options.update(position);self.position_changed.emit(position);self.request_refresh();event.accept()

    def contextMenuEvent(self,event):
        if self.options['locked']:return
        menu=QMenu(self)
        menu.addAction('锁定位置并穿透鼠标').triggered.connect(lambda:self.locked_requested.emit(True))
        menu.addAction('隐藏桌面字幕').triggered.connect(lambda:self.enabled_requested.emit(False))
        menu.exec(event.globalPos())

    def shutdown(self):
        self._shutdown=True;self.close()

    def closeEvent(self,event):
        if not self._shutdown:self.enabled_requested.emit(False)
        event.accept()
