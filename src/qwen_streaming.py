"""Bounded near-real-time utterances; no invented word alignment or NN fallback."""
from collections import deque
import numpy as np
from streaming import Vad,quiet_gain
from audio_gain import DynamicGain
from segmentation import TextAssembler,endpoint_samples
from text_rules import join_text
from text_agreement import TextAgreement


class QwenRecognizer:
    def __init__(self,engine,emit,quiet=True,interval=1.5,segmentation='balanced',agc=None,gain_provider=None):
        self.engine=engine;self.emit=emit;self.quiet=quiet;self.vad=Vad()
        self.agc=DynamicGain(agc);self.gain_provider=gain_provider;self._last_config=agc
        self.clock=0;self.base=0;self.size=0;self.active=False;self.silence=0;self.speech_run=0
        self.pre=deque(maxlen=16);self.storage=np.empty(engine.max_samples+160000,np.float32)
        self.last_infer=0;self.last_preview='';self.latest=None;self.latest_size=0
        self.interval_samples=int(max(3.,interval)*16000)
        self.silence_limit=endpoint_samples(segmentation,quiet)
        self.hard_limit=min(12*16000,engine.max_samples-16000)
        self.segmenter=TextAssembler(segmentation);self.last_voice_clock=0
        self.agreement=TextAgreement();self.text_end=0.
    def push(self,block):
        probability=self.vad(block*quiet_gain(block,maximum=64.)) if self.quiet else self.vad(block)
        speech=probability>=(.20 if self.active else .32)
        if self.gain_provider:
            config=self.gain_provider()
            if config is not self._last_config:self.agc.configure(config);self._last_config=config
        normalized=self.agc.process(block,speech)
        self.speech_run=self.speech_run+1 if speech else 0
        if not self.active:
            if self.speech_run>=2:
                self.active=True;self.base=self.clock-len(self.pre)*512;self.size=0
                for old in self.pre:self.append(old)
                self.pre.clear();self.last_infer=self.base;self.append(normalized)
            else:self.pre.append(normalized)
        else:self.append(normalized)
        self.clock+=len(block)
        if speech:self.last_voice_clock=self.clock
        if self.active:self.silence=0 if speech else self.silence+len(block)
        return probability
    def append(self,block):
        if self.size+len(block)>len(self.storage):raise RuntimeError('音频积压超过窗口，停止以避免丢音')
        self.storage[self.size:self.size+len(block)]=block;self.size+=len(block)
    def poll(self,stop=False):
        if not self.active:
            for event in self.segmenter.update(now=self.clock/16000,idle=(self.clock-self.last_voice_clock)/16000,final=stop):
                self.emit('committed',dict(event,asr_model=self.engine.model_id))
            self.preview('')
            return
        final=stop or self.silence>=self.silence_limit or self.size>=self.hard_limit
        due=self.size>=32000 and self.clock-self.last_infer>=self.interval_samples and self.silence<2560
        if not final and not due:return
        if self.size<4000:
            if final:
                self.reset()
                if stop:self.poll(stop=True)
            return
        if self.size>self.engine.max_samples:raise RuntimeError('Qwen 音频超过 NPU 窗口，未回退其他设备')
        # Silence needs no additional decoder pass if the speech was already
        # recognized in a preview; trailing ambience is not new linguistic input.
        reusable=final and self.latest is not None and self.last_infer>=self.last_voice_clock+2560
        result=self.latest if reusable else self.engine.infer(self.storage[:self.size])
        self.latest=result;self.latest_size=self.size;self.last_infer=self.clock
        confirmed,preview,lo,hi=self.agreement.update(result['text'],final=final)
        if not reusable:self.emit('metrics',dict(infer_ms=result['infer_ms'],cpu_ms=result.get('cpu_ms',0),
            audio_seconds=self.size/16000,source_language=result.get('language','auto'),**self.agc.metrics()))
        if confirmed or stop:
            end=max(self.base+4000,self.clock-self.silence)/16000
            duration=end-self.base/16000;length=max(1,len(result['text']))
            start=max(self.text_end,self.base/16000+duration*lo/length)
            finish=max(start,self.base/16000+duration*hi/length);self.text_end=finish
            for event in self.segmenter.update(confirmed,start,finish,now=self.clock/16000,
                    idle=self.silence/16000,final=stop,language=result.get('language','auto'),
                    window_limit=final and not stop and self.silence<self.silence_limit):
                self.emit('committed',dict(event,asr_model=self.engine.model_id))
        self.preview(preview)
        if final:self.reset()
    def preview(self,text):
        text=join_text(self.segmenter.text,text.strip())
        if text!=self.last_preview:
            self.last_preview=text;self.emit('partial',{'text':text})
    def reset(self):
        self.size=0;self.active=False;self.silence=0;self.speech_run=0;self.latest=None;self.latest_size=0
        self.agreement=TextAgreement()
        self.pre.clear();self.preview('')
