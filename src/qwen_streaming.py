"""Bounded near-real-time utterances; no invented word alignment or NN fallback."""
from collections import deque
import math
import time
import numpy as np
from streaming import Vad,quiet_gain
from audio_gain import DynamicGain
from segmentation import TextAssembler,endpoint_samples
from text_rules import join_text,normalized
from text_agreement import TextAgreement,UNITS
from final_delivery import FinalDeliveryGate,forced_delivery

_PREVIEW_DUTY=.35  # scheduling target only; backlog/finalization can exceed it
_FIRST_AUDIO_SECONDS=3.
_IDLE_COST_RESET_SECONDS=1.
_UI_MIN_INTERVAL_SECONDS=.5
_UI_DEFAULT_INTERVAL_SECONDS=1.5
_MAX_INTERVAL_SECONDS=999999.
_MAX_INFERENCE_COST_SECONDS=3600.

def _finite_positive(value,maximum=None):
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return 0.
    if not math.isfinite(value) or value<=0:return 0.
    return min(value,maximum) if maximum is not None else value

def _requested_samples(interval):
    try:value=float(interval)
    except (TypeError,ValueError,OverflowError):value=_UI_DEFAULT_INTERVAL_SECONDS
    if not math.isfinite(value):value=_UI_DEFAULT_INTERVAL_SECONDS
    value=min(_MAX_INTERVAL_SECONDS,max(_UI_MIN_INTERVAL_SECONDS,value))
    return int(value*16000)

def _measured_inference_cost(begun,result):
    try:elapsed=time.perf_counter()-begun
    except Exception:elapsed=0.
    reported=_finite_positive(result.get('infer_ms',0),_MAX_INFERENCE_COST_SECONDS*1000)/1000 if isinstance(result,dict) else 0.
    return max(_finite_positive(elapsed,_MAX_INFERENCE_COST_SECONDS),reported)

def _adaptive_refresh_samples(requested,inference_cost):
    cost=_finite_positive(inference_cost,_MAX_INFERENCE_COST_SECONDS)
    return max(int(requested),int(cost/_PREVIEW_DUTY*16000))


