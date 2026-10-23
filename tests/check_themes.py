"""Offline appearance integration: OS notifications, drafts, persistence and readability."""
import os,sys,json,argparse,copy
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
temporary=TemporaryDirectory(prefix='captions-themes-');test_root=Path(temporary.name)
artifacts=args.artifacts or test_root;artifacts.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(test_root/'data')
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import Qt,QTimer,QEventLoop,QCoreApplication,QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtTest import QTest
from theme import PRESETS,COLOR_ROLES,effective_colors
from storage import default_settings,save_settings,load_settings
from config_io import export_configuration,import_configuration,validate_configuration
from settings_dialog import SettingsDialog
from app import Window


def luminance(color):
    rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(v*k for v,k in zip(rgb,(.2126,.7152,.0722)))


def contrast(a,b):
    light,dark=sorted((luminance(a),luminance(b)),reverse=True)
    return (light+.05)/(dark+.05)


ratios={}
for name,c in PRESETS.items():
    assert set(c)=={role[1] for role in COLOR_ROLES}
    pairs=[('text',bg) for bg in ('window_bg','sidebar_bg','panel_bg','input_bg','button_bg','menu_bg')]
    pairs+=[('muted',bg) for bg in ('window_bg','sidebar_bg')]
    pairs+=[('english','subtitle_bg'),('chinese','subtitle_bg'),('subtitle_meta','subtitle_bg'),
        ('preview_text','preview_bg'),('accent_text','accent'),('accent_text','accent_hover'),
        ('selected_text','selected_bg'),('tooltip_text','tooltip_bg'),('success','accent_bg'),('warning','window_bg')]
    ratios[name]=min(contrast(c[a],c[b]) for a,b in pairs)
    for a,b in pairs:assert contrast(c[a],c[b])>=4.5,(name,a,b,contrast(c[a],c[b]))

qt=QApplication([])
for file in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf'):
    if Path(file).exists():QFontDatabase.addApplicationFont(file)
settings=default_settings();settings['translate']=False;save_settings(settings)


def settle(ms=15):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


w=Window(start_workers=False);w.show();settle()
sid=w.history.create_session('暗物质与宇宙 · 示例记录',w.profile());w.view_session=w.current_session=sid
for n,(en,zh) in enumerate([
        ('The universe still holds many mysteries.','宇宙仍藏着许多未解之谜。'),
        ('We search for dark matter through its gravitational effects.','我们通过引力效应寻找暗物质。'),
        ('Every observation brings us closer to understanding.','每一次观测，都让我们更接近答案。')]):
    rid=w.history.add(sid,en,n*5000,(n+1)*5000,status='done');w.history.update(rid,'done',zh)
w.refresh_sessions();w.last_partial='An experiment begins with a question.';w.capturing=True
w.render_now(force=True);settle()
live=copy.deepcopy(w.settings);desktop=copy.deepcopy(w.desktop.options)
for index,(name,colors) in enumerate(PRESETS.items()):
    w.apply_colors(colors);settle();w.grab().save(str(artifacts/f'workspace-{index+1}.png'))
assert w.settings==live and w.desktop.options==desktop
w.capturing=False

# Real Qt signal path, including asynchronous dispatch and duplicate suppression.
w.settings['theme']={'follow_system':True,'family':'neutral'}
qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light);settle();w.apply_display()
calls=[];set_style=qt.setStyleSheet
qt.setStyleSheet=lambda value:(calls.append(value),set_style(value))[-1]
qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark);settle()
assert w.colors==PRESETS['墨夜'] and len(calls)==1
qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark);settle()
assert len(calls)==1 and not w.system_theme.timer.isActive()
assert w.settings['colors']==live['colors']
w.settings['theme']['follow_system']=False;w.apply_display();calls.clear()
qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light);settle()
assert not calls and w.colors==live['colors']
qt.setStyleSheet=set_style

errors=[]
def edit_and_cancel():
    try:
        d=w.dialog;e=d.theme_editor;d.tabs.setCurrentWidget(e);settle()
        e.cards['暖岩'].setFocus();QTest.keyClick(e.cards['暖岩'],Qt.Key.Key_Space);settle()
        assert w.colors==PRESETS['暖岩'] and not e.follow.isChecked()
        e.follow.setChecked(True);assert w.colors==PRESETS['米纸']
        qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark);settle()
        assert w.colors==PRESETS['暖岩'] and e.validated_colors()==PRESETS['暖岩']
        e.follow.setChecked(False);assert w.colors==PRESETS['暖岩']
        e.change_color('english','#ecdecb');custom=dict(e.colors)
        e.save_palette('我的阅读配色');e.follow.setChecked(True)
        qt.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light);settle()
        assert w.colors==PRESETS['米纸'] and e.validated_colors()==custom
        path=test_root/'export.json';export_configuration(d.draft(),path);loaded=import_configuration(path)
        assert loaded['theme']=={'follow_system':True,'family':'warm'} and loaded['colors']==custom
        assert loaded['saved_themes'][0]['colors']==custom
        d.load_draft(loaded);e.follow.setChecked(False);assert w.colors==custom
        # Invalid color drafts stay invalid when another color is edited.
        e.fields['text'].hex.setText('#ab');e.fields['english'].hex.setText('#ffffff')
        try:e.validated_colors();raise AssertionError('Invalid draft accepted')
        except ValueError:pass
        e.choose_preset('雾白')
        d.resize(1000,740);e.scroll.verticalScrollBar().setValue(0);settle()
        d.grab().save(str(artifacts/'theme-settings.png'))
        d.resize(860,620);settle()
        assert e.scroll.horizontalScrollBar().maximum()==0
        for card in e.cards.values():assert card.width()>=100 and card.isVisible()
        d.grab().save(str(artifacts/'theme-settings-small.png'))
        e.change_color('english','#aabbcc') # queued preview must not escape cancellation
    except BaseException as error:errors.append(repr(error))
    finally:w.dialog.reject()
QTimer.singleShot(10,edit_and_cancel);w.open_settings();settle(250)
assert not errors,errors
assert w.colors==live['colors'] and w.settings['theme']['follow_system'] is False
assert not w.findChildren(QDialog)

# Saved automatic preferences survive restart; legacy colors stay manual.
new=copy.deepcopy(live);new['theme']={'follow_system':True,'family':'warm'};save_settings(new)
loaded=load_settings();assert loaded['theme']==new['theme'] and loaded['colors']==new['colors']
legacy=copy.deepcopy(live);legacy.pop('theme');(test_root/'data'/'settings.json').write_text(json.dumps(legacy),'utf-8')
assert load_settings()['colors']==legacy['colors'] and not load_settings()['theme']['follow_system']
for bad in ({'follow_system':'yes'},{'family':[]},{'family':'missing'}):
    try:validate_configuration(dict(new,theme=bad));raise AssertionError('Invalid theme accepted')
    except ValueError:pass
w.close();settle(400);temporary.cleanup()
print(json.dumps({'ok':True,'minimum_text_contrast':{k:round(v,2) for k,v in ratios.items()},
    'event_driven_switch':True,'duplicate_event_no_repaint':True,'manual_ignores_system':True,
    'custom_colors_restored':True,'cancel_restores':True,'config_roundtrip':True,'legacy_preserved':True},ensure_ascii=False))
