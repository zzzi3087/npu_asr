"""Offline Qt integration check. Run with the app Python; no NPU, microphone or cloud calls.
Uses isolated temporary settings/history and removes fixtures on exit.
"""
import os,sys,json,time,uuid,copy
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[1]
TEST_TEMP=TemporaryDirectory(prefix="parakeet-desktop-captions-")
TEST_ROOT=Path(TEST_TEMP.name)
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['PARAKEET_DATA_DIR']=str(TEST_ROOT/'data')
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt,QTimer,QEventLoop,QPointF
from PySide6.QtGui import QFontDatabase,QFont,QImage,QPainter,QColor,QMouseEvent
from PySide6.QtWidgets import QApplication
from desktop_caption_config import default_desktop_captions,normalize_desktop_captions
from desktop_captions import DesktopCaptions,SAMPLE_PAIRS
from storage import DATA,default_settings,load_settings,save_settings
from config_io import export_configuration,import_configuration,validate_configuration
import settings_dialog
from app import Window
settings_dialog.get_key=lambda p:''
qt=QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
qt.setFont(QFont('Microsoft YaHei UI',10))
DATA.mkdir(parents=True)

def settle(ms=30):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()

base=default_desktop_captions()
assert base['sentence_count']==2 and base['source_color']==base['translation_color']=='#ffffff'
assert base['outline_color']=='#000000' and base['locked']
for bad in [dict(source_size=0),dict(source_color='red'),dict(locked=1),dict(width=400.5),dict(line_height=float('nan'))]:
    try:normalize_desktop_captions(bad,strict=True);raise AssertionError(bad)
    except ValueError:pass
assert normalize_desktop_captions({'source_size':0})['source_size']==36
custom=dict(base,source_font='Arial',source_size=42,translation_size=30,source_color='#ffeeaa',translation_color='#aaffcc',x=24,bottom=650)
settings=default_settings();settings['translate']=False;settings['desktop_captions']=custom
save_settings(settings)
assert load_settings()['desktop_captions']==custom
export_configuration(settings,DATA/'config.json')
assert import_configuration(DATA/'config.json')['desktop_captions']==custom
assert validate_configuration({})['desktop_captions']==base
print('PASS settings validation, legacy defaults, storage/config roundtrip',flush=True)

overlay=DesktopCaptions(base);settle()
assert not overlay.isVisible()
overlay.set_pairs(SAMPLE_PAIRS);settle()
assert overlay.isVisible() and overlay.parent() is None
assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
im=overlay._image
assert im.pixelColor(0,0).alpha()==0
opaque=white=black=0
for y in range(im.height()):
    for x in range(im.width()):
        c=im.pixelColor(x,y)
        if c.alpha()>240:
            opaque+=1
            white+=int(c.red()>245 and c.green()>245 and c.blue()>245)
            black+=int(c.red()<10 and c.green()<10 and c.blue()<10)
assert white>500 and black>500 and opaque<im.width()*im.height()*.65,(white,black,opaque)
im.save(str(TEST_ROOT/'desktop-overlay-transparent.png'))
composite=QImage(im.size(),QImage.Format.Format_RGB32);composite.fill(QColor('#395a7d'))
p=QPainter(composite);p.drawImage(0,0,im);p.end();composite.save(str(TEST_ROOT/'desktop-overlay-preview.png'))
for source in ['😀 𝛼 axions '+('a very long physics caption '*100),'暗物质 轴子 中微子 '*70,'مرحبا هذا نص عربي '+('اختبار '*70)]:
    overlay.set_pairs([(source,'理论物理与天体粒子物理。'*50)]);settle()
    assert not overlay._image.isNull()
    assert overlay.height()<=qt.primaryScreen().availableGeometry().height()-32
assert len(overlay._glyphs)<=1024
overlay.set_pairs(SAMPLE_PAIRS);settle()
height=overlay.height()
overlay.configure(dict(base,source_size=56,translation_size=48));settle()
assert overlay.height()>height
overlay.configure(dict(base,locked=False));settle()
assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
positions=[];overlay.position_changed.connect(positions.append)
pt=QPointF(20,20);global_point=QPointF(overlay.mapToGlobal(pt.toPoint()))
overlay.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress,pt,global_point,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
overlay.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove,pt,global_point+QPointF(20,-30),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
overlay.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease,pt,global_point+QPointF(20,-30),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
settle();assert positions and positions[0]['x']==overlay.options['x']
assert qt.primaryScreen().availableGeometry().contains(overlay.geometry())
overlay.configure(dict(base,x=-50000,bottom=50000));settle()
assert qt.primaryScreen().availableGeometry().contains(overlay.geometry())
overlay.configure(base);overlay.set_pairs(SAMPLE_PAIRS);settle(300)
before=(overlay.rebuild_count,overlay.paint_count)
t0=time.perf_counter()
for _ in range(10000):overlay.set_pairs(SAMPLE_PAIRS)
repeat_ms=(time.perf_counter()-t0)*1000
settle();assert (overlay.rebuild_count,overlay.paint_count)==before
cpu0=time.process_time();wall0=time.perf_counter();settle(10000)
idle_wall=time.perf_counter()-wall0;idle_cpu=time.process_time()-cpu0
assert (overlay.rebuild_count,overlay.paint_count)==before
overlay.configure(dict(base,enabled=False));settle();assert not overlay.isVisible()
overlay.shutdown()
print('PASS transparent pixels/outline, Unicode wrapping, font size, input flags, drag/clamp, no idle redraw',flush=True)

