"""Validated typography and native QTextDocument layout (including line height)."""
import math,re
from PySide6.QtGui import QFont,QTextCursor,QTextCharFormat,QTextBlockFormat,QColor
from caption_visibility import caption_visibility,visible_caption_pair

FONT_FIELDS={'ui_font','english_font','chinese_font','preview_font'}
LIMITS={
    'ui_size':(10,20,13),'preview_size':(12,72,24),'preview_height':(32,240,40),
    'english_line':(1.,3.,1.35),'chinese_line':(1.,3.,1.35),'preview_line':(1.,3.,1.35),
    'paragraph_gap':(0,100,25),'bilingual_gap':(0,60,7),'meta_gap':(0,40,6),
    'padding_x':(0,80,4),'padding_y':(0,80,4),
    'english_letter':(-1.,10.,0.),'chinese_letter':(-1.,10.,0.),'preview_letter':(-1.,10.,0.),
    'ui_letter':(-.5,5.,0.),'english_word':(0.,20.,0.),'chinese_word':(0.,20.,0.),
    'content_margin':(8,60,32),'layout_gap':(4,36,18),'control_padding':(4,16,8),
}


def default_typography():
    return dict({key:'Microsoft YaHei UI' for key in FONT_FIELDS},
        **{key:spec[2] for key,spec in LIMITS.items()},english_bold=False,chinese_bold=False)


def valid_family(value):
    return isinstance(value,str) and 0<len(value.strip())<=120 and not re.search(r'[";{}\\\r\n<>]',value)


def normalize_typography(values=None,strict=False):
    result=default_typography()
    if values is None:return result
    if not isinstance(values,dict):
        if strict:raise ValueError('字体与间距配置必须是对象')
        return result
    for key,value in values.items():
        if key not in result:continue
        okay=False
        if key in FONT_FIELDS:
            okay=valid_family(value)
            if okay:value=value.strip()
        elif key in LIMITS:
            lo,hi,default=LIMITS[key]
            okay=type(value) in (int,float) and math.isfinite(value) and lo<=value<=hi
            if okay:value=int(value) if isinstance(default,int) else float(value)
        else:okay=type(value) is bool
        if okay:result[key]=value
        elif strict:raise ValueError('字体与间距参数无效：'+key)
    return result


def make_font(t,kind,size):
    font=QFont(t[kind+'_font'])
    font.setPixelSize(size)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,t.get(kind+'_letter',0.))
    font.setWordSpacing(t.get(kind+'_word',0.))
    font.setBold(t.get(kind+'_bold',False))
    return font


class CaptionFormatter:
    """Shared row formatter for full and incremental caption rendering."""
    def __init__(self,colors,settings):
        self.t=normalize_typography(settings.get('typography'))
        self.colors=colors
        self.fonts={kind:make_font(self.t,kind,settings[kind+'_size']) for kind in ('english','chinese')}
        self.meta_font=make_font(self.t,'ui',max(10,self.t['ui_size']-2))
        self.show_source,self.show_translation=caption_visibility(settings)
        self.show_pending_source=settings.get('show_pending_source') is True

    def insert_row(self,cursor,row,first=False,new_block=False):
        t=self.t
        count=0
        def block(text,color,font,line=1.,bottom=0,top=0):
            nonlocal count
            fmt=QTextBlockFormat()
            fmt.setLineHeight(line*100,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setTopMargin(top+(t['padding_y'] if first and count==0 else 0))
            fmt.setBottomMargin(bottom)
            fmt.setLeftMargin(t['padding_x']);fmt.setRightMargin(t['padding_x'])
            char=QTextCharFormat();char.setFont(font);char.setForeground(QColor(color))
            if count==0 and not new_block:cursor.setBlockFormat(fmt);cursor.setCharFormat(char)
            else:cursor.insertBlock(fmt,char)
            cursor.insertText(text,char)
            count+=1
        source,translation=visible_caption_pair(row['en'],row['zh'],self.show_source,self.show_translation,status=row.get('status'),show_pending_source=self.show_pending_source)
        if not source and not translation:return 0
        seconds=max(0,row['start_ms']//1000)
        stamp=f'{seconds//60:02d}:{seconds%60:02d}'
        if row.get('timing')=='approximate':stamp='≈ '+stamp
        if row.get('provisional'):stamp+=' · 临时译文 · 等待确认' if row['zh'] else ' · 临时字幕 · 等待确认'
        block(stamp,self.colors['subtitle_meta'],self.meta_font,bottom=t['meta_gap'])
        if row['uncertain']:
            char=QTextCharFormat();char.setFont(self.meta_font);char.setForeground(QColor(self.colors['warning']))
            cursor.insertText(' · 边界待核',char)
        if source:block(source,self.colors['english'],self.fonts['english'],t['english_line'],
            t['bilingual_gap'] if translation else t['paragraph_gap'])
        if translation:block(translation,self.colors['chinese'],self.fonts['chinese'],t['chinese_line'],t['paragraph_gap'])
        return count

    def insert_placeholder(self,cursor):
        t=self.t
        fmt=QTextBlockFormat()
        fmt.setLineHeight(100,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        fmt.setTopMargin(t['padding_y'])
        fmt.setLeftMargin(t['padding_x']);fmt.setRightMargin(t['padding_x'])
        char=QTextCharFormat();char.setFont(make_font(t,'ui',t['ui_size']));char.setForeground(QColor(self.colors['muted']))
        cursor.setBlockFormat(fmt);cursor.setCharFormat(char);cursor.insertText('正在等待确认的语音…',char)


def _text_block(doc,first=False):
    block=doc.firstBlock() if first else doc.lastBlock()
    step=(lambda b:b.next()) if first else (lambda b:b.previous())
    while block.isValid() and not block.text() and step(block).isValid():block=step(block)
    return block


def apply_tail_padding(doc,formatter):
    block=_text_block(doc)
    if not block.isValid():return
    cursor=QTextCursor(doc);cursor.setPosition(block.position())
    fmt=block.blockFormat();fmt.setBottomMargin(formatter.t['padding_y']+formatter.t['paragraph_gap'])
    cursor.setBlockFormat(fmt)


def clear_tail_padding(doc,formatter):
    block=_text_block(doc)
    if not block.isValid():return
    cursor=QTextCursor(doc);cursor.setPosition(block.position())
    fmt=block.blockFormat();fmt.setBottomMargin(formatter.t['paragraph_gap'])
    cursor.setBlockFormat(fmt)


def apply_head_padding(doc,formatter):
    block=_text_block(doc,first=True)
    if not block.isValid():return
    cursor=QTextCursor(doc);cursor.setPosition(block.position())
    fmt=block.blockFormat();fmt.setTopMargin(formatter.t['padding_y'] if block.text() else 0)
    cursor.setBlockFormat(fmt)


def render_caption_rows(browser,rows,colors,settings):
    """Rebuild the whole document; return each row's (first block, count)."""
    doc=browser.document()
    doc.clear();doc.setDocumentMargin(0)
    cursor=QTextCursor(doc)
    cursor.beginEditBlock()
    formatter=CaptionFormatter(colors,settings)
    mapping=[];inserted=0;first=True
    for row in rows:
        count=formatter.insert_row(cursor,row,first=first,new_block=inserted>0)
        mapping.append((inserted,count))
        inserted+=count
        if count:first=False
    # Space below the last caption without changing subtitle time or content.
    if rows:apply_tail_padding(doc,formatter)
    else:formatter.insert_placeholder(cursor)
    cursor.endEditBlock()
    return mapping


def render_captions(browser,rows,colors,settings):
    render_caption_rows(browser,rows,colors,settings)
