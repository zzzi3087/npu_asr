"""Silero VAD, continuous resampling, and time-constrained window alignment."""
import re
from collections import deque
from pathlib import Path
import numpy as np
import onnxruntime as ort
from segmentation import SentenceAssembler,endpoint_samples
from text_rules import normalized,SMALL_NUMBERS,TENS
from audio_gain import DynamicGain
from model_manager import model_file


class Vad:
    def __init__(self):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        self.model = ort.InferenceSession(str(model_file('silero-vad','silero_vad.onnx')),
                                          options, providers=['CPUExecutionProvider'])
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, 64), np.float32)
        self.input = np.empty((1,576),np.float32)
        self.feeds={'input':self.input,'state':self.state,'sr':np.array(16000,np.int64)}

    def __call__(self, block):
        self.input[:,:64]=self.context;self.input[:,64:]=block.reshape(1,512)
        self.feeds['state']=self.state
        output, self.state = self.model.run(None,self.feeds)
        self.context[:]=self.input[:,-64:]
        return float(output.reshape(-1)[0])


def number_groups(words):
    """Align twenty three / twenty-three / 23 as one timed unit.

    Keep the actual model spelling for display. Do not commit half a compound
    number: a later numeral token could otherwise block prefix confirmation.
    """
    grouped = []
    i = 0
    while i < len(words):
        word, stamp = words[i]
        bare = word.lower().strip('.,!?;:')
        if bare in TENS and not re.search(r'[.!?,;:]$', word) and i+1 < len(words):
            following = words[i+1][0].lower().strip('.,!?;:')
            if following in SMALL_NUMBERS and 0 < SMALL_NUMBERS[following] < 10:
                word += ' ' + words[i+1][0]
                i += 1
        grouped.append((word, stamp))
        i += 1
    return grouped


def quiet_gain(audio, maximum=32., target=.025):
    """One bounded linear gain; preserve SNR and avoid clipping/noise gating."""
    rms = float(np.sqrt(np.mean(audio * audio)))
    peak = float(np.max(np.abs(audio)))
    if rms < 1e-9:
        return 1.
    return max(1., min(maximum, target / rms, .9 / max(peak, 1e-9)))


class Timeline:
    def __init__(self):
        self.confirmed = []
        self.previous = []
        self.last_forced = False

    def cut(self, current):
        if not self.confirmed or not current:
            return 0
        last = self.confirmed[-1][1]
        if current[0][1] > last + .7:
            return 0
        reference = [(w,t) for w,t in self.confirmed[-12:] if t >= current[0][1]-.7]
        if not reference:
            return 0
        first = next((i for i,(_,t) in enumerate(current) if t >= reference[0][1]-.7), len(current))
        stop = next((i for i,(_,t) in enumerate(current) if t > last+.7), len(current))
        candidates = current[first:stop]
        if not candidates:
            return first
        # Semi-global timed sequence alignment. Substitution at the same audio
        # time consumes an already committed word even if ASR revised its text.
        # The old suffix-only match left that revision at the head forever.
        cost = [0.] * (len(candidates)+1)
        for word, stamp in reference:
            row = [cost[0]+1.5]
            for j,(other, other_stamp) in enumerate(candidates,1):
                dt = abs(stamp-other_stamp)
                substitution = (0. if normalized(word)==normalized(other) else .75) + dt*1.5
                if dt > .7:
                    substitution = 10.
                row.append(min(cost[j-1]+substitution, cost[j]+1.5, row[-1]+1.5))
            cost = row
        return first + min(range(len(cost)), key=lambda j: (cost[j],-j))

    def update(self, words, start, end, final=False):
        current = [(word, start + stamp) for word, stamp in number_groups(words)]
        remaining = current[self.cut(current):]
        stable = 0
        self.last_forced = False
        if final:
            stable = len(remaining)
        else:
            previous = self.previous[self.cut(self.previous):]
            for (word, stamp), (old, old_stamp) in zip(remaining, previous):
                if normalized(word) != normalized(old) or abs(stamp-old_stamp) > .45 or stamp > end-.45:
                    break
                stable += 1
            stable = min(stable, max(0, len(remaining)-1))
            if remaining and remaining[0][1] < end-6.:
                deadline = sum(stamp <= end-4. for _,stamp in remaining)
                if deadline > stable:
                    stable = deadline
                    self.last_forced = True
        new = remaining[:stable]
        self.confirmed.extend(new)
        self.confirmed = self.confirmed[-100:]
        self.previous = current
        return new, remaining[stable:]


