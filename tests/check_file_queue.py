"""Queue ordering, recovery, window/tray lifecycle and history integration."""
import json,os,queue,sys,threading
from pathlib import Path
from tempfile import TemporaryDirectory
folder=TemporaryDirectory(prefix='captions-filequeue-test-')
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(folder.name)/'data')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from file_queue import FileQueue
from storage import default_settings,save_settings,export_session
from app import Window
qt=QApplication([])
def settle(ms=35):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
root=Path(folder.name);paths=[]
for index in range(3):
    p=root/f'fixture-{index}.wav';p.write_bytes(b'fake');paths.append(p)
settings=default_settings();settings.update(translate=False,api_key='DO_NOT_COPY_PROFILE_OR_KEYS')
q=FileQueue(path=root/'queue.json');dispatched=[];q.dispatch.connect(lambda job:dispatched.append(job['id']))
q.set_asr_options('qwen3-asr-1.7b','Chinese')
assert q.add(paths,settings)==3
assert all(job['options']['asr_model']=='qwen3-asr-1.7b' and job['options']['source_language']=='Chinese' for job in q.jobs)
q.set_asr_options('mega-asr','Japanese')
assert FileQueue(path=q.path).asr_options=={'asr_model':'mega-asr','source_language':'Japanese'}
assert all(job['options']['asr_model']=='qwen3-asr-1.7b' for job in q.jobs)
legacy_path=root/'legacy-queue.json';legacy_path.write_text(json.dumps({'version':1,'jobs':q.jobs}),encoding='utf-8')
legacy=FileQueue(path=legacy_path)
assert legacy.asr_options=={'asr_model':'parakeet','source_language':'auto'}
assert legacy.jobs[0]['options']['asr_model']=='qwen3-asr-1.7b'
assert q.add(paths,settings)==0
settings['domain_bias']['test_snapshot_mutation']=True
assert q.jobs[0]['options']['domain_bias']!=settings['domain_bias']
assert 'DO_NOT_COPY' not in q.path.read_text('utf-8')
q.move(q.jobs[2]['id'],-1);order=[job['id'] for job in q.jobs]
q.set_available(False);settle();assert not dispatched
q.set_available(True);settle();assert dispatched==[order[0]]
q.update(order[0],'progress',{'stage':'transcribing','percent':45,'stage_percent':30});q.pause(True)
q.update(order[0],'done',{'status':'completed'});settle();assert len(dispatched)==1
q.pause(False);settle();assert dispatched[-1]==order[1] and len(dispatched)==2
restored=FileQueue(path=q.path);assert restored.paused and restored.find(order[1])['status']=='interrupted'
q.update(order[1],'done',{'status':'failed','error':'fixture failure'});settle();assert dispatched[-1]==order[2]
q.update(order[2],'done',{'status':'cancelled'});settle();q.retry(order[1]);settle();assert q.active==order[1]
q.update(order[1],'done',{'status':'completed'});q.clear_finished();assert not q.jobs

class Worker:
    def __init__(self):
        self.commands=queue.Queue();self.file_cancel=threading.Event();self.download_cancel=threading.Event()
        self.stop_capture=threading.Event();self.quit_requested=threading.Event();self.engine_key='fixture'
    def isRunning(self):return False
