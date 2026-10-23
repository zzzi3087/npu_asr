"""Isolated history CRUD, source switching and compact workspace regression."""
import os,sys,json,argparse,time,copy
from pathlib import Path
from tempfile import TemporaryDirectory
temporary=TemporaryDirectory(prefix='captions-layout-');root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
output=args.artifacts or Path(temporary.name);output.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(temporary.name)/'data')
sys.path.insert(0,str(root/'src'));sys.path.insert(0,str(root/'tools'))
from PySide6.QtCore import Qt,QTimer,QEventLoop,QCoreApplication,QEvent,QPoint
from PySide6.QtWidgets import QApplication,QFrame,QPushButton,QMessageBox,QInputDialog,QDialog
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from storage import DATA,default_settings,load_settings,save_settings
from translation import Translator
from history_row import HistoryRow
from app import Window
qt=QApplication([])
for path in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf'):
    if Path(path).exists():QFontDatabase.addApplicationFont(path)

def settle(ms=25):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

# Old layouts migrate once; an intentional later height survives restart.
DATA.mkdir(parents=True)
legacy=default_settings();legacy.pop('main_layout_version');legacy['typography']['preview_height']=72
(DATA/'settings.json').write_text(json.dumps(legacy),'utf-8')
assert load_settings()['typography']['preview_height']==40
s=load_settings();s['typography']['preview_height']=90;save_settings(s)
assert load_settings()['typography']['preview_height']==90
s=default_settings();s['translate']=False;save_settings(s)
w=Window(start_workers=False);w.show();settle()
assert w.settings_button.menu() is None
assert not w.findChild(QFrame,'toolbar')
assert not any(b.text() in ('A−','A＋') for b in w.findChildren(QPushButton))
assert w.partial.height()==40
assert w.retry_button.mapTo(w,QPoint()).x()<w.width()/3
assert w.partial.mapTo(w,QPoint()).y()>w.height()*.7

# Quick source menu remembers explicit devices, persists, and leaves active capture alone.
w.settings['audio_source']={'kind':'microphone','device':'Fixture microphone'};w.refresh_source_summary()
w.populate_source_menu();assert [a.text() for a in w.source_menu.actions()][:2]==['麦克风','桌面音频']
w.source_menu.actions()[1].trigger();assert load_settings()['audio_source']['kind']=='desktop'
w.select_audio_source('microphone');assert w.settings['audio_source']['device']=='Fixture microphone'
w.capturing=True;w.active_source=dict(w.settings['audio_source']);w.select_audio_source('desktop')
assert w.capturing and '麦克风' in w.source_summary.text() and '下次' in w.source_summary.toolTip()
w.capturing=False;w.refresh_source_summary();assert '桌面音频' in w.source_summary.text()

# Settings click enters the dialog directly, including the source menu's device shortcut.
errors=[];opened=[]
def cancel_settings():
    try:assert w.dialog.isVisible();opened.append(w.dialog.tabs.currentIndex());w.dialog.reject()
    except BaseException as error:errors.append(repr(error));w.dialog.reject()
QTimer.singleShot(10,cancel_settings);w.settings_button.click();settle()
assert not errors and len(opened)==1
w.populate_source_menu();QTimer.singleShot(10,cancel_settings);w.source_menu.actions()[-1].trigger();settle()
assert opened[-1]==1 and not errors

ids=[w.history.create_session('Synthetic history '+str(n),w.profile()) for n in range(15)]
for sid in ids:w.history.add(sid,'Isolated fixture.',0,1000,status='disabled')
w.view_session=ids[0];w.current_session=ids[0];w.refresh_sessions();w.render_now();settle()
assert len(w.history_rows)==15
row=w.history_rows[ids[1]][1]
QTest.mouseClick(row,Qt.MouseButton.LeftButton,pos=QPoint(12,14));settle();assert w.view_session==ids[1]
reuse={sid:id(pair[1]) for sid,pair in w.history_rows.items()}
start=time.perf_counter()
for _ in range(30):w.refresh_sessions()
refresh_ms=(time.perf_counter()-start)*1000/30
assert {sid:id(pair[1]) for sid,pair in w.history_rows.items()}==reuse
assert len(w.findChildren(HistoryRow))==15
w.history.add(ids[0],'A second confirmed sentence.',1000,2000,status='disabled');w.refresh_sessions()
assert id(w.history_rows[ids[0]][1])==reuse[ids[0]] and '2 段' in w.history_rows[ids[0]][1].detail.text()

