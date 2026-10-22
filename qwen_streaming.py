"""Bounded near-real-time utterances; no invented word alignment or NN fallback."""
from collections import deque
import re
import numpy as np
from streaming import Vad,quiet_gain
from audio_gain import DynamicGain


class QwenRecognizer:
    def __init__(self,engine,emit,quiet=True,interval=1.5,segmentation='balanced',agc=None,gain_provider=None):
        self.engine=engine;self.emit=emit;self.quiet=quiet;self.vad=Vad()
        self.agc=DynamicGain(agc);self.gain_provider=gain_provider;self._last_config=agc
        self.clock=0;self.base=0;self.size=0;self.active=False;self.silence=0;self.speech_run=0
        self.pre=deque(maxlen=16);self.storage=np.empty(engine.max_samples+160000,np.float32)
        self.last_infer=0;self.last_preview='';self.latest=None;self.latest_size=0
        self.interval_samples=int(max(3.,interval)*16000)
        self.silence_limit=int({'fast':.7,'balanced':1.0,'sentence':1.3}.get(segmentation,1.)*16000)
        self.hard_limit=12*16000
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
        if self.active:self.silence=0 if speech else self.silence+len(block)
        return probability
    def append(self,block):
        if self.size+len(block)>len(self.storage):raise RuntimeError('音频积压超过窗口，停止以避免丢音')
        self.storage[self.size:self.size+len(block)]=block;self.size+=len(block)
    def poll(self,stop=False):
        if not self.active:return
        final=stop or self.silence>=self.silence_limit or self.size>=self.hard_limit
        due=self.size>=32000 and self.clock-self.last_infer>=self.interval_samples
        if not final and not due:return
        if self.size<4000:
            if final:self.reset()
            return
        if self.size>self.engine.max_samples:raise RuntimeError('Qwen 音频超过 NPU 窗口，未回退其他设备')
        # Silence needs no additional decoder pass if the speech was already
        # recognized in a preview; trailing ambience is not new linguistic input.
        reusable=final and self.latest is not None and self.size-self.latest_size<=self.silence
        result=self.latest if reusable else self.engine.infer(self.storage[:self.size])
        self.latest=result;self.latest_size=self.size;self.last_infer=self.clock
        if result['text']!=self.last_preview:
            self.last_preview=result['text'];self.emit('partial',{'text':self.last_preview})
        if not reusable:self.emit('metrics',dict(infer_ms=result['infer_ms'],cpu_ms=result.get('cpu_ms',0),
            audio_seconds=self.size/16000,source_language=result.get('language','auto'),**self.agc.metrics()))
        if final:
            text=result['text'].strip()
            if text:
                # Sentence times are explicitly approximate. Qwen has no word
                # timestamps here, so acoustic time is not used for deduplication.
                pieces=[piece.strip() for piece in re.split(r'(?<=[。！？!?])|(?<=\.)\s+(?=[A-Z])',text) if piece.strip()]
                total=max(1,sum(len(piece) for piece in pieces));offset=0
                end=max(self.base+4000,self.clock-self.silence)
                for piece in pieces:
                    start=self.base+(end-self.base)*offset/total;offset+=len(piece)
                    finish=self.base+(end-self.base)*offset/total
                    self.emit('committed',{'text':piece,'audio_start_ms':int(start/16),'audio_end_ms':int(finish/16),
                        'approximate_timestamps':True,'source_language':result.get('language','auto'),'asr_model':self.engine.model_id})
            self.reset()
    def reset(self):
        self.size=0;self.active=False;self.silence=0;self.speech_run=0;self.latest=None;self.latest_size=0
        self.pre.clear();self.last_preview='';self.emit('partial',{'text':''})
