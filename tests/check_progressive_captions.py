"""Long-sentence preview/replacement regressions; synthetic ASR and no API calls."""
import os,sys,json,time,tempfile,queue
from pathlib import Path
temporary=tempfile.TemporaryDirectory(prefix='captions-progressive-');root=Path(temporary.name)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(root/'data')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from progressive_captions import StablePrefix,DraftCaptions
from config_io import export_configuration,import_configuration,validate_configuration
from storage import default_settings,save_settings,export_session
import streaming,qwen_streaming

long='The detector measures many different particles during each experiment and the scientists compare these observations against detailed predictions from several independent theoretical calculations'
p=StablePrefix();assert p.offer(long,0,3,0) is None
draft=p.offer(long,0,6,0);assert draft and len(draft['text'].split())>=10
for now in range(7,50):
    value=p.offer(long+' with further observations',0,now,0)
    assert not value or not value.get('translate'),value
assert p.offer('A short sentence.',1,60,50)['text']=='A short sentence.'
p=StablePrefix();p.offer(long,0,3,0)
assert p.offer('An entirely revised sentence which has no stable prefix at all',0,7,0) is None
p=StablePrefix();chinese='我们通过对多个不同能量区间的实验数据进行详细分析来检验理论模型并比较观测结果'
p.offer(chinese,0,3,0);assert p.offer(chinese,0,6,0)

# Configurable budgets, one-second default, growth and duplicate suppression.
for limit in (1,2,3,10):
    p=StablePrefix(limit);events=[]
    for step in range(12):
        text=' '.join('particle'+str(i) for i in range(20+15*step))
        now=6+1.25*step
        p.offer(text,0,now-.1,0)
        event=p.offer(text,0,now,0)
        if event and event.get('translate'):events.append(event)
    assert len(events)==limit,(limit,events)
    assert len({v['text'] for v in events})==limit
    assert all(len(a['text'])<len(b['text']) for a,b in zip(events,events[1:]))
p=StablePrefix(2,.5);seed=' '.join('particle'+str(i) for i in range(20));expanded=seed+' '+' '.join('particle'+str(i) for i in range(20,35))
p.offer(seed,0,3,0);assert p.offer(seed,0,6,0)
assert not (p.offer(expanded,0,6.1,0) or {}).get('translate')
assert not (p.offer(expanded,0,6.4,0) or {}).get('translate')
assert p.offer(expanded,0,6.5,0)
assert default_settings()['progressive_translation_limit']==2
assert default_settings()['progressive_translation_interval']==1.

class Vad:
    def __call__(self,block):return .99
streaming.Vad=qwen_streaming.Vad=Vad
class Engine:
    max_samples=17*16000
    def __init__(self,model):self.model_id=model;self.calls=0
    def infer(self,audio):
        self.calls+=1
        return dict(text=long,words=[(w,.25*i) for i,w in enumerate(long.split())],language='English',infer_ms=1)

for model,cls in [('parakeet',streaming.Recognizer),('parakeet-v3',streaming.Recognizer),('qwen3-asr-1.7b',qwen_streaming.QwenRecognizer),('mega-asr',qwen_streaming.QwenRecognizer)]:
    calls=[]
    for enabled in (False,True):
        events=[];engine=Engine(model);r=cls(engine,lambda k,v:events.append((k,v)),progressive=enabled)
        for _ in range(240):r.push(np.full(512,.01,np.float32));r.poll()
        partials=[v for k,v in events if k=='provisional'];assert bool(partials)==enabled,(model,partials)
        assert sum(v.get('translate',False) for v in partials)==int(enabled),(model,partials)
        assert not any(k=='committed' for k,v in events)
        r.poll(stop=True);final=[v for k,v in events if k=='committed']
        assert len(final)==1 and final[0]['text']==long
        if enabled:assert partials[0]['sentence_id']==final[0]['sentence_id']
        calls.append(engine.calls)
    assert calls[0]==calls[1],(model,calls)

from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from translation import Translator
import translation,app
qt=QApplication([])
def settle(ms=30):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
class ManualTranslator(Translator):
    def start(self):pass
