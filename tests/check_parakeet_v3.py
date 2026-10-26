"""Offline multilingual, timed-boundary and model-selection regression checks."""
import os,sys,json
from pathlib import Path
from tempfile import TemporaryDirectory
temporary=TemporaryDirectory(prefix='captions-v3-')
os.environ['PARAKEET_DATA_DIR']=str(Path(temporary.name)/'data')
os.environ['QT_QPA_PLATFORM']='offscreen'
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'src'))
import numpy as np
from languages import PARAKEET_V3_LANGUAGES,ASR_MODELS,supported_language
from text_rules import normalized,complete_text,sentence_spans
from segmentation import SentenceAssembler
from streaming import timed_hypothesis
from config_io import validate_configuration
from storage import default_settings
from domain_bias import default_domain_bias,model_bias
from engine import read_vocabulary
vocab_path=Path(temporary.name)/'vocabulary.json'
vocab_path.write_text(json.dumps({str(i):'piece'+str(i) for i in range(1031)}),'utf-8')
vocab,blank=read_vocabulary(vocab_path,'parakeet');assert blank==1024 and len(vocab)==1024
vocab_path.write_text(json.dumps({'blank_id':8192,'id_to_token':['piece'+str(i) for i in range(8192)]}),'utf-8')
vocab,blank=read_vocabulary(vocab_path,'parakeet-v3');assert blank==8192 and len(vocab)==8192
assert len(PARAKEET_V3_LANGUAGES)==25 and len(ASR_MODELS)==4
assert all(supported_language('parakeet-v3',v) for v in PARAKEET_V3_LANGUAGES|{'auto'})
assert not supported_language('parakeet-v3','Chinese')
assert not supported_language('parakeet','French')
assert not supported_language('qwen3-asr-1.7b','Maltese')
for a,b in [('énergie','energie'),('материя','теория'),('ύλη','θεωρία')]:assert normalized(a)!=normalized(b)
assert normalized('e\u0301nergie')==normalized('énergie')
for language,text in [('French','La matière noire est'),('German','Die Messung ist'),('Russian','Это данные для')]:
    assert not complete_text(text+'.',language)
assert len(list(sentence_spans('La mesure est terminée. Nous continuons.', 'French')))==2
assert len(list(sentence_spans('Das ist ein Test. Messungen folgen.', 'German')))==2
assert len(list(sentence_spans('Наблюдения завершены. Мы продолжаем.', 'Russian')))==2
words=[('twenty',.1),('three',.1),('particles.',.1)]
h,ends=timed_hypothesis({'words':words,'word_spans':[(w,t,t+.16) for w,t in words]},2)
assert h==[('twenty three',2.1),('particles.',2.1)] and ends==[2.26,2.26]
s=SentenceAssembler(language='French');h=[('La',1.),('mesure',1.2),('est',1.4),('terminée.',1.6)]
out=s.update(h,h,2.5,acoustic_end=True,ends=[1.16,1.36,1.56,1.84])
assert out[0]['audio_end_ms']==1840 and out[0]['audio_start_ms']==1000
v=default_settings()|{'asr_model':'parakeet-v3','source_language':'Ukrainian'}
assert validate_configuration(v)['asr_model']=='parakeet-v3'
v['source_language']='Chinese'
try:validate_configuration(v);raise AssertionError('Unsupported language accepted')
except ValueError:pass
bias=default_domain_bias();bias['parakeet_v3']=True
assert model_bias(bias,'parakeet-v3')['terms'] and not model_bias(bias,'parakeet')['terms']
spec=json.loads((root/'resources/model-catalog.json').read_text('utf-8'))['models']['parakeet-v3']
assert {f['name'] for f in spec['files']}=={'parakeet_'+part+ext for part in ('encoder','decoder','joint','melspectogram') for ext in ('.xml','.bin')}|{'parakeet_vocab.json','tokenizer.json'}
assert len({f['sha256'] for f in spec['files']})==len(spec['files'])

# A verified unchanged tail may finish sooner; stale/missing timing and an
# unfinished phrase must retain the ordinary endpoint. Never add ASR calls.
import streaming
class FakeVad:
    def __call__(self,block):return .99 if block[0] else 0.
streaming.Vad=FakeVad
class Engine:
    max_samples=240000;language='English';model_id='parakeet-v3'
    def __init__(self,text,timed=True,stale=False):self.text=text;self.calls=0;self.timed=timed;self.stale=stale
    def infer(self,audio):
        self.calls+=1;parts=self.text.split();n=len(parts)
        words=[(w,1.+i*.2) for i,w in enumerate(parts)]
        result={'text':self.text,'words':words,'infer_ms':1}
        if self.timed:result['word_spans']=[(w,t,1. if self.stale else (2. if i==n-1 else t+.1)) for i,(w,t) in enumerate(words)]
        return result
def feed(r,blocks,voice):
    for _ in range(blocks):r.push(np.full(512,.01 if voice else 0.,np.float32));r.poll()
delays=[]
for timed,stale,incomplete in [(True,False,False),(False,False,False),(True,True,False),(True,False,True)]:
    events=[];e=Engine('The sample is' if incomplete else 'The sample is ready.',timed,stale)
    r=streaming.Recognizer(e,lambda k,v:events.append((k,v,r.clock)),interval=1.)
    feed(r,64,True);end=r.clock;feed(r,32,False)
    committed=[clock for k,v,clock in events if k=='committed']
    if incomplete:assert not committed
    else:
        delay=(committed[0]-end)/16000;delays.append(delay)
        assert abs(delay-(.608 if timed and not stale else .768))<.001,delay
    assert e.calls==3,e.calls
    count=e.calls;feed(r,100,False);assert e.calls==count

from PySide6.QtWidgets import QApplication
from settings_dialog import SettingsDialog
from file_queue import FileQueue
qt=QApplication([]);dialog=SettingsDialog(default_settings())
dialog.asr_model.setCurrentIndex(dialog.asr_model.findData('parakeet-v3'))
assert dialog.asr_model.count()==4 and dialog.interval.isEnabled() and not dialog.feature_backend.isEnabled()
assert not dialog.source_language.model().item(dialog.source_language.findData('Chinese')).isEnabled()
assert dialog.source_language.model().item(dialog.source_language.findData('Ukrainian')).isEnabled()
dialog.source_language.setCurrentIndex(dialog.source_language.findData('Ukrainian'))
dialog.asr_model.setCurrentIndex(dialog.asr_model.findData('qwen3-asr-1.7b'))
assert dialog.source_language.currentData()=='auto'
q=FileQueue(path=Path(temporary.name)/'queue.json');q.set_asr_options('parakeet-v3','French')
assert q.asr_options['source_language']=='French' and default_settings()['asr_model']=='parakeet'
dialog.close()
print(json.dumps({'ok':True,'v3_languages':25,'unicode_preserved':True,'word_end_timestamps':True,
    'independent_model_selection':True,'separate_phrase_bias':True,'stable_endpoint_seconds':delays,
    'no_extra_asr_calls':True,'missing_or_stale_timing_keeps_normal_endpoint':True}))