save_settings(default_settings()|{'translate':False})
w=Window(start_workers=False);w.worker=Worker();w.show();settle();w.open_files();settle()
try:
    d=w.file_dialog;w.capturing=True;w.file_queue.set_available(False)
    d.model_combo.setCurrentIndex(d.model_combo.findData('qwen3-asr-1.7b'))
    d.language_combo.setCurrentIndex(d.language_combo.findData('Chinese'))
    d.add_paths(paths[:1]);settle()
    d.model_combo.setCurrentIndex(d.model_combo.findData('mega-asr'))
    d.language_combo.setCurrentIndex(d.language_combo.findData('Japanese'))
    d.add_paths(paths[1:2]);settle();assert not w.file_queue.active
    assert w.settings['asr_model']=='parakeet' and w.settings['source_language']=='auto'
    w.settings.update(asr_model='qwen3-asr-1.7b',source_language='German');d.refresh()
    assert d.model_combo.currentData()=='mega-asr' and d.language_combo.currentData()=='Japanese'
    w.on_event('stopped',None);settle();ident=w.file_queue.active;assert ident
    first=w.file_queue.find(ident);sid=first['session'];assert sid and w.source_summary.text().endswith('本地文件')
    command,args=w.worker.commands.get_nowait();assert command=='file' and args['id']==ident
    assert args['options']['asr_model']=='qwen3-asr-1.7b' and args['options']['source_language']=='Chinese'
    assert not w.capturing and w.start.text()=='取消文件转录'
    event=lambda kind,value:w.on_event('file_event',{'id':ident,'kind':kind,'value':value})
    event('progress',{'stage':'transcribing','percent':50,'stage_percent':40})
    assert d.items[ident][1].value()==50
    event('committed',{'text':'Physics fixture.','audio_start_ms':1250,'audio_end_ms':2800})
    assert w.history.rows(sid)[0]['en']=='Physics fixture.'
    assert w.history.rows(sid)[0]['start_ms']==1250
    w.current_session=w.view_session=sid;w.new_session_view();assert w.current_session==sid
    w.file_queue.pause(True);w.toggle();assert w.worker.file_cancel.is_set() and w.worker.download_cancel.is_set()
    event('done',{'status':'cancelled'});settle();assert w.file_queue.active is None
    assert w.history.session(sid)['ended_at'] is not None
    exported=root/'result.srt';export_session(w.history,sid,exported)
    assert '00:00:01,250 --> 00:00:02,800' in exported.read_text('utf-8-sig')
    assert len(w.history.rows(sid))==1 and not w.capturing
    w.file_queue.pause(False);settle();assert w.file_queue.active
    assert not w.worker.file_cancel.is_set()
    command,args=w.worker.commands.get_nowait()
    assert command=='file' and args['options']['asr_model']=='mega-asr' and args['options']['source_language']=='Japanese'
    w.file_queue.pause(True)
    w.on_event('file_event',{'id':w.file_queue.active,'kind':'done','value':{'status':'completed'}})
    w.toggle();command,args=w.worker.commands.get_nowait()
    assert command=='start' and args['asr_model']=='qwen3-asr-1.7b' and args['source_language']=='German'
    assert w.file_queue.asr_options=={'asr_model':'mega-asr','source_language':'Japanese'}
    w.request_quit();settle(500)
    assert w._closed and w.worker.file_cancel.is_set() and w.file_queue.paused
    # Translation backpressure must not strand earlier file sessions once ASR
    # moves current_session past them. This transport never touches a network.
    import app as app_module
    app_module.get_key=lambda profile:''
    translated=Window(start_workers=False);translated.translator.stop();translated.translator.wait(2000)
    class FakeTranslator:
        def __init__(self):self.jobs=queue.Queue(maxsize=32)
        def enqueue(self,*job):self.jobs.put_nowait(job)
        def isRunning(self):return False
    translated.translator=FakeTranslator();translated.settings.update(translate=True,source_language='German')
    sessions=[translated.history.create_session('backpressure '+str(i),translated.profile()) for i in range(3)]
    for session in sessions:
        for i in range(45):translated.history.add(session,'Fixture '+str(i),i*1000,i*1000+800,'queued',metadata={'source_language':'Chinese' if session==sessions[0] else 'English'})
        translated.pump(session)
    translated.current_session=sessions[-1]
    count=0
    while not translated.translator.jobs.empty():
        session,segment,*payload=translated.translator.jobs.get_nowait()
        assert payload[-2]['source_language']==translated.history.segment(segment)['source_language']
        translated.on_translation(session,segment,'Translated fixture.',True);count+=1
        assert count<=135
    assert count==135 and all(not translated.history.queued(session) for session in sessions)
    translated.request_quit();settle(500);assert translated._closed
    print(json.dumps({'queue_ordering':True,'dedup_and_snapshot':True,'no_credentials_in_queue':True,
        'pause_retry_cancel':True,'restart_preserves_interrupted_jobs':True,'waits_for_live_capture':True,
        'per_file_history':True,'srt_timing_preserved':True,'gui_progress_updates':True,'clean_exit_cancels_file':True,
        'all_file_translations_drain_after_backpressure':True,'file_model_and_language_independent':True,
        'file_model_preferences_persist':True,'queued_models_unchanged':True,'legacy_jobs_preserved':True,
        'live_capture_uses_live_model_after_file_transcription':True,'translation_uses_file_source_language':True}))
finally:
    if not w._closed:w.request_quit();settle(500)
