"""Shared application palette, including rich-text subtitles and Qt controls."""
import re
from PySide6.QtGui import QColor,QPalette
from typography import normalize_typography,make_font

# All visible application colors resolve through these user-editable roles.
COLOR_ROLES = [
    ('背景','window_bg','主窗口背景','#111824'),
    ('背景','sidebar_bg','侧栏背景','#0b121c'),
    ('背景','panel_bg','工具栏与面板背景','#162131'),
    ('背景','subtitle_bg','字幕区域背景','#111824'),
    ('背景','preview_bg','实时预览背景','#162131'),
    ('背景','input_bg','输入框与下拉框背景','#111c2a'),
    ('背景','menu_bg','菜单背景','#182537'),
    ('文字','text','全局普通文字','#dce5f0'),
    ('文字','heading','标题与品牌文字','#f3f7fc'),
    ('文字','muted','辅助说明文字','#8495ad'),
    ('文字','disabled_text','禁用状态文字','#596c85'),
    ('字幕','english','原文字幕文字','#eff5fc'),
    ('字幕','chinese','译文字幕文字','#85ddc4'),
    ('字幕','preview_text','实时预览文字','#9cafc7'),
    ('字幕','subtitle_meta','字幕时间与标记文字','#627c99'),
    ('字幕','warning','边界待核与警告文字','#d2ad76'),
    ('强调与状态','accent','强调色、焦点与音量条','#78ddc5'),
    ('强调与状态','accent_hover','强调按钮悬停背景','#a3efdd'),
    ('强调与状态','accent_text','强调按钮文字','#092c25'),
    ('强调与状态','accent_bg','NPU 状态底色','#12332f'),
    ('强调与状态','success','就绪与操作提示文字','#67d6bd'),
    ('控件','button_bg','普通按钮背景','#1a2535'),
    ('控件','hover_bg','按钮与列表悬停背景','#25354a'),
    ('控件','disabled_bg','禁用控件背景','#172030'),
    ('控件','selected_bg','选中项与文字选区背景','#1c303b'),
    ('控件','selected_text','选中项与文字选区文字','#e7fcf5'),
    ('控件','border','边框与分隔线','#29394e'),
    ('控件','border_hover','悬停边框','#46627f'),
    ('控件','scrollbar','滚动条','#34475d'),
    ('控件','track','音量条底色','#233247'),
    ('提示','tooltip_bg','工具提示背景','#182537'),
    ('提示','tooltip_text','工具提示文字','#dce5f0'),
]
# Four complete palettes, in light/dark pairs. Keep color semantics consistent:
# neutral reading surfaces, restrained translated text, and a distinct action color.
PALETTE_VALUES={
    # role                 Mist       Ink        Paper      Stone
    'window_bg':          ('#f4f5f7','#181b20','#f4f0e9','#211f1d'),
    'sidebar_bg':         ('#eceef2','#14171b','#eae4da','#1b1917'),
    'panel_bg':           ('#fafbfc','#22262d','#fbf7f0','#2b2824'),
    'subtitle_bg':        ('#fafbfc','#1c2026','#fbf7f0','#25221f'),
    'preview_bg':         ('#e9edf3','#242a33','#eee7dc','#302b26'),
    'input_bg':           ('#ffffff','#1a1e24','#fffaf3','#211e1b'),
    'menu_bg':            ('#fafbfc','#242830','#fbf7f0','#2b2824'),
    'text':               ('#303945','#d6dce5','#403b35','#e0d7ce'),
    'heading':            ('#202833','#edf1f7','#292621','#f3eae0'),
    'muted':              ('#626d7b','#9ca7b7','#6d6459','#b0a396'),
    'disabled_text':      ('#87909b','#687383','#978c7e','#7f7369'),
    'english':            ('#202833','#edf1f7','#292621','#f3eae0'),
    'chinese':            ('#476584','#a4bddc','#79583d','#d3b99b'),
    'preview_text':       ('#52647b','#b0bfd3','#6e5c49','#c6b4a2'),
    'subtitle_meta':      ('#647182','#99a4b4','#7b6d5d','#aa9b8c'),
    'warning':            ('#966015','#e9bf7a','#915523','#e8b57e'),
    'accent':             ('#355f98','#9bbbe8','#985737','#dfb18b'),
    'accent_hover':       ('#294d80','#bad0ef','#7e442a','#ecc6a5'),
    'accent_text':        ('#ffffff','#18263a','#ffffff','#302319'),
    'accent_bg':          ('#e3ecea','#22332f','#e4e9de','#2c3529'),
    'success':            ('#2e685a','#9bc9b5','#4d6947','#b4c7a4'),
    'button_bg':          ('#f9fafc','#272d36','#faf6ef','#302b26'),
    'hover_bg':           ('#e2e7ef','#323c4b','#e8ddce','#40362d'),
    'disabled_bg':        ('#e9edf1','#22272e','#eae3d9','#2b2622'),
    'selected_bg':        ('#dce5f2','#2b3b53','#ebdccb','#493829'),
    'selected_text':      ('#2c4567','#dce8fa','#65442e','#f2dfcc'),
    'border':             ('#d6dce4','#353c47','#dcd2c5','#443c34'),
    'border_hover':       ('#8c9cb2','#75869d','#a68d76','#99816b'),
    'scrollbar':          ('#a3afbf','#566375','#b9a792','#716151'),
    'track':              ('#dfe5ed','#343e4e','#e3d8c9','#46392d'),
    'tooltip_bg':         ('#303945','#353f4d','#403b35','#433a31'),
    'tooltip_text':       ('#fafbfc','#edf1f7','#fbf7f0','#f3eae0'),
}
PRESET_NAMES=('雾白','墨夜','米纸','暖岩')
PRESETS={name:{key:values[index] for key,values in PALETTE_VALUES.items()}
    for index,name in enumerate(PRESET_NAMES)}
