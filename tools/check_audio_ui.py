"""Offline source selection, flat navigation, display switch and live overlay tests."""
import os,sys,json,time,argparse
from pathlib import Path
from tempfile import TemporaryDirectory
root=Path(__file__).resolve().parents[1];temp=TemporaryDirectory(prefix='live-captions-ui-')
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
output=args.artifacts or Path(temp.name);output.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(temp.name)/'data')
sys.path.insert(0,str(root))
from PySide6.QtCore import Qt,QTimer,QEventLoop,QCoreApplication,QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QComboBox,QDialog
from storage import DATA,default_settings,save_settings,load_settings
from config_io import export_configuration,import_configuration,validate_configuration
from audio_sources import normalize_audio_source
from switch_button import SwitchButton
from app import Window
qt=QApplication([])
for f in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf'):QFontDatabase.addApplicationFont(f)
def settle(ms=50):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
DATA.mkdir(parents=True);s=default_settings();s['translate']=False;save_settings(s)
for bad in ({'kind':'camera'},{'kind':'desktop','device':3}):
    try:normalize_audio_source(bad,strict=True);raise AssertionError(bad)
    except ValueError:pass
legacy=dict(s);legacy.pop('audio_source');legacy['microphone_name']='Fixture · WASAPI'
(DATA/'settings.json').write_text(json.dumps(legacy),'utf-8')
assert load_settings()['audio_source']=={'kind':'microphone','device':'Fixture · WASAPI'}
assert validate_configuration(legacy)['audio_source']['device']=='Fixture · WASAPI'
save_settings(s)
w=Window(start_workers=False);w.show();settle()
assert w.windowTitle()=='实时字幕' and isinstance(w.desktop_button,SwitchButton)
assert not w.findChildren(QComboBox),'Devices belong in settings; the top source switch uses a small menu'
assert w.source_summary.menu() is w.source_menu
assert w.settings_button.menu() is None
assert not hasattr(w.desktop_button,'menu')
sid=w.history.create_session('fixture',w.profile());w.current_session=w.view_session=sid
rid=w.history.add(sid,'Confirmed English sentence.',0,1000,status='done');w.history.update(rid,'done','已经确认的中文译文。')
w.capturing=True;w.render_now();settle();base_height=w.desktop.height()
w.on_event('partial',{'text':'This new sentence is still being recognized.'});settle()
assert w.desktop.partial.startswith('This new sentence') and w.desktop.live_active
assert w.desktop.height()>=base_height
revision=w.desktop.rebuild_count
w.on_event('partial',{'text':'This new sentence is still being recognized.'});settle()
assert w.desktop.rebuild_count==revision
w.desktop._image.save(str(output/'live-preview-overlay.png'))
w.showMinimized();w.on_event('partial',{'text':'Preview continues while the workbench is minimized.'});settle()
assert w.desktop.isVisible() and w.desktop.partial.startswith('Preview continues')
w.showNormal();w.capturing=False;w.render_now();settle();assert not w.desktop.live_active
w.desktop_button.click();settle();assert not w.desktop.options['enabled'] and not w.desktop.isVisible()
w.desktop_button.click();settle();assert w.desktop.options['enabled'] and w.desktop.isVisible()
w.grab().save(str(output/'main.png'))
errors=[]
def edit_cancel():
    try:
        d=w.dialog;nav=d.tabs.navigation
        names=[nav.item(i).text() for i in range(nav.count())]
        assert len(names)==len(set(names))==d.tabs.count()==8,names
        assert '声音与语言' not in names and '词表与数据' not in names and '音量增益' not in names
        assert '收音设置' in names
        for i in range(nav.count()):
            assert nav.item(i).flags() & Qt.ItemFlag.ItemIsEnabled
            nav.setCurrentRow(i);assert d.tabs.currentIndex()==nav.item(i).data(Qt.ItemDataRole.UserRole)
        assert d.tabs.widget(1).isAncestorOf(d.agc_fields['manual_db'])
        assert d.tabs.widget(1).isAncestorOf(d.audio_source_editor)
        d.audio_source_editor.kind.setCurrentIndex(d.audio_source_editor.kind.findData('desktop'))
        assert d.draft()['audio_source']['kind']=='desktop'
        d.tabs.setCurrentIndex(1);d.grab().save(str(output/'recording-settings.png'))
        d.desktop_editor.fields['preview_enabled'].setChecked(False);d.reject()
    except BaseException as e:errors.append(repr(e));w.dialog.reject()
QTimer.singleShot(10,edit_cancel);w.open_settings();settle(250)
assert not errors,errors
assert w.settings['audio_source']['kind']=='microphone' and w.desktop.options['preview_enabled']
def edit_save():
    try:
        d=w.dialog;d.audio_source_editor.set_values({'kind':'desktop','device':''});d.save()
    except BaseException as e:errors.append(repr(e));w.dialog.reject()
QTimer.singleShot(10,edit_save);w.open_settings();settle()
assert not errors,errors
assert load_settings()['audio_source']['kind']=='desktop' and '桌面音频' in w.source_summary.text()
export_configuration(w.settings,output/'config.json');reloaded=import_configuration(output/'config.json')
assert reloaded['audio_source']==w.settings['audio_source']
assert reloaded['desktop_captions']['preview_enabled']
assert not w.findChildren(QDialog)
w.close();settle(500);assert not w.translator.isRunning() and not w.desktop.isVisible()
summary=dict(flat_clickable_unique_pages=True,merged_gain_and_source_settings=True,compact_source_menu_on_top=True,
    desktop_switch_only=True,renamed_to_live_captions=True,live_preview_line=True,same_preview_skips_redraw=True,
    preview_when_minimized=True,source_cancel_save_and_export=True,legacy_microphone_migrated=True,clean_shutdown=True)
(output/'audio-ui-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True);temp.cleanup()
