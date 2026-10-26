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


def render_captions(browser,rows,colors,settings):
    t=normalize_typography(settings.get('typography'))
    doc=browser.document()
    doc.clear();doc.setDocumentMargin(0)
    cursor=QTextCursor(doc)
    cursor.beginEditBlock()
    first=True
    def block(text,color,font,line=1.,bottom=0,top=0):
        nonlocal first
        fmt=QTextBlockFormat()
        fmt.setLineHeight(line*100,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        fmt.setTopMargin(top+(t['padding_y'] if first else 0))
        fmt.setBottomMargin(bottom)
        fmt.setLeftMargin(t['padding_x']);fmt.setRightMargin(t['padding_x'])
        char=QTextCharFormat();char.setFont(font);char.setForeground(QColor(color))
        if first:cursor.setBlockFormat(fmt);cursor.setCharFormat(char);first=False
        else:cursor.insertBlock(fmt,char)
        cursor.insertText(text,char)
    fonts={kind:make_font(t,kind,settings[kind+'_size']) for kind in ('english','chinese')}
    meta_font=make_font(t,'ui',max(10,t['ui_size']-2))
    show_source,show_translation=caption_visibility(settings)
    for row in rows:
        source,translation=visible_caption_pair(row['en'],row['zh'],show_source,show_translation)
        if not source and not translation:continue
        seconds=max(0,row['start_ms']//1000)
        stamp=f'{seconds//60:02d}:{seconds%60:02d}'
        if row.get('timing')=='approximate':stamp='≈ '+stamp
        if row.get('provisional'):stamp+=' · 临时译文 · 等待整句' if row['zh'] else ' · 长句预译'
        block(stamp,colors['subtitle_meta'],meta_font,bottom=t['meta_gap'])
        if row['uncertain']:
            char=QTextCharFormat();char.setFont(meta_font);char.setForeground(QColor(colors['warning']))
            cursor.insertText(' · 边界待核',char)
        if source:block(source,colors['english'],fonts['english'],t['english_line'],
            t['bilingual_gap'] if translation else t['paragraph_gap'])
        if translation:block(translation,colors['chinese'],fonts['chinese'],t['chinese_line'],t['paragraph_gap'])
    # Space below the last caption without changing subtitle time or content.
    if rows:
        fmt=cursor.blockFormat();fmt.setBottomMargin(t['padding_y']+t['paragraph_gap']);cursor.setBlockFormat(fmt)
    else:
        block('正在等待确认的语音…',colors['muted'],make_font(t,'ui',t['ui_size']))
    cursor.endEditBlock()