# Operate on the menu's target, independently of the record displayed on the right.
QInputDialog.getText=lambda *a,**kw:('Renamed target',True)
w.open_history_menu(ids[2],w.history_rows[ids[2]][1].menu_button)
assert [a.text() for a in w.history_menu.actions()]==['重命名','删除']
w.history_menu.close();w.history_rename.trigger();settle()
assert w.history.session(ids[2])['title']=='Renamed target' and w.view_session==ids[1]
QMessageBox.question=lambda *a,**kw:QMessageBox.StandardButton.Cancel
w.delete_session(ids[2]);assert w.history.session(ids[2])
w.capturing=True;w.current_session=ids[2]
w.open_history_menu(ids[2],w.history_rows[ids[2]][1].menu_button)
assert not w.history_delete.isEnabled();w.history_menu.close()
w.delete_session(ids[2]);assert w.history.session(ids[2]);w.capturing=False
QMessageBox.question=lambda *a,**kw:QMessageBox.StandardButton.Yes
deleted_segment=w.history.rows(ids[2])[0]['id'];w.active_jobs.add(deleted_segment)
w.delete_session(ids[2]);settle()
assert not w.history.session(ids[2]) and not w.history.segment(deleted_segment)
real_pump=w.pump;pumped=[];w.pump=lambda sid:pumped.append(sid)
w.on_translation(ids[2],deleted_segment,'Late reply',True)
assert deleted_segment not in w.active_jobs and w.history.session(ids[1]) and ids[1] in pumped
w.pump=real_pump
w.current_session=w.view_session=ids[1];w.delete_session(ids[1]);w.render_now();settle()
assert w.view_session is None and w.current_session is None and not w.retry_button.isEnabled()
w.capturing=True;w.current_session=ids[4];w.view_session=ids[3]
w.delete_session(ids[3]);assert w.capturing and w.view_session==w.current_session==ids[4]
w.capturing=False

# Targeted queue cancellation preserves unrelated records, order and shutdown marker.
t=Translator()
for sid in ('keep','delete','keep','delete'):t.enqueue(sid,sid,'fixture',[],{},'')
t.jobs.put_nowait(None)
removed=t.cancel_queued(session='delete');assert len(removed)==2
assert [t.jobs.get_nowait()[0] for _ in range(2)]==['keep','keep']
assert t.jobs.get_nowait() is None

# Both supported main-window sizes keep controls and row menus inside the workspace.
w.view_session=ids[0];w.refresh_sessions();w.render_now()
header_heights={}
for width,height,name in ((1220,830,'workspace'),(860,620,'workspace-small')):
    w.resize(width,height);settle();assert w.width()==width,(width,w.width())
    header_heights[str(width)]=w.header.height()
    assert w.header.narrow==(width==860)
    assert w.header.height()<(175 if width==860 else 125)
    assert w.footer.height()<=24
    for widget in (w.settings_button,w.source_summary,w.start,w.desktop_button):
        pos=widget.mapTo(w,QPoint());assert pos.x()>=0 and pos.x()+widget.width()<=w.width()
    w.grab().save(str(output/(name+'.png')))
w.notify('布局检查通知');settle();assert w.notice.isVisible() and not w.provider_label.isVisible()
footer_height=w.footer.height();w.clear_notice();settle()
assert w.provider_label.isVisible() and not w.notice.isVisible() and w.footer.height()==footer_height
w.close();settle(400)
assert not w.translator.isRunning() and not w.findChildren(QDialog)
summary=dict(direct_settings=True,per_record_menu=True,rename_and_delete_target_correct=True,
    delete_cancel_and_active_capture_guard=True,late_translation_ignored=True,targeted_queue_cancel=True,
    source_switch_and_persistence=True,active_source_preserved=True,compact_preview=True,
    preview_migration_preserves_later_custom_height=True,history_widgets_reused=True,
    average_history_refresh_ms=round(refresh_ms,2),small_window_fits=True,clean_shutdown=True)
summary.update(compact_header_heights=header_heights,inline_footer_notifications=True)
(output/'main-layout-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
temporary.cleanup()
