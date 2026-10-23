"""Tray lifecycle and real local-IPC checks, isolated from microphone and user data."""
import json,os,sys,threading,uuid,time,subprocess,queue
from pathlib import Path
from tempfile import TemporaryDirectory
folder=TemporaryDirectory(prefix='captions-tray-')
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(folder.name)/'data')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from PySide6.QtCore import QObject,Signal,QTimer,QEventLoop,Qt
from PySide6.QtWidgets import QApplication,QSystemTrayIcon
from storage import default_settings,save_settings,load_settings
from config_io import export_configuration,import_configuration
from tray_controller import TrayController
from instance_activation import InstanceActivation,activate_existing
import settings_dialog
settings_dialog.get_key=lambda p:''
from app import Window

class FakeIcon(QObject):
    activated=Signal(object)
    @staticmethod
    def isSystemTrayAvailable():return True
    def __init__(self,icon,parent):super().__init__(parent);self.visible=False;self.tooltip=''
    def setContextMenu(self,menu):self.menu=menu
    def setToolTip(self,text):self.tooltip=text
    def show(self):self.visible=True
    def hide(self):self.visible=False

class CaptureWorker:
    def __init__(self):
        self.stop_capture=threading.Event();self.quit_requested=threading.Event();self.download_cancel=threading.Event()
        self.commands=queue.Queue();self.engine_key='fixture'
    def isRunning(self):return False
    def update_gain(self,value):pass

qt=QApplication([])
def settle(ms=45):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
settings=default_settings();settings['translate']=False;save_settings(settings)
w=Window(start_workers=False);w.tray.shutdown();w.tray=TrayController(w,FakeIcon);w.show();settle()
try:
    sid=w.history.create_session('tray fixture',w.profile());w.current_session=w.view_session=sid
    w.worker=CaptureWorker();w.capturing=True;w.start.setText('停止收音');w.sync_tray();w.render_now();settle()
    before=w.browser.toPlainText();translator=w.translator
    w.close();settle()
    assert not w.isVisible() and w._in_tray and not w.closing
    assert translator.isRunning() and w.capturing and w.desktop.options['enabled']
    assert not w.worker.stop_capture.is_set() and not w.worker.quit_requested.is_set()
    w.on_event('committed',{'text':'A neutrino oscillates.','audio_start_ms':0,'audio_end_ms':1000})
    w.on_event('partial',{'text':'Dark matter scatters.'});settle(180)
    assert w.workbench_dirty and w.browser.toPlainText()==before
    assert w.history.rows(sid)[-1]['en']=='A neutrino oscillates.'
    assert w.desktop.pairs[-1][0]=='A neutrino oscillates.' and w.desktop.isVisible()
    assert w.desktop.partial=='Dark matter scatters.'
    assert w.history.session(sid)['ended_at'] is None
    # Actual local pipe, not a mocked single-instance activation callback.
    server=InstanceActivation(w,'captions-test-'+uuid.uuid4().hex);assert server.listening
    child=subprocess.Popen([sys.executable,'-c','import sys;from instance_activation import activate_existing;sys.exit(0 if activate_existing(sys.argv[1]) else 1)',server.name],
        cwd=Path(__file__).resolve().parents[1]/'src',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    deadline=time.monotonic()+8
    while child.poll() is None and time.monotonic()<deadline:settle(25)
    assert child.poll()==0,child.communicate(timeout=1)
    settle(150)
    assert w.isVisible() and not w._in_tray
    assert 'A neutrino oscillates.' in w.browser.toPlainText() and not w.workbench_dirty
    server.close()
    w.tray.activated(QSystemTrayIcon.ActivationReason.Trigger);assert w.isVisible()
    w.showMinimized();settle();assert not w.isVisible() and w._in_tray
    w.tray.show_action.trigger();settle();assert w.isVisible() and not w.isMinimized()
    w.tray.capture_action.trigger();assert w.worker.stop_capture.is_set()
    w.on_event('stopped',None);w.tray.capture_action.trigger()
    assert w.worker.commands.get_nowait()[0]=='start' and w.capturing
    # Tray controls use the same persisted desktop-caption setting.
    w.tray.desktop_action.trigger();settle();assert not w.desktop.options['enabled']
    assert not load_settings()['desktop_captions']['enabled']
    w.tray.desktop_action.trigger();settle();assert w.desktop.options['enabled']
    # Keeping a settings draft open must not strand an invisible modal dialog.
    errors=[]
    def hide_restore_settings():
        try:
            d=w.dialog;d.name.setText('Unsaved draft')
            assert w.hide_to_tray() and not d.isVisible()
            w.restore_from_tray();assert d.isVisible() and d.name.text()=='Unsaved draft'
            d.reject()
        except BaseException as error:errors.append(repr(error));w.dialog.reject()
    QTimer.singleShot(60,hide_restore_settings);w.open_settings();settle();assert not errors,errors
    config=Path(folder.name)/'config.json';export_configuration(w.settings,config)
    assert import_configuration(config)['close_to_tray'] and import_configuration(config)['minimize_to_tray']
    # Fully exiting must stop work and remove both the overlay and tray icon.
    worker_state={}
    w.capturing=False;w.hide_to_tray();w.tray.quit_action.trigger();settle(550)
    assert w._closed and w.closing and not translator.isRunning()
    assert w.worker.quit_requested.is_set() and w.worker.download_cancel.is_set()
    assert not w.tray.icon.visible and w.desktop._shutdown
    # An unavailable tray or a disabled close preference must still allow exit.
    other=Window(start_workers=False);assert not other.tray.available
    other.show();other.close();settle(450);assert other._closed
    disabled=Window(start_workers=False);disabled.tray.shutdown();disabled.tray=TrayController(disabled,FakeIcon)
    disabled.settings['close_to_tray']=False;disabled.show();disabled.close();settle(450)
    assert disabled._closed and not disabled.tray.icon.visible
    # Exiting through the tray while settings are open closes the modal safely.
    modal=Window(start_workers=False);modal.tray.shutdown();modal.tray=TrayController(modal,FakeIcon);modal.show()
    QTimer.singleShot(60,modal.request_quit);modal.open_settings();settle(550)
    assert modal._closed and not modal.translator.isRunning()
    saving=Window(start_workers=False);saving.tray.shutdown();saving.tray=TrayController(saving,FakeIcon);saving.show()
    def save_then_exit():
        saving.dialog.close_to_tray.setChecked(False)
        saving.dialog.save();saving.request_quit()
    QTimer.singleShot(60,save_then_exit);saving.open_settings();settle(600)
    assert saving._closed and not saving.translator.isRunning() and load_settings()['close_to_tray'] is False
    print(json.dumps(dict(close_hides_taskbar_window=True,capture_continues=True,history_saved_in_background=True,
        desktop_captions_continue=True,background_layout_deferred=True,real_local_ipc_restores_window=True,
        minimize_to_tray=True,menu_controls_persist=True,settings_draft_restored=True,
        explicit_exit_stops_workers=True,missing_tray_falls_back_to_exit=True,close_preference_can_be_disabled=True,
        exit_with_open_settings=True,exit_during_settings_save_preserves_data=True),indent=2))
finally:
    if not w._closed:w.request_quit();settle(500)
