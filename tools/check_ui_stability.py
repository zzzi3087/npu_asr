"""Offline settings lifecycle/responsiveness regression; no NPU, microphone or cloud calls."""
import os,sys,json,time,copy,statistics,argparse
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[1]
temporary=TemporaryDirectory(prefix='parakeet-ui-check-');test_root=Path(temporary.name)
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
artifacts=args.artifacts or test_root
artifacts.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(test_root/'data')
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt,QTimer,QEventLoop,QCoreApplication,QEvent,QPoint,QPointF
from PySide6.QtGui import QWheelEvent,QFontDatabase
from PySide6.QtWidgets import QApplication,QDialog,QScrollArea
from storage import DATA,default_settings,load_settings,save_settings
import settings_dialog
from app import Window
from qwen_tokenizer import load_tokenizer
settings_dialog.get_key=lambda p:(_ for _ in ()).throw(AssertionError('Opening settings must not read credentials'))
qt=QApplication([])
for font_path in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf'):
    if Path(font_path).exists():QFontDatabase.addApplicationFont(font_path)
DATA.mkdir(parents=True);s=default_settings();s['translate']=False;save_settings(s)

def settle(ms=30):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

w=Window(start_workers=False);w.show();settle()
sid=w.history.create_session('Synthetic UI responsiveness fixture',w.profile());w.current_session=w.view_session=sid
for n in range(100):
    rid=w.history.add(sid,'The experiment searches for dark matter and axions in the universe. '*3,n*1000,(n+1)*1000,status='done')
    w.history.update(rid,'done','实验探索暗物质、轴子与宇宙的起源。'*3)
w.render_now();settle()
load_tokenizer.cache_clear();start=time.perf_counter();d=settings_dialog.SettingsDialog(w.settings,w);d.show();qt.processEvents()
open_ms=(time.perf_counter()-start)*1000
assert load_tokenizer.cache_info().currsize==0
for i in range(d.tabs.count()):
    d.tabs.setCurrentIndex(i);qt.processEvents()
    assert d.tabs.currentIndex()==i
    if i in (0,1,2,7,8):d.grab().save(str(artifacts/f'settings-page-{i}.png'))
