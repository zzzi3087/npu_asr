"""Boundary/latency/idle-work regressions, with synthetic text and audio only."""
import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from segmentation import SentenceAssembler,TextAssembler
from text_rules import terminal,sentence_spans,normalized
from text_agreement import TextAgreement
import streaming,qwen_streaming


def words(text,start=0.,step=.2):return [(w,start+i*step) for i,w in enumerate(text.split())]
def texts(events):return [v['text'] for k,v in events if k=='committed']

for value in ('Dr.','Eq.','Fig.','U.S.','A.','e.g.','3.14','https://example.com.'):
    assert not terminal(value),value
assert not terminal('3.','14') and not terminal('125.','GeV')
assert normalized('twenty-three')==normalized('23')
for text in ('Dr. Green measures 3.14 GeV. It works.',
             'We visit the National. Research Laboratory. They agree.',
             'The value in Eq. (2) is unchanged. We continue.'):
    spans=list(sentence_spans(text));assert len(spans)==2,(text,spans)
text='测量已经完成。结果与理论一致！我们继续实验。'
assert len(list(sentence_spans(text)))==3
a=TextAgreement()
assert not a.update('The sample is ready. We begin')[0]
assert a.update('The sample is ready. We begin today.')[0]=='The sample is ready.'
# A later revision to already displayed words must not duplicate that prefix.
assert a.update('The samples are ready. We begin today.',final=True)[0]=='We begin today.'
a=TextAgreement();a.update('Yes. Yes. We continue')
assert a.update('Yes. Yes. We continue today.')[0]=='Yes. Yes.'
assert a.update('Yes. Yes. We continue today.',final=True)[0]=='We continue today.'
a=TextAgreement();a.update('The value is 3. 14 electron volts. We continue')
assert a.update('The value is 3.14 electron volts. We continue')[0]==''

# Fixed word/age thresholds used to split ordinary clauses and even one word.
for suffix in ('is','over','just','the','plus','per','to','is uh'):
    s=SentenceAssembler();h=words('The detector response '+suffix)
    assert not s.update(h,h,3,acoustic_end=True,idle=.8)
    assert not s.update([],None,3.4,idle=1.2)
    assert not s.update([],h,15,idle=0),suffix
    tail=words('consistent with expectations.',start=15)
    result=s.update(tail,tail,17,acoustic_end=True,idle=.8)
    assert len(result)==1 and suffix in result[0]['text'],result
s=SentenceAssembler();h=words('The measurement of the particle energy requires a careful calibration of the detector response across all of the selected events before we can evaluate the result',step=.35)
assert not s.update(h,h,13), 'Plain word count must not cut a sentence'
tail=words('with sufficient precision.',start=13)
assert len(s.update(tail,tail,15,acoustic_end=True,idle=.8))==1

# Revision removes a speculative period; punctuation needs repeated right context.
s=SentenceAssembler();h=words('We measured the response. With care')
assert not s.update(h,h,2)
assert not s.update([],words('We measured the response with care'),2.1)
assert '.' not in s.text
s=SentenceAssembler();h=words('The sample is ready. We start.')
assert not s.update(h,h,2)
out=s.update([],h,2.1);assert [r['text'] for r in out]==['The sample is ready.']
assert s.update([],None,2.2,final=True)[0]['text']=='We start.'

# An incomplete tail stays visible, but quiet polling does not rescan it.
s=SentenceAssembler();h=words('The measured value is')
s.update(h,h,2,acoustic_end=True,idle=.8);before=s.scans
for i in range(10000):s.update([],None,2+i*.00001,idle=.8+i*.00001)
assert s.scans==before
assert s.update([],None,4,final=True)[0]['text']=='The measured value is'

# Independent acoustic windows must not become forced subtitle lines.
a=TextAssembler();assert not a.update('The measured value is',0,1,now=1.8,idle=.8,language='English')
out=a.update('above the reference.',2,3,now=3.8,idle=.8,language='English')
assert [v['text'] for v in out]==['The measured value is above the reference.']
a=TextAssembler();out=a.update('这是因为',0,1,now=1.8,idle=.8,language='Chinese');assert not out
out=a.update('探测器需要校准。',2,3,now=3.8,idle=.8,language='Chinese')
assert out[0]['text']=='这是因为探测器需要校准。'
a=TextAssembler();assert not a.update('The beam energy is approximately.',0,12,now=12,window_limit=True,language='English')
out=a.update('one hundred GeV. We continue.',12,15,now=15.8,idle=.8,language='English')
assert [v['text'] for v in out]==['The beam energy is approximately one hundred GeV.','We continue.'],out
a=TextAssembler();assert not a.update('粒子能量大约。',0,12,now=12,window_limit=True,language='Chinese')
out=a.update('为一百GeV。',12,15,now=15.8,idle=.8,language='Chinese')
assert out[0]['text']=='粒子能量大约为一百GeV。',out
a=TextAssembler();assert not a.update('The sample is ready.',0,12,now=12,window_limit=True,language='English')
out=a.update('We begin measuring.',12,15,now=15.8,idle=.8,language='English')
assert [v['text'] for v in out]==['The sample is ready.','We begin measuring.'],out