THEME_FAMILIES={'neutral':('中性灰蓝','雾白','墨夜'),'warm':('柔和暖色','米纸','暖岩')}
PRESET_DESCRIPTIONS=('浅灰 · 雾蓝','石墨 · 灰蓝','米白 · 棕褐','暖灰 · 琥珀')
DEFAULT_COLORS=PRESETS['雾白']
COLOR_ROLES=[(group,key,label,DEFAULT_COLORS[key]) for group,key,label,_ in COLOR_ROLES]


def normalize_theme(value=None,strict=False):
    if value is None:return {'follow_system':False,'family':'neutral'}
    if not isinstance(value,dict):
        if strict:raise ValueError('配色模式必须是对象')
        value={}
    follow=value.get('follow_system',False);family=value.get('family','neutral')
    if type(follow) is not bool:
        if strict:raise ValueError('跟随系统须为布尔值')
        follow=False
    if not isinstance(family,str) or family not in THEME_FAMILIES:
        if strict:raise ValueError('不支持的配色组')
        family='neutral'
    return {'follow_system':follow,'family':family}


def effective_colors(colors,options=None,dark=False):
    options=normalize_theme(options)
    if options['follow_system']:
        return dict(PRESETS[THEME_FAMILIES[options['family']][2 if dark else 1]])
    return normalize_colors(colors)


def normalize_colors(values=None):
    result=dict(DEFAULT_COLORS)
    if isinstance(values,dict):
        for key in result:
            value=values.get(key)
            if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value.strip()):
                result[key]=value.strip().lower()
    return result