class Recognizer:
    def __init__(self, engine, emit, quiet=True, interval=1.5, segmentation='balanced',agc=None,gain_provider=None,progressive=False):
        self.engine = engine
        from progressive_captions import StablePrefix
        self.prefix=StablePrefix() if progressive else None
        self.agc=DynamicGain(agc)
        self.gain_provider=gain_provider
        self._last_config=agc
        self.emit = emit
        self.vad = Vad()
        self.quiet = quiet
        self.interval_samples = int(interval * 16000)
        self.silence_limit=endpoint_samples(segmentation,quiet)
        self.latest=None
        self.gain = 1.
        self.speech_run = 0
        self.voice_samples = 0
        self.clock = 0
        self.pre = deque(maxlen=16 if quiet else 8)
        self.storage = np.empty(engine.max_samples + 160000, np.float32)
        self.size = 0
        self.base = 0
        self.active = False
        self.silence = 0
        self.last_infer = 0
        self.timeline = Timeline()
        self.segmenter = SentenceAssembler(segmentation)
        self.pauses = deque(maxlen=128)
        self.pause_start = None
        self.last_voice_clock = 0
        self.last_preview = ''

    @property
    def audio(self):
        return self.storage[:self.size]

    def append(self, block):
        if self.size + len(block) > len(self.storage):
            raise RuntimeError('录音积压超过缓冲容量，停止以避免静默丢音。')
        self.storage[self.size:self.size+len(block)] = block
        self.size += len(block)

    def push(self, block):
        if self.quiet:
            desired = quiet_gain(block)
            # Smooth increases in gain; reduce immediately to prevent clipping.
            self.gain = min(desired, self.gain * .75 + desired * .25)
            probability = self.vad(block * self.gain)
            speech = probability >= (.20 if self.active else .32)
        else:
            probability = self.vad(block)
            speech = probability >= .4
        self.speech_run = self.speech_run+1 if speech else 0
        if self.gain_provider:
            config=self.gain_provider()
            if config is not self._last_config:self.agc.configure(config);self._last_config=config
        block=self.agc.process(block,speech)
        if speech:
            if self.pause_start is not None and self.clock-self.pause_start>=5120:
                self.pauses.append((self.pause_start/16000,self.clock/16000))
            self.pause_start = None
            self.last_voice_clock = self.clock+len(block)
        elif self.pause_start is None:
            self.pause_start = self.clock
        if not self.active:
            if self.speech_run >= (2 if self.quiet else 1):
                self.active = True
                self.size = 0
                for previous in self.pre:
                    self.append(previous)
                self.base = self.clock - self.size
                self.pre.clear()
                self.append(block)
                self.last_infer = self.base
            else:
                self.pre.append(block.copy())
        else:
            self.append(block)
        self.clock += len(block)
        if self.active:
            self.silence = 0 if speech else self.silence + len(block)
            if speech:
                self.voice_samples += len(block)
        return probability

    def poll(self, stop=False):
        if not self.active:
            events = self.segmenter.update([],None,self.clock/16000,final=stop,
                idle=(self.clock-self.last_voice_clock)/16000,pauses=self.pauses)
            for event in events:self.emit('committed',event)
            self.preview([])
            return
        final = stop or self.silence >= self.silence_limit
        # Once a pause starts, wait for its endpoint rather than decoding the
        # same words once for a preview and again a few hundred ms later.
        due = self.size >= 16000 and self.clock-self.last_infer >= self.interval_samples and self.silence<2560
        if not final and not due:
            return
        if len(self.audio) > self.engine.max_samples:
            excess = len(self.audio) - self.engine.max_samples
            # Refuse to silently lose speech if inference cannot keep pace.
            safe = self.timeline.confirmed[-1][1] if self.timeline.confirmed else -1
            if (self.base+excess)/16000 > safe - .25:
                raise RuntimeError('识别积压超过窗口，已停止以避免静默丢音；请降低预览频率或检查设备。')
            self.storage[:self.size-excess] = self.storage[excess:self.size]
            self.size -= excess
            self.base += excess
            self.latest=None
        if len(self.audio) < 4000 or self.voice_samples < (1536 if self.quiet else 512):
            if final:
                self.reset()
                if stop:
                    for event in self.segmenter.update([],None,self.clock/16000,final=True):
                        self.emit('committed',event)
                    self.preview([])
            return
        reusable=final and self.latest is not None and self.last_infer>=self.last_voice_clock+2560
        result=self.latest if reusable else self.engine.infer(self.audio)
        self.latest=result
        self.last_infer = self.clock
        new, preview = self.timeline.update(result['words'], self.base/16000, self.clock/16000, final)
        hypothesis = [(word,self.base/16000+stamp) for word,stamp in number_groups(result['words'])]
        events = self.segmenter.update(new,hypothesis,self.clock/16000,uncertain=self.timeline.last_forced,
            final=stop,acoustic_end=final,idle=(self.clock-self.last_voice_clock)/16000,pauses=self.pauses)
        for event in events:self.emit('committed',event)
        if self.prefix and self.segmenter.words and not stop:
            value=self.prefix.offer(self.segmenter.text,self.segmenter.sequence,self.clock/16000,
                self.segmenter.words[0].stamp,'English')
            if value:self.emit('provisional',value)
        self.preview(preview)
        if not reusable:
            self.emit('metrics', {'infer_ms': result['infer_ms'], 'audio_seconds': len(self.audio)/16000,
                                'cpu_ms': result.get('cpu_ms', 0), **self.agc.metrics()})
        if final:self.reset()
        elif self.size>=192000 and self.timeline.confirmed:
            # Retain at least six seconds and 0.8 s before the latest confirmed
            # word. Only confirmed audio can leave the inference window.
            keep=min(self.timeline.confirmed[-1][1]-.8,self.clock/16000-6.)
            trim=max(0,int(keep*16000)-self.base)
            if trim>=64000:
                self.storage[:self.size-trim]=self.storage[trim:self.size]
                self.size-=trim;self.base+=trim;self.latest=None

    def preview(self,tail):
        text=' '.join(part for part in (self.segmenter.text,' '.join(word for word,_ in tail)) if part)
        if text!=self.last_preview:
            self.last_preview=text
            self.emit('partial',{'text':text})

    def reset(self):
        self.active = False
        self.pre.clear()
        # The finalized trailing silence has already been consumed. Do not feed
        # it to another utterance or count it twice on the sample timeline.
        self.size = 0
        self.voice_samples = 0
        self.speech_run = 0
        self.silence = 0
        self.latest=None
        self.timeline = Timeline()