settings=default_settings();settings['translate']=False;save_settings(settings)
w=Window(start_workers=False);w.show()
sid=w.history.create_session('overlay fixture',w.profile());w.current_session=w.view_session=sid
ids=[]
for n in range(3):
    ident=w.history.add(sid,f'Physics sentence {n+1}.',n*1000,(n+1)*1000,status='done')
    w.history.update(ident,'done',f'物理学句子 {n+1}。');ids.append(ident)
w.render_now();settle(200)
assert w.desktop.pairs==tuple((f'Physics sentence {n}.',f'物理学句子 {n}。') for n in (2,3))
count=w.desktop.rebuild_count
w.on_event('partial',{'text':'Unstable partial text'})
w.on_event('level',20);settle()
assert w.desktop.rebuild_count==count
other=w.history.create_session('older fixture',w.profile())
w.history.add(other,'Older unrelated history.',0,1000,status='disabled')
w.view_session=other;w.capturing=True;w.render_now();settle()
assert w.desktop.pairs[-1][0]=='Physics sentence 3.'
w.on_translation(sid,ids[-1],'更新的第三句译文。',True);settle()
assert w.desktop.pairs[-1]==('Physics sentence 3.','更新的第三句译文。')
count=w.desktop.rebuild_count
w.on_translation(sid,ids[0],'迟到的第一句译文。',True);settle()
assert w.desktop.rebuild_count==count
w.view_session=sid;w.render_now();settle()
before_html=w.browser.toPlainText()
w.showMinimized();settle()
ident=w.history.add(sid,'Newest sentence while minimized.',3000,4000,status='done')
w.history.update(ident,'done','最小化时的新字幕。')
w.render_now();settle()
assert w.workbench_dirty and w.browser.toPlainText()==before_html
assert w.desktop.pairs[-1]==('Newest sentence while minimized.','最小化时的新字幕。')
w.showNormal();settle(200)
assert not w.workbench_dirty and 'Newest sentence while minimized.' in w.browser.toPlainText()
w.hide();settle();assert w.desktop.isVisible()
w.show();w.capturing=False;w.view_session=sid
errors=[]
def cancel_draft():
    try:
        d=w.dialog;assert d.tabs.currentIndex()==d.desktop_tab
        d.desktop_editor.set_values(custom,emit=True);settle()
        assert w.desktop.options['source_color']=='#ffeeaa'
        d.desktop_editor.fields['source_color'].setText('broken')
        d.desktop_editor.restore_style();settle()
        assert d.desktop_editor.values()['source_color']=='#ffffff'
        d.desktop_editor.fields['source_size'].setValue(44)
        assert d.desktop_editor.timer.isActive()
        d.reject()
    except BaseException as e:errors.append(repr(e));w.dialog.reject()
QTimer.singleShot(30,cancel_draft);w.open_settings(page='desktop');settle(200)
assert not errors,errors
assert w.desktop.options==base and not w.desktop.preview
assert load_settings()['desktop_captions']==base
def save_draft():
    try:
        d=w.dialog;d.desktop_editor.set_values(custom,emit=True);settle()
        d.grab().save(str(TEST_ROOT/'desktop-overlay-settings.png'))
        d.desktop_editor.schedule()
        d.save()
    except BaseException as e:errors.append(repr(e));w.dialog.reject()
QTimer.singleShot(30,save_draft);w.open_settings(page='desktop');settle(200)
assert not errors,errors
assert load_settings()['desktop_captions']==custom and not w.desktop.preview
w.change_desktop(enabled=False);settle();assert not w.desktop.isVisible() and not w.desktop_button.isChecked()
w.change_desktop(enabled=True);settle();assert w.desktop.isVisible()
w.desktop_position_changed(dict(x=123,bottom=555,screen=''))
assert load_settings()['desktop_captions']['x']==123
w.change_desktop(x=None,bottom=None,screen='');settle()
assert load_settings()['desktop_captions']['x'] is None
w.grab().save(str(TEST_ROOT/'desktop-overlay-main.png'))
w.close();settle(500)
assert not w.desktop.isVisible() and w.desktop._shutdown and not w.translator.isRunning()
summary=dict(config_roundtrip=True,transparent_background=True,white_fill_pixels=white,black_outline_pixels=black,
    unicode_wrap=True,latest_two_bilingual_pairs=True,late_translation_in_place=True,live_while_browsing_history=True,
    partial_does_not_redraw=True,draft_cancel_and_save=True,mouse_through_and_drag=True,main_hide_independent=True,minimized_workbench_defers_layout=True,
    idle_wall_seconds=round(idle_wall,3),idle_process_cpu_seconds=round(idle_cpu,6),idle_extra_rebuilds=0,idle_extra_paints=0,
    identical_updates_10000_ms=round(repeat_ms,3),glyph_cache_limit=1024,shutdown=True)
(TEST_ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

TEST_TEMP.cleanup()
