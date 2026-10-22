"""Portable transparent desktop caption settings, independent of the workbench."""
import math,re
from typography import valid_family

def default_desktop_captions():
    return dict(enabled=True,locked=True,topmost=True,sentence_count=2,width=1000,max_lines=3,
        source_font='Microsoft YaHei UI',translation_font='Microsoft YaHei UI',source_size=36,translation_size=32,
        source_color='#ffffff',translation_color='#ffffff',outline_color='#000000',outline_width=2.5,
        bold=True,line_height=1.15,bilingual_gap=6,sentence_gap=14,screen='',x=None,bottom=None,
        preview_enabled=True,preview_font='Microsoft YaHei UI',preview_size=28,preview_color='#ffffff',preview_gap=14)

def normalize_desktop_captions(value=None,strict=False):
    result=default_desktop_captions()
    if value is None:return result
    if not isinstance(value,dict):
        if strict:raise ValueError('桌面字幕配置必须是对象')
        return result
    limits={'sentence_count':(1,5),'width':(320,2400),'max_lines':(1,8),'source_size':(12,96),'translation_size':(12,96),
        'outline_width':(0,8),'line_height':(1,2),'bilingual_gap':(0,40),'sentence_gap':(0,60),'preview_size':(12,96),'preview_gap':(0,60)}
    for key,item in value.items():
        if key not in result:continue
        okay=False
        if key in ('enabled','locked','topmost','bold','preview_enabled'):okay=type(item) is bool
        elif key.endswith('_font'):okay=valid_family(item)
        elif key.endswith('_color'):okay=isinstance(item,str) and bool(re.fullmatch(r'#[0-9a-fA-F]{6}',item))
        elif key in ('x','bottom'):okay=item is None or (type(item) is int and -100000<=item<=100000)
        elif key=='screen':okay=isinstance(item,str) and len(item)<=200
        elif key in limits:
            lo,hi=limits[key];okay=type(item) in (int,float) and math.isfinite(item) and lo<=item<=hi
            if isinstance(result[key],int):okay=okay and int(item)==item
            if okay:item=int(item) if isinstance(result[key],int) else float(item)
        if okay:result[key]=item
        elif strict:raise ValueError('桌面字幕参数无效：'+key)
    return result
