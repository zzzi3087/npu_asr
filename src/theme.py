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
DEFAULT_COLORS={key:value for _,key,_,value in COLOR_ROLES}
LIGHT_COLORS=dict(DEFAULT_COLORS,window_bg='#f4f6fa',sidebar_bg='#e7edf4',panel_bg='#ffffff',
    subtitle_bg='#ffffff',preview_bg='#e8f4ef',input_bg='#ffffff',menu_bg='#ffffff',
    text='#243449',heading='#14263d',muted='#5b6d83',disabled_text='#8994a3',
    english='#14263d',chinese='#086c56',preview_text='#49627a',subtitle_meta='#61758b',warning='#926019',
    accent='#087e67',accent_hover='#096952',accent_text='#ffffff',accent_bg='#d6eee6',success='#087258',
    button_bg='#f0f4f8',hover_bg='#dce7ef',disabled_bg='#e9edf2',selected_bg='#d0e9e0',
    selected_text='#173f35',border='#cad5e1',border_hover='#829ab1',scrollbar='#a6b6c7',track='#dce4ed',
    tooltip_bg='#243449',tooltip_text='#ffffff')
WARM_COLORS=dict(LIGHT_COLORS,window_bg='#f4eee3',sidebar_bg='#e9dfce',panel_bg='#fffaf1',
    subtitle_bg='#fffaf1',preview_bg='#eee5d5',input_bg='#fffaf1',menu_bg='#fffaf1',
    text='#483e32',heading='#332a21',muted='#776955',english='#332a21',chinese='#56683a',
    preview_text='#76674e',subtitle_meta='#89765b',accent='#687b47',accent_hover='#536436',
    accent_bg='#e1e7d1',success='#526b36',button_bg='#f1e7d5',hover_bg='#e4d6bc',
    selected_bg='#e0e6cc',selected_text='#344322',border='#d3c4aa',border_hover='#a99777',
    scrollbar='#beac8b',track='#e2d7c3')
PRESETS={'深海夜色':DEFAULT_COLORS,'明亮简洁':LIGHT_COLORS,'暖色纸张':WARM_COLORS}


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