class FakeVad:
    def __call__(self,block):return .99 if block[0] else .0
streaming.Vad=qwen_streaming.Vad=FakeVad
class Engine:
    max_samples=17*16000;model_id='qwen3-asr-1.7b'
    def __init__(self,text):self.text=text;self.calls=0;self.sizes=[]
    def infer(self,audio):
        self.calls+=1;self.sizes.append(len(audio))
        return {'text':self.text,'words':words(self.text,start=.1,step=.12),
            'language':'English','infer_ms':1,'cpu_ms':0}

def feed(r,seconds,speech):
    block=np.full(512,.01 if speech else 0,np.float32)
    for _ in range(round(seconds*16000/512)):r.push(block);r.poll()

latencies={}
for cls in (streaming.Recognizer,qwen_streaming.QwenRecognizer):
    events=[];engine=Engine('The observations support this conclusion.')
    r=cls(engine,lambda k,v:events.append((k,dict(v,clock=r.clock/16000))),quiet=True)
    feed(r,1.024,True);voice_end=r.clock/16000;feed(r,1.,False)
    assert texts(events)==[engine.text],texts(events)
    delay=next(v['clock'] for k,v in events if k=='committed')-voice_end
    assert delay<=.8,(cls.__name__,delay)
    assert engine.calls==1,engine.calls
    scans=getattr(r.segmenter,'scans',0);feed(r,20.,False)
    assert engine.calls==1 and getattr(r.segmenter,'scans',0)==scans
    latencies[cls.__name__]=round(delay,3)

    events=[];engine=Engine('The response is')
    r=cls(engine,lambda k,v:events.append((k,v)),quiet=True)
    feed(r,1.024,True);feed(r,1.,False);assert not texts(events)
    engine.text='consistent with the prediction.'
    feed(r,1.024,True);feed(r,1.,False)
    assert texts(events)==['The response is consistent with the prediction.'],texts(events)

# Repeated words and last partial text survive stop, including a tiny activation.
events=[];engine=Engine('The response is')
r=streaming.Recognizer(engine,lambda k,v:events.append((k,v)))
feed(r,1.024,True);feed(r,1.,False);feed(r,.064,True);r.poll(stop=True)
assert texts(events)==['The response is'],texts(events)
events=[];engine=Engine('The response is')
r=qwen_streaming.QwenRecognizer(engine,lambda k,v:events.append((k,v)),interval=999999)
feed(r,r.hard_limit/16000,True);assert not texts(events)
engine.text='consistent with theory.';feed(r,1.,True);feed(r,1.,False)
assert texts(events)==['The response is consistent with theory.'],texts(events)
assert max(engine.sizes)<=engine.max_samples
for model in ('qwen3-asr-1.7b','mega-asr'):
    events=[];engine=Engine('The sample is ready. We continue');engine.model_id=model
    r=qwen_streaming.QwenRecognizer(engine,lambda k,v:events.append((k,v)))
    feed(r,6.2,True)
    assert texts(events)==['The sample is ready.'] and r.active
    engine.text='The sample is ready. We continue with measurements.'
    feed(r,.5,True);feed(r,1.,False)
    assert texts(events)==['The sample is ready.','We continue with measurements.'],texts(events)
    assert all(v['asr_model']==model for k,v in events if k=='committed')
start=time.perf_counter();s=SentenceAssembler();h=words('The measured value is')
s.update(h,h,1)
for i in range(100000):s.update([],None,1.1,idle=.1)
print(json.dumps({'ok':True,'synthetic_endpoint_delay_seconds':latencies,
    'idle_100000_polls_ms':round((time.perf_counter()-start)*1000,2),
    'idle_boundary_scans':s.scans,'cross_window_phrases':True,'no_word_count_cut':True,
    'abbreviations_units_names':True,'unicode_text':True,'quiet_has_no_extra_inference':True}))
