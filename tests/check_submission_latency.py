"""Continuous-speech submission: late punctuation and a revised prefix word."""
import json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import streaming
from segmentation import SentenceAssembler
from text_rules import sentence_spans


def words(text,step=.25):return [(word,i*step) for i,word in enumerate(text.split())]


# Lecture-style independent sentences must not be held by an English guard.
examples=[
 ('Should we show this? You know.',2),
 ('because the measurement is reliable. And the results are ready.',2),
 ('We measure particles and fields and so on. That may explain the observation.',2),
 ('The measurement is complete. That we can repeat tomorrow.',1),
 ('We measured the response. With care.',1),
 ('If the detector fails. We repeat the measurement.',1),
]
for text,count in examples:
 assert len(list(sentence_spans(text,'English')))==count,text

# Text and two following words are already stable; a newly inserted period
# need not incur a third ASR pass. A speculative period still waits.
assembler=SentenceAssembler()
first=words('The sensor is ready We are measuring')
assert not assembler.update(first,first,2)
second=words('The sensor is ready. We are measuring')
events=assembler.update([],second,3.5)
assert [e['text'] for e in events]==['The sensor is ready.']
assembler=SentenceAssembler();first=words('The sensor is ready.')
assert not assembler.update(first,first,2)
assert not assembler.update([],first,3.5)

# One changing early word must not block an otherwise repeatedly established
# sentence until the age-based six-second fallback. Test the full recognizer.
class Vad:
 def __call__(self,block):return .99
streaming.Vad=Vad
class Engine:
 model_id='parakeet-v3';language='English';max_samples=240000
 def __init__(self):self.calls=0
 def infer(self,audio):
  self.calls+=1
  head=['The','This','Our','Their'][min(self.calls-1,3)]
  text=head+' detector measures particle energies accurately. We are starting another measurement'
  tokens=words(text,.18)
  return dict(text=text,words=tokens,word_spans=[(w,t,t+.13) for w,t in tokens],infer_ms=1,cpu_ms=0)

def replay(legacy=False):
 engine=Engine();events=[]
 recognizer=streaming.Recognizer(engine,lambda k,v:events.append((k,dict(v,clock=recognizer.clock/16000))))
 if legacy:recognizer.timeline.sentence_prefix=lambda current,previous,end,stable,language:stable
 for _ in range(220):recognizer.push(np.full(512,.01,np.float32));recognizer.poll()
 commits=[v for k,v in events if k=='committed']
 assert commits and 'energies accurately.' in commits[0]['text']
 return commits[0]['clock'],engine.calls,commits[0]

before,calls_before,_=replay(True);after,calls_after,event=replay()
assert after<before and calls_after==calls_before,(before,after,calls_before,calls_after)
assert event['uncertain'] # Revised word is disclosed, not disguised as exact agreement.

# Do not accept large rewrites or matching words shifted to unrelated times.
t=streaming.Timeline()
old=words('The detector measures particle energies accurately. We are starting')
changed=words('An observer analyzes unusual events differently. We are starting')
t.update(old,0,3)
assert not t.update(changed,0,4.5)[0]
assert not t.close_prefix([(w,stamp+2) for w,stamp in old],old,2)
for wait,expected in ((.5,True),(6.,False)):
 assembler=SentenceAssembler(max_wait=wait)
 hypothesis=words('The detector is ready. We can continue',.2)
 events=assembler.update(hypothesis,hypothesis,2)
 assert bool(events)==expected,(wait,events)
 if events:assert events[0]['uncertain'] and events[0]['boundary_reason']=='confirmation_timeout'
 assembler=SentenceAssembler(max_wait=wait)
 hypothesis=words('The energy is approximately. One hundred GeV.',.2)
 assert not assembler.update(hypothesis,hypothesis,2),events
engine=Engine();recognizer=streaming.Recognizer(engine,lambda *_:None,confirmation_wait=.5)
recognizer.reset();assert recognizer.timeline.max_wait==recognizer.segmenter.max_wait==.5
print(json.dumps({'lecture_boundary_cases':len(examples),'late_punctuation_without_third_pass':True,
 'synthetic_first_submission_before':before,'synthetic_first_submission_after':after,
 'same_asr_calls':calls_after,'large_rewrite_guard':True,'configurable_confirmation_wait':True}))