app.Translator=ManualTranslator;app.get_key=lambda _profile:''
s=default_settings();s['desktop_captions']['enabled']=True
s['profiles']=[dict(id='fixture',name='Fixture',protocol='openai',base_url='http://127.0.0.1/v1',model='fixture',auth='none',extra={})]
s['selected_profile']='fixture';save_settings(s)
w=app.Window(start_workers=False);w.show();settle()
try:
    sid=w.history.create_session('Synthetic long sentence',w.profile());w.current_session=w.view_session=sid;w.capturing=True
    w.on_event('provisional',draft);job=w.translator.jobs.get_nowait();ident=job[1]
    assert job[4]['_provisional'] and job[2]==draft['text']
    w.on_translation(sid,ident,'探测器测量多种粒子',True);w.render_now();settle()
    assert '探测器测量多种粒子' in w.browser.toPlainText() and '临时译文' in w.browser.toPlainText()
    assert len(w.history.rows(sid))==0 and w.desktop.pairs[-1][1].endswith('…')
    second=dict(draft,text=' '.join(long.split()[:22]),audio_end_ms=9000)
    w.on_event('provisional',second);second_job=w.translator.jobs.get_nowait()
    assert second_job[2]==second['text'] and second_job[1]!=ident and len(w.drafts.entries)==1
    w.render_now();assert '探测器测量多种粒子' in w.browser.toPlainText()
    w.on_translation(sid,second_job[1],'optional update failed',False);w.render_now()
    assert '探测器测量多种粒子' in w.browser.toPlainText()
    w.on_event('committed',dict(text=long,sentence_id=0,audio_start_ms=0,audio_end_ms=10000))
    row=w.history.rows(sid)[0];full_job=w.translator.jobs.get_nowait()
    assert full_job[2]==long and not full_job[4].get('_provisional')
    w.render_now();assert '探测器测量多种粒子' in w.browser.toPlainText()
    # Final failure keeps the readable draft while exports contain one full source.
    w.on_translation(sid,row['id'],'fixture unavailable',False);w.render_now()
    assert '探测器测量多种粒子' in w.browser.toPlainText() and w.translation_warning.isVisible()
    exported=root/'result.srt';assert export_session(w.history,sid,exported)==1
    text=exported.read_text('utf-8-sig');assert long in text and '探测器测量多种粒子' not in text
    w.retry_current();retry=w.translator.jobs.get_nowait();assert retry[2]==long
    w.on_translation(sid,row['id'],'完整句子译文。',True);w.render_now();settle()
    assert '完整句子译文。' in w.browser.toPlainText() and '临时译文' not in w.browser.toPlainText()
    assert '探测器测量多种粒子' not in w.browser.toPlainText() and len(w.history.rows(sid))==1
    assert w.desktop.pairs[-1]==(long,'完整句子译文。')
    w.on_translation(sid,ident,'迟到的旧译文',True);w.render_now();assert '迟到的旧译文' not in w.browser.toPlainText()
    # Queue pressure and unavailable API skip requests, but retain raw display.
    value=dict(draft,sentence_id=1)
    w.settings['progressive_translation']=False;w.on_event('provisional',value);assert not w.drafts.entries
    w.settings['progressive_translation']=True;w.active_jobs.add('busy');w.on_event('provisional',value)
    assert w.drafts.entries and w.translator.jobs.empty()
    w.active_jobs.clear();w.set_translation_status(False,'fixture');w.on_event('provisional',value)
    assert w.drafts.entries and w.translator.jobs.empty()
    w.set_translation_status(None);w.on_event('provisional',value);assert w.drafts.entries
    # Final arrives before queued draft starts: cancel just that obsolete request.
    w.on_event('committed',dict(text=long,sentence_id=1,audio_start_ms=10000,audio_end_ms=20000))
    assert not w.drafts.entries and w.translator.jobs.qsize()==1
    assert not w.translator.jobs.get_nowait()[1].startswith('draft:')
    # Immediate punctuation remains reversible in the actual UI integration.
    reversible=dict(draft,sentence_id=40,text='The sensor is ready.',revisable=True,translate=True)
    w.active_jobs.clear();w.on_event('provisional',reversible)
    job=w.translator.jobs.get_nowait();old_id=job[1]
    w.on_translation(sid,old_id,'传感器已准备好。',True)
    before_rows=len(w.history.rows(sid))
    w.on_event('provisional',dict(reversible,text='The sensor is faulty.',translate=False))
    w.on_translation(sid,old_id,'迟到且已失效的译文',True);w.render_now()
    assert 'The sensor is faulty.' in w.browser.toPlainText()
    assert '传感器已准备好。' not in w.browser.toPlainText() and '迟到且已失效的译文' not in w.browser.toPlainText()
    assert len(w.history.rows(sid))==before_rows and w.translator.jobs.empty()
    w.on_event('provisional',dict(sentence_id=40,withdraw=True));w.render_now()
    assert 'The sensor is faulty.' not in w.browser.toPlainText()
    # Translation disabled still shows source, then durable completion replaces it.
    w.settings['translate']=False
    w.on_event('provisional',dict(reversible,sentence_id=41));w.render_now()
    assert 'The sensor is ready.' in w.browser.toPlainText() and w.translator.jobs.empty()
    w.on_event('committed',dict(text='The sensor is ready.',sentence_id=41,audio_start_ms=20000,audio_end_ms=22000))
    w.render_now();assert w.browser.toPlainText().count('The sensor is ready.')==1
    assert len(w.history.rows(sid))==before_rows+1 and not w.drafts.entries
    w.settings['translate']=True
    config=root/'settings.json';export_configuration(w.settings,config)
    assert import_configuration(config)['progressive_translation'] is True
    from settings_dialog import SettingsDialog
    dialog=SettingsDialog(w.settings,w)
    assert dialog.progressive_limit.value()==2 and dialog.progressive_interval.value()==1.
    assert dialog.confirmation_wait.value()==3.
    dialog.confirmation_wait.setValue(.5)
    dialog.progressive_limit.setValue(4);dialog.progressive_interval.setValue(.5)
    settings=dialog.draft();assert settings['progressive_translation_limit']==4 and settings['progressive_translation_interval']==.5
    dialog.load_draft(settings);assert dialog.progressive_limit.value()==4 and dialog.progressive_interval.value()==.5
    export_configuration(settings,config);restored=import_configuration(config)
    assert restored['progressive_translation_limit']==4 and restored['progressive_translation_interval']==.5 and restored['confirmation_wait']==.5
    dialog.asr_model.setCurrentIndex(dialog.asr_model.findData('qwen3-asr-1.7b'));assert not dialog.appearance.isRowVisible(dialog.confirmation_wait)
    dialog.asr_model.setCurrentIndex(dialog.asr_model.findData('parakeet-v3'));assert dialog.appearance.isRowVisible(dialog.confirmation_wait)
    dialog.reject();dialog.deleteLater()
    for key,value in [('progressive_translation_limit',0),('progressive_translation_limit',2.5),('progressive_translation_limit',True),('progressive_translation_interval',0),('progressive_translation_interval',float('nan')),('confirmation_wait',0),('confirmation_wait',float('inf'))]:
        try:validate_configuration(dict(w.settings,**{key:value}));raise AssertionError('accepted invalid preview setting')
        except ValueError:pass
    try:validate_configuration(dict(w.settings,progressive_translation='false'));raise AssertionError('accepted invalid toggle')
    except ValueError:pass