class QwenRecognizer:
    def __init__(self,engine,emit,quiet=True,interval=1.5,segmentation='balanced',agc=None,gain_provider=None,progressive=False,progressive_limit=2,progressive_interval=1.,final_delivery_wait=4.,final_delivery_words=35,final_delivery_enabled=True,final_delivery_clock=None):
        self.engine=engine;self.emit=emit;self.quiet=quiet;self.vad=Vad()
        from progressive_captions import StablePrefix
        self.prefix=StablePrefix(progressive_limit,progressive_interval) if progressive else None
        self.agc=DynamicGain(agc);self.gain_provider=gain_provider;self._last_config=agc
        self.clock=0;self.base=0;self.size=0;self.active=False;self.silence=0;self.speech_run=0
        self.pre=deque(maxlen=16);self.storage=np.empty(engine.max_samples+160000,np.float32)
        self.last_infer=0;self.last_preview='';self.latest=None;self.latest_size=0
        self.interval_samples=_requested_samples(interval)
        self.first_audio_samples=int(_FIRST_AUDIO_SECONDS*16000)
        self.inference_cost=0.
        self.first_infer_done=False
        self.last_infer_size=0
        self.inactive_gap=0.
        self.silence_limit=endpoint_samples(segmentation,quiet)
        self.hard_limit=min(12*16000,engine.max_samples-16000)
        self.segmenter=TextAssembler(segmentation);self.last_voice_clock=0
        self.agreement=TextAgreement();self.text_end=0.
        self.delivery=FinalDeliveryGate(final_delivery_wait,final_delivery_words,final_delivery_enabled,final_delivery_clock)
        # Latest agreement tail. Text-only models can expose all recognized
        # text here while TextAssembler is still empty.
        self.gate_preview=''
    def push(self,block):
        probability=self.vad(block*quiet_gain(block,maximum=64.)) if self.quiet else self.vad(block)
        speech=probability>=(.20 if self.active else .32)
        if self.gain_provider:
            config=self.gain_provider()
            if config is not self._last_config:self.agc.configure(config);self._last_config=config
        normalized=self.agc.process(block,speech)
        self.speech_run=self.speech_run+1 if speech else 0
        # Existing VAD speech detection arms the wall clock at voice onset.
        if speech and not self.delivery.speech_active:self.delivery.voice_started()
        if speech and not self.active:
            self.inactive_gap=max(self.inactive_gap,(self.clock-self.last_voice_clock)/16000)
        if not self.active:
            if self.speech_run>=2:
                # A real idle gap starts a genuinely new utterance: inherited
                # cost must not delay its first 3 s context. This is bounded
                # audio-clock input, not a timer or extra inference.
                if self.inactive_gap>=_IDLE_COST_RESET_SECONDS:
                    self.inference_cost=0.
                self.active=True;self.base=self.clock-len(self.pre)*512;self.size=0
                for old in self.pre:self.append(old)
                self.pre.clear();self.last_infer=self.base;self.append(normalized)
                self.gate_preview='';self.first_infer_done=False;self.last_infer_size=0
                self.inactive_gap=0.
            else:self.pre.append(normalized)
        else:self.append(normalized)
        self.clock+=len(block)
        if speech:self.last_voice_clock=self.clock
        if self.active:self.silence=0 if speech else self.silence+len(block)
        return probability
    def append(self,block):
        if self.size+len(block)>len(self.storage):raise RuntimeError('音频积压超过窗口，停止以避免丢音')
        self.storage[self.size:self.size+len(block)]=block;self.size+=len(block)
    def commit(self,event):
        self.emit('committed',dict(event,asr_model=self.engine.model_id))
        # Only a real final segment resets the monotonic delivery clock.
        self.delivery.note_committed()
    def pending_text(self):
        return join_text(self.segmenter.text,self.gate_preview.strip())
    def force_pending(self,trigger):
        """Promote a bounded prefix from existing text; never call inference."""
        held=self.segmenter.text or ''
        preview=self.gate_preview or ''
        combined=join_text(held,preview.strip())
        if not combined:return False
        language=self.segmenter.language or (self.latest or {}).get('language','auto')
        part=forced_delivery(combined,language,self.delivery.words)
        if not part:return False
        audio_end=max(self.base+4000,self.clock-self.silence)/16000
        audio_start=self.segmenter.start if held else max(self.text_end,self.base/16000)
        audio_start=min(max(self.base/16000,audio_start),max(self.base/16000,audio_end))
        cut=len(part.text);total=max(1,len(combined))
        finish=audio_start+max(0.,audio_end-audio_start)*cut/total
        finish=min(audio_end,max(audio_start+.001,finish))
        event={'text':part.text,'audio_start_ms':int(audio_start*1000),
            'audio_end_ms':max(int(audio_start*1000)+1,int(finish*1000)),
            'approximate_timestamps':True,'source_language':language or 'auto',
            'boundary_reason':'final_delivery_'+trigger,'uncertain':True,
            'sentence_id':self.segmenter.sequence}
        self.segmenter.sequence+=1
        self.text_end=finish
        if held and len(part.text)<=len(held):
            # The cut stays inside already-confirmed text; keep the remainder
            # in the assembler and leave the agreement tail untouched.
            self.segmenter.text=held[len(part.text):].lstrip()
            self.gate_preview=preview
        else:
            if held and part.text.startswith(held):
                forced_preview=part.text[len(held):].lstrip()
            else:
                forced_preview=part.text
            self.segmenter.text='';self.gate_preview=part.tail
            # Advance the agreement cursor over the promoted preview prefix.
            # Later rolling revisions can no longer publish those words again.
            keys=[normalized(match.group()) for match in UNITS.finditer(forced_preview)]
            if keys and not (self.agreement.confirmed and self.agreement.confirmed[-len(keys):]==keys):
                self.agreement.confirmed.extend(keys)
        self.segmenter.start=finish;self.segmenter.end=audio_end
        self.commit(event)
        return True
    def refresh_samples(self):
        # Requested cadence is a floor; measured full-inference wall time backs
        # off subsequent previews. Cost survives only uninterrupted hard-window
        # continuation; natural stop/endpoint and a real idle gap clear it.
        return _adaptive_refresh_samples(self.interval_samples,self.inference_cost)
    def poll(self,stop=False):
        if not self.active:
            for event in self.segmenter.update(now=self.clock/16000,idle=(self.clock-self.last_voice_clock)/16000,final=stop):
                self.commit(event)
            if stop or self.speech_run==0:self.delivery.reset()
            self.gate_preview=''
            self.preview('')
            return
        final=stop or self.silence>=self.silence_limit or self.size>=self.hard_limit
        # The first context needs enough audio for the autoregressive decoder.
        # This gate applies only until the first decode of this utterance; it is
        # deliberately not a perpetual refresh floor and never blocks an
        # endpoint/stop finalization below.
        first_gate=self.size>=self.first_audio_samples if not self.first_infer_done else True
        due=(first_gate and self.clock-self.last_infer>=self.refresh_samples()
            and self.silence<2560 and self.size>self.last_infer_size)
        # Flush an already-recognized tail on an ordinary poll, without a new
        # generation and without starting inference.
        trigger=self.delivery.trigger(self.pending_text())
        if trigger and not stop:
            self.force_pending(trigger);self.preview(self.gate_preview)
            return
        if not final and not due:return
        if self.size<4000:
            if final:
                self.reset()
                if stop:self.poll(stop=True)
            return
        if self.size>self.engine.max_samples:raise RuntimeError('Qwen 音频超过 NPU 窗口，未回退其他设备')
        # Same audio cannot produce a better final result than the one already
        # stored; never decode it again. This also covers stop while the last
        # preview is still the newest available result.
        same_audio=self.latest is not None and self.size==self.last_infer_size
        # Silence needs no additional decoder pass if the speech was already
        # recognized in a preview; trailing ambience is not new linguistic input.
        reusable=final and (same_audio or (self.latest is not None
            and self.last_infer>=self.last_voice_clock+2560))
        begun=0. if reusable else time.perf_counter()
        result=self.latest if reusable else self.engine.infer(self.storage[:self.size])
        if not reusable:
            # The wall clock wraps the whole call, including autoregressive
            # decode. A reported inference time is a fallback only.
            self.inference_cost=max(_measured_inference_cost(begun,result),self.inference_cost*.8)
            self.last_infer_size=self.size
            self.first_infer_done=True
        self.latest=result;self.latest_size=self.size;self.last_infer=self.clock
        confirmed,preview,lo,hi=self.agreement.update(result['text'],final=final,language=result.get('language','auto'))
        if not reusable:self.emit('metrics',dict(
            infer_ms=_finite_positive(result.get('infer_ms',0),_MAX_INFERENCE_COST_SECONDS*1000),
            cpu_ms=_finite_positive(result.get('cpu_ms',0),_MAX_INFERENCE_COST_SECONDS*1000),
            audio_seconds=self.size/16000,source_language=result.get('language','auto'),
            refresh_interval_ms=self.refresh_samples()/16,**self.agc.metrics()))
        if confirmed or stop:
            end=max(self.base+4000,self.clock-self.silence)/16000
            duration=end-self.base/16000;length=max(1,len(result['text']))
            start=max(self.text_end,self.base/16000+duration*lo/length)
            finish=max(start,self.base/16000+duration*hi/length);self.text_end=finish
            for event in self.segmenter.update(confirmed,start,finish,now=self.clock/16000,
                    idle=self.silence/16000,final=stop,language=result.get('language','auto'),
                    window_limit=final and not stop and self.silence<self.silence_limit):
                self.commit(event)
        self.gate_preview=preview
        trigger=self.delivery.trigger(self.pending_text())
        if trigger:
            self.force_pending(trigger)
            preview=self.gate_preview
        if self.prefix and not stop:
            start=self.segmenter.start if self.segmenter.text else max(self.text_end,self.base/16000)
            value=self.prefix.offer(join_text(self.segmenter.text,preview),self.segmenter.sequence,
                self.clock/16000,start,result.get('language','auto'))
            if value:self.emit('provisional',value)
        self.preview(preview)
        if final:
            # Do not discard a word-gated remaining tail at the endpoint.
            for _ in range(64):
                trigger=self.delivery.trigger(self.pending_text())
                if not trigger or not self.force_pending(trigger):break
            # Only an uninterrupted 12 s hard-window continuation keeps the
            # measured cost. Natural idle/endpoint/stop starts a new utterance
            # whose first decode is gated by audio context alone.
            hard_continuation=(not stop and self.size>=self.hard_limit
                and self.silence<self.silence_limit)
            self.reset(keep_cost=hard_continuation)
    def preview(self,text):
        text=join_text(self.segmenter.text,text.strip())
        if text!=self.last_preview:
            self.last_preview=text;self.emit('partial',{'text':text})
    def reset(self,keep_cost=False):
        self.size=0;self.active=False;self.silence=0;self.speech_run=0;self.latest=None;self.latest_size=0
        # Retained cost is only for uninterrupted hard-window continuation or
        # until a real idle gap is observed. Natural stop/endpoint resets clear
        # it so a genuinely new utterance starts from its requested cadence.
        if not keep_cost:self.inference_cost=0.
        self.first_infer_done=False;self.last_infer_size=0;self.inactive_gap=0.
        self.agreement=TextAgreement()
        self.pre.clear();self.preview('')
        self.delivery.reset();self.gate_preview=''