STYLE_TEMPLATE='''
QWidget {background:@window_bg; color:@text; font-family:"Microsoft YaHei UI"; font-size:13px;}
QFrame#sidebar {background:@sidebar_bg; border-right:1px solid @border;}
QFrame#sidebar QWidget {background:transparent;}
QLabel#brand {font-size:20px; font-weight:800; letter-spacing:2px; color:@heading;}
QLabel#muted {color:@muted; font-size:12px;}
QLabel#sectionHeading {font-size:17px; font-weight:600; color:@heading;}
QLabel#dialogHeading {font-size:23px; font-weight:700; padding-bottom:10px;}
QLabel#heading {font-size:30px; font-weight:700; color:@heading;}
QLabel#badge {color:@success; background:@accent_bg; border:1px solid @border_hover; border-radius:10px; padding:4px 10px; font-size:11px;}
QLabel#eyebrow {color:@accent; font-size:11px; font-weight:600; letter-spacing:1px;}
QPushButton,QToolButton {background:@button_bg; border:1px solid @border; border-radius:8px; padding:9px 13px; color:@text;}
QPushButton:hover,QToolButton:hover {background:@hover_bg; border-color:@border_hover;}
QPushButton:disabled,QToolButton:disabled {color:@disabled_text; background:@disabled_bg; border-color:@border;}
QPushButton#primary {background:@accent; color:@accent_text; border-color:@accent; font-weight:700;}
QPushButton#primary:hover {background:@accent_hover;}
QPushButton#primary:disabled {background:@disabled_bg; color:@disabled_text; border-color:@border;}
QPushButton#quietButton {background:transparent; border:0; color:@muted; padding:5px;}
QPushButton#quietButton:hover {color:@accent;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit {background:@input_bg; border:1px solid @border; border-radius:7px; padding:8px; selection-background-color:@selected_bg; selection-color:@selected_text;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QPlainTextEdit:focus {border-color:@accent;}
QAbstractItemView {background:@input_bg; color:@text; selection-background-color:@selected_bg; selection-color:@selected_text;}
QListWidget {background:transparent; border:0; outline:0; padding:2px;}
QListWidget::item {padding:13px 11px; border-radius:8px; margin:3px 0; color:@muted;}
QListWidget::item:selected {background:@selected_bg; color:@selected_text; border-left:3px solid @accent;}
QListWidget::item:hover {background:@hover_bg;}
QListWidget#settingsNavigation::item {padding:7px 10px; margin:1px 0;}
QListWidget#historyList::item {padding:0; margin:3px 0;}
QFrame#historyRow {background:transparent; border:0;}
QFrame#historyRow QLabel {background:transparent;}
QLabel#historyTitle {color:@text;}
QLabel#historyDetail {color:@muted; font-size:11px;}
QFrame#historyRow[selected="true"] QLabel {color:@selected_text;}
QToolButton#historyMenu {background:transparent; border:0; padding:0; font-weight:700;}
QToolButton#historyMenu:hover {background:@hover_bg; color:@accent;}
QWidget#headerControls QToolButton {padding:5px 9px;}
QWidget#workspaceHeader QPushButton#primary {padding:6px 12px;}
QFrame#workspaceFooter {background:transparent; border:0;}
QFrame#workspaceFooter QLabel {background:transparent; font-size:11px; padding:0;}
QTextBrowser {background:@subtitle_bg; border:0; padding:0; selection-background-color:@selected_bg; selection-color:@selected_text;}
QFrame#toolbar {background:@panel_bg; border:1px solid @border; border-radius:11px;}
QFrame#preview {background:@preview_bg; border:1px solid @border; border-radius:12px;}
QFrame#preview QLabel {background:transparent;}
QProgressBar {border:0; background:@track; border-radius:2px;}
QProgressBar::chunk {background:@accent; border-radius:2px;}
QCheckBox {spacing:7px; background:transparent; color:@text;}
QCheckBox::indicator {width:14px; height:14px; border:1px solid @border_hover; border-radius:3px; background:@input_bg;}
QCheckBox::indicator:checked {background:@accent; border-color:@accent;}
QCheckBox:disabled {color:@disabled_text;}
QTabWidget::pane {border:1px solid @border; border-radius:9px;}
QTabBar::tab {background:@panel_bg; color:@muted; padding:10px 13px; border-bottom:2px solid transparent;}
QTabBar::tab:selected {color:@accent; border-bottom-color:@accent;}
QMenu {background:@menu_bg; border:1px solid @border; padding:7px;}
QMenu::item {padding:9px 25px 9px 12px; border-radius:5px; color:@text;}
QMenu::item:selected {background:@selected_bg; color:@selected_text;}
QMenu::separator {height:1px; background:@border; margin:4px;}
QScrollBar:vertical {background:transparent; width:7px; margin:0;}
QScrollBar::handle:vertical {background:@scrollbar; min-height:28px; border-radius:3px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:none;}
QScrollBar:horizontal {background:transparent; height:7px;}
QScrollBar::handle:horizontal {background:@scrollbar; min-width:28px; border-radius:3px;}
QSplitter::handle {background:@border; width:1px;}
QToolTip {background:@tooltip_bg; color:@tooltip_text; border:1px solid @border; padding:6px;}
QScrollArea#colorScroll {border:0; background:transparent;}
QFrame#themeSample {background:@subtitle_bg; border:1px solid @border; border-radius:8px;}
QFrame#themeSample QLabel {background:transparent;}
QLabel#colorValidation {color:@warning;}
'''


def stylesheet(colors=None,typography=None):
    colors=normalize_colors(colors)
    t=normalize_typography(typography)
    style=re.sub(r'@(\w+)',lambda match:colors[match[1]],STYLE_TEMPLATE)
    style=style.replace('font-family:"Microsoft YaHei UI";',f'font-family:"{t["ui_font"]}"; letter-spacing:{t["ui_letter"]}px;')
    style=re.sub(r'font-size:(\d+)px',lambda m:f'font-size:{round(int(m[1])*t["ui_size"]/13)}px',style)
    style=style.replace('padding:8px;',f'padding:{t["control_padding"]}px;')
    return style+'\nQFrame#preview QTextBrowser {background:transparent;}\n'


STYLE=stylesheet()


def apply_theme(application,colors,typography=None):
    colors=normalize_colors(colors)
    t=normalize_typography(typography)
    style=stylesheet(colors,t)
    # Caption fonts/spacing do not change widget CSS. Avoid repolishing every
    # control and font picker for an unrelated subtitle edit.
    theme_key=(tuple(sorted(colors.items())),t['ui_font'],t['ui_size'],t['ui_letter'],t['control_padding'])
    if getattr(application,'_parakeet_theme_key',None)==theme_key and application.styleSheet()==style:return False
    palette=QPalette()
    roles={'Window':'window_bg','WindowText':'text','Base':'input_bg','AlternateBase':'panel_bg',
        'Text':'text','Button':'button_bg','ButtonText':'text','BrightText':'heading',
        'Highlight':'selected_bg','HighlightedText':'selected_text','ToolTipBase':'tooltip_bg',
        'ToolTipText':'tooltip_text','PlaceholderText':'muted','Link':'accent','LinkVisited':'chinese',
        'Light':'border_hover','Midlight':'panel_bg','Mid':'border','Dark':'border','Shadow':'border'}
    for role,key in roles.items():
        palette.setColor(getattr(QPalette.ColorRole,role),QColor(colors[key]))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Text,QColor(colors['disabled_text']))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.ButtonText,QColor(colors['disabled_text']))
    if application.palette()!=palette:application.setPalette(palette)
    font=make_font(t,'ui',t['ui_size'])
    if application.font()!=font:application.setFont(font)
    application.setStyleSheet(style)
    application._parakeet_theme_key=theme_key
    return True