finally:
    w.capturing=False;w.close();settle(450)

# Optional requests fail fast with one attempt; normal requests retain their policy.
calls=[]
class Session:
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def post(self,url,**kwargs):
        calls.append(kwargs)
        class Response:
            status_code=500 if len(calls)==1 else 200
            def json(self):return {'choices':[{'message':{'content':'完整译文'}}]}
        return Response()
original=translation.requests.Session;translation.requests.Session=Session
t=Translator();results=[];t.result.connect(lambda *r:results.append(r))
profile=s['profiles'][0]
try:
    t.enqueue('fixture','draft:optional','partial',[],dict(profile,_provisional=True),'')
    t.enqueue('fixture','complete','full',[],profile,'');t.start()
    deadline=time.monotonic()+3
    while len(results)<2 and time.monotonic()<deadline:settle()
    assert len(calls)==2 and calls[0]['timeout']==(3,5) and calls[1]['timeout']==(8,20)
    assert results[0][-1] is False and results[1][-1] is True
    assert '_provisional' not in json.dumps(calls)
finally:t.stop();assert t.wait(2000);translation.requests.Session=original

# Revised boundaries and stale/deleted sessions cannot resurrect an old preview.
d=DraftCaptions();r=d.add('one',draft);d.link('one',0,'final','Short.');assert not d.entries
r=d.add('one',draft);d.discard_session('one');assert not d.translated(r['id'],'old',True)
d=DraftCaptions();r=d.add('one',draft);old_id=r['id'];d.translated(old_id,'old',True)
second=dict(draft,text=long,audio_end_ms=9000);r=d.add('one',second);new_id=r['id']
assert r['en']==draft['text'] and r['zh']=='old' and len(d.entries)==1
assert not d.translated(old_id,'late',True)
assert d.translated(new_id,'new',True) and r['en']==long and r['zh']=='new'
# A revised final boundary inside a pending longer preview cancels that draft.
d=DraftCaptions();r=d.add('one',draft);d.translated(r['id'],'old',True);r=d.add('one',second)
d.link('one',0,'final',draft['text']);assert not d.entries
print(json.dumps(dict(all_four_models=True,no_extra_asr_calls=True,configurable_preview_limit=True,default_limit=2,default_interval=1,
    stable_clause_or_reversible_punctuation=True,atomic_full_replacement=True,failed_final_preserves_draft=True,
    no_duplicate_history_or_exports=True,late_results_ignored=True,queue_pressure_skips_requests=True,
    optional_request_timeout_and_no_retry=True,settings_roundtrip=True)))