d.resize(860,620);d.tabs.setCurrentIndex(1);qt.processEvents();d.grab().save(str(artifacts/'settings-small.png'))
assert d.tabs.width()>600 and d.tabs.navigation.width()>=150
d.tabs.search.setText('字号');qt.processEvents()
names=[d.tabs.navigation.item(i).text() for i in range(d.tabs.navigation.count())]
assert '桌面字幕' in names and '工作台字体' in names and '翻译服务' not in names
d.tabs.search.setText('not-a-setting');assert d.tabs.no_results.isVisible();d.tabs.search.clear()
d.tabs.setCurrentWidget(d.typography_editor);qt.processEvents()
field=d.typography_editor.fields['english_size'];d.tabs.search.setFocus();qt.processEvents()
scroll=field.parentWidget()
while not isinstance(scroll,QScrollArea):scroll=scroll.parentWidget()
scroll.verticalScrollBar().setValue(0);before=field.value()
wheel=QWheelEvent(QPointF(5,5),QPointF(field.mapToGlobal(QPoint(5,5))),QPoint(),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
qt.sendEvent(field,wheel)
assert field.value()==before and scroll.verticalScrollBar().value()>0
d.reject();d.deleteLater();settle()
assert not w.findChildren(QDialog)
invalid=settings_dialog.SettingsDialog(w.settings,w)
bad_id=invalid.current['id'];invalid.extra.setPlainText('{broken JSON')
invalid.new_profile();invalid.save()
assert invalid.save_job is None and invalid.tabs.currentIndex()==4 and invalid.profile.currentData()==bad_id
invalid.reject();invalid.deleteLater();settle()

style_calls=[];original_style=qt.setStyleSheet
qt.setStyleSheet=lambda text:(style_calls.append(len(text)),original_style(text))[-1]
timings=[]
for n in range(6):
    appearance=copy.deepcopy(w.appearance);appearance['english_size']=30+n
    start=time.perf_counter();w.preview_typography(appearance);qt.processEvents();timings.append((time.perf_counter()-start)*1000)
assert not style_calls,'Subtitle font-size changes must not reapply application CSS'
revision=w.browser.document().revision();w.render_now();qt.processEvents()
assert w.browser.document().revision()==revision
qt.setStyleSheet=original_style
errors=[];retained=[];page_times=[]
for n in range(16):
    def exercise(n=n):
        try:
            dialog=w.dialog;start=time.perf_counter()
            for index in range(dialog.tabs.count()):dialog.tabs.setCurrentIndex(index);qt.processEvents()
            page_times.append((time.perf_counter()-start)*1000/dialog.tabs.count())
            if n==0:
                old_revision=w.browser.document().revision()
                w.on_event('committed',{'text':'New sentence while editing settings.','audio_start_ms':101000,'audio_end_ms':102000})
                w.render_now();assert w.workbench_dirty and w.browser.document().revision()==old_revision
            dialog.typography_editor.fields['english_size'].setValue(45)
            dialog.theme_editor.change_color('english','#ef1234')
            dialog.desktop_editor.fields['source_size'].setValue(42)
            dialog.reject()
        except BaseException as e:errors.append(repr(e));w.dialog.reject()
    QTimer.singleShot(1,exercise);w.open_settings();settle(5)
    retained.append(len(w.findChildren(QDialog)))
    assert retained[-1]==0,retained
settle(250);assert not errors,errors
assert w.appearance['english_size']==w.settings['english_size'] and w.colors==w.settings['colors']
assert not w.desktop.preview

# A slow disk or credential backend must leave the Qt event loop responsive.
real_save=settings_dialog.save_settings
heartbeats=[];timer=QTimer();timer.setInterval(15);timer.timeout.connect(lambda:heartbeats.append(time.perf_counter()))
def slow_save(value):time.sleep(.3);real_save(value)
settings_dialog.save_settings=slow_save
def start_save():
    dialog=w.dialog;dialog.typography_editor.fields['english_size'].setValue(29)
    dialog.save();assert dialog.save_job.isRunning() and not dialog.buttons.isEnabled()
    dialog.reject();assert dialog.isVisible()
timer.start();QTimer.singleShot(10,start_save);w.open_settings();timer.stop();settle()
assert len(heartbeats)>=8,len(heartbeats)
assert load_settings()['english_size']==29 and w.settings['english_size']==29
assert not w.findChildren(QDialog)

def failing_save(value):raise OSError('fixture')
settings_dialog.save_settings=failing_save
def start_failure():
    dialog=w.dialog
    def failed():
        assert dialog.isVisible() and dialog.buttons.isEnabled() and dialog.save_job.error
        dialog.reject()
    dialog.save();dialog.save_job.finished.connect(failed)
QTimer.singleShot(10,start_failure);w.open_settings();settle()
settings_dialog.save_settings=real_save
assert load_settings()['english_size']==29 and not w.findChildren(QDialog)
w.close();settle(500)
summary=dict(first_settings_ms=round(open_ms,2),subtitle_size_preview_ms=[round(t,2) for t in timings],
    median_preview_ms=round(statistics.median(timings),2),reopen_cycles=len(retained),retained_dialogs=retained,
    mean_page_switch_ms=round(statistics.mean(page_times),2),no_style_reapply_for_caption_size=True,
    unchanged_document_skips_layout=True,cold_tokenizer_not_loaded=True,searchable_categories=True,
    wheel_does_not_change_unfocused_controls=True,pending_preview_cancelled=True,async_save_heartbeats=len(heartbeats),
    failed_save_recovers=True)
summary.update(invalid_json_in_another_profile_is_reported=True,workbench_layout_deferred_while_settings_open=True)
(artifacts/'ui-stability-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
temporary.cleanup()
