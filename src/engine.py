"""Parakeet TDT greedy decoder. ASR networks are bound strictly to NPU.

Model architecture and port conventions follow FluidInference/eddy-audio
(Apache-2.0); model weights are CC-BY-4.0, NVIDIA / FluidInference.
Each call decodes an independent window and resets predictor state.
"""
import json
import time
from pathlib import Path
import numpy as np
import openvino as ov
from download_models import MODEL_DIR,DEFAULT_MODEL_DIR,model_directory
from frontend import MelFrontend
from model_io import read_ir


def read_vocabulary(path,model_id):
    raw=json.loads(Path(path).read_text('utf-8'))
    if model_id=='parakeet-v3':
        blank=int(raw['blank_id']);vocab={str(i):piece for i,piece in enumerate(raw['id_to_token'])}
        if blank!=8192:raise ValueError('Parakeet v3 词表大小不匹配')
    else:
        # The pinned v2 JSON includes seven extra entries beyond its 1024
        # trained labels. They are not additional logits or a shifted blank.
        blank=1024;vocab={str(i):raw[str(i)] for i in range(blank)}
    if set(vocab)!={str(i) for i in range(blank)}:raise ValueError('Parakeet 词表编号不完整')
    return vocab,blank


class Engine:
    def __init__(self, status=print, device='NPU', feature_backend='numpy',model_dir=None,model_id='parakeet',language='auto'):
        if device!='NPU':raise ValueError('语音转录神经网络仅允许 NPU 执行')
        if feature_backend not in ('numpy','gpu','openvino'):raise ValueError('不支持的音频特征计算设备')
        self.core = ov.Core()
        if device == 'NPU' and 'NPU' not in self.core.available_devices:
            raise RuntimeError('未发现 NPU；请检查 Intel NPU 驱动。不会回退 CPU。')
        self.device = device
        from model_manager import model_dir as resolve_model_dir
        self.model_dir=Path(model_dir) if model_dir is not None else resolve_model_dir(model_id)
        self.model_id=model_id;self.language='English' if model_id=='parakeet' else language
        self.vocab,self.blank=read_vocabulary(self.model_dir/'parakeet_vocab.json',model_id)
        self.labels=self.blank+1
        self.pieces=[self.vocab[str(i)] for i in range(self.blank)]
        self.special={i for i,piece in enumerate(self.pieces) if piece.startswith('<') and piece.endswith('>')}
        self.devices = {}
        self.models = {}
        self.requests = {}
        # Keep the cache stable when weights move into the program directory.
        cache = DEFAULT_MODEL_DIR.parent.parent / ('cache-' + ov.__version__.split('-')[0])
        cache.mkdir(parents=True,exist_ok=True)
        self.core.set_property({'CACHE_DIR': str(cache)})
        self.frontend = None
        if feature_backend == 'numpy':
            if model_id=='parakeet-v3':
                from frontend import V3MelFrontend
                self.frontend=V3MelFrontend(self.core,self.model_dir/'parakeet_melspectogram.xml')
            else:self.frontend = MelFrontend(self.core, self.model_dir / 'parakeet_melspectogram.xml')
            self.devices['melspectogram'] = ['CPU']
            status('特征处理就绪：CPU / NumPy FFT + 稀疏 Mel')
        for component in ([] if self.frontend else ['melspectogram']) + ['encoder', 'decoder', 'joint']:
            target = ('GPU' if feature_backend=='gpu' else 'CPU') if component == 'melspectogram' else device
            status(f'加载 {component} → {target}（首次编译可能需要数分钟）')
            start = time.perf_counter()
            model = read_ir(self.core,self.model_dir / f'parakeet_{component}.xml')
            if target == 'NPU':
                # Canonicalize negative LogSoftmax axes, matching eddy's workaround.
                for op in model.get_ordered_ops():
                    if op.get_type_name() == 'LogSoftmax':
                        axis = op.get_attributes().get('axis', 0)
                        rank = op.input_value(0).get_partial_shape().rank
                        if axis < 0 and rank.is_static:
                            op.set_attribute('axis', axis + rank.get_length())
                model.validate_nodes_and_infer_types()
            config = {'PERFORMANCE_HINT': 'LATENCY'}
            if target == 'CPU':
                config.update(INFERENCE_NUM_THREADS=1, NUM_STREAMS=1, ENABLE_CPU_PINNING=False)
            elif target=='GPU':
                # FP16 changes low-energy normalization too much. Only this
                # deterministic DSP graph may use GPU; ASR stays strictly NPU.
                config['INFERENCE_PRECISION_HINT']=ov.Type.f32
            compiled = self.core.compile_model(model, target, config)
            actual = compiled.get_property('EXECUTION_DEVICES')
            actual = [actual] if isinstance(actual, str) else list(actual)
            if target == 'NPU' and (not actual or not all(d.startswith('NPU') for d in actual)):
                raise RuntimeError(f'{component} 执行设备不正确：{actual}')
            self.devices[component] = actual
            self.models[component] = compiled
            self.requests[component] = compiled.create_infer_request()
            status(f'{component} 就绪：{actual}，{time.perf_counter()-start:.1f}s')
        if int(np.prod(self.models['joint'].output(0).shape))!=self.labels+5:raise ValueError('Parakeet 词表与 joint 输出不匹配')
        self.frames = int(self.models['encoder'].input('melspectogram').shape[-1])
        self.max_samples = (self.frames-1 if model_id=='parakeet-v3' else self.frames) * 160
        # Reuse model-owned buffers. Rebinding dictionaries and copying every
        # decoder/joint input on every token was unnecessary host CPU work.
        dec = self.requests['decoder']
        self.targets = dec.get_tensor('targets').data
        self.hidden = dec.get_tensor('h_in').data
        self.cell = dec.get_tensor('c_in').data
        self.joint_enc = self.requests['joint'].get_tensor('encoder_outputs').data
        self.joint_dec = self.requests['joint'].get_tensor('decoder_outputs').data
        self.features = self.requests['encoder'].get_tensor('melspectogram').data
        self.feature_length = self.requests['encoder'].get_tensor('melspectogram_length').data
        self.phrase_bias=None
        self._bias_signature=((),0.)

    def configure_terms(self,phrases=(),strength=.6):
        signature=(tuple(phrases),float(strength))
        if signature==self._bias_signature:return
        if phrases and strength:
            from phrase_bias import PhraseBias
            tokenizer=self.model_dir/('tokenizer.json' if self.model_id=='parakeet-v3' else 'tokenizer.model')
            self.phrase_bias=PhraseBias(phrases,self.vocab,tokenizer,strength)
        else:self.phrase_bias=None
        self._bias_signature=signature

    def infer(self, audio):
        start = time.perf_counter()
        cpu_start = time.process_time()
        stages = {}
        pcm = np.asarray(audio, dtype=np.float32).reshape(1, -1)
        audio_seconds=pcm.shape[1]/16000
        if pcm.shape[1] > self.max_samples:
            raise ValueError('Audio exceeds model window')
        if self.frontend:
            mel = self.frontend(pcm)
            length = self.frontend.length(pcm.shape[1]) if hasattr(self.frontend,'length') else pcm.shape[1]//160+1
        else:
            if np.max(np.abs(pcm))<1e-10:
                length=1498 if self.model_id=='parakeet-v3' else pcm.shape[1]//160+1
                mel=np.zeros((1,128,length),np.float32)
            else:
                pre = self.requests['melspectogram']
                input_pcm=pcm
                shape=self.models['melspectogram'].input(0).partial_shape
                if shape.is_static:
                    size=shape[-1].get_length()
                    if pcm.shape[1]>size:raise ValueError('音频超过前处理窗口')
                    input_pcm=np.pad(pcm,((0,0),(0,size-pcm.shape[1])))
                input_length=input_pcm.shape[1] if self.model_id=='parakeet-v3' else pcm.shape[1]
                pre.infer({0: input_pcm, 1: np.array([input_length], np.int32)}, share_inputs=True)
                mel = pre.get_output_tensor(0).data
                length = int(pre.get_output_tensor(1).data[0])
        self.features.fill(0)
        count = min(mel.shape[-1], self.frames)
        self.features[:, :, :count] = mel[:, :, :count]
        self.feature_length[0] = min(length, self.frames)
        stages['features'] = (time.perf_counter()-start)*1000
        enc = self.requests['encoder']
        stage_start = time.perf_counter()
        enc.infer()
        stages['encoder'] = (time.perf_counter()-stage_start)*1000
        encoded = enc.get_tensor('encoder_output').data
        valid = min(int(enc.get_tensor('encoder_output_length').data[0]), encoded.shape[-1])
        dec = self.requests['decoder']
        joint = self.requests['joint']
        self.hidden.fill(0)
        self.cell.fill(0)
        last = self.blank
        cached = False
        tokens = []
        frame = 0
        same_frame = 0
        bias_state=0;bias_changes=0
        stage_start = time.perf_counter()
        # The 10-symbol frame guard bounds decoding without truncating a fast
        # multilingual window at an arbitrary 512-token vocabulary limit.
        while frame < valid:
            if not cached:
                self.targets[0, 0] = last
                dec.infer()
                self.joint_dec[:] = dec.get_output_tensor(0).data
                cached = True
            self.joint_enc[:] = encoded[:, :, frame:frame+1].transpose(0, 2, 1)
            joint.infer()
            logits = joint.get_output_tensor(0).data.reshape(-1)
            token = int(np.argmax(logits[:self.labels]))
            if self.phrase_bias:
                biased,bias_state=self.phrase_bias.choose(logits,bias_state,token)
                bias_changes+=int(biased!=token);token=biased
            duration = int(np.argmax(logits[self.labels:self.labels+5]))
            if token != self.blank:
                tokens.append((token,frame*.08,min((frame+max(1,duration))*.08,audio_seconds)))
                last = token
                self.hidden[:] = dec.get_output_tensor(1).data
                self.cell[:] = dec.get_output_tensor(2).data
                cached = False
            # TDT permits zero-duration nonblank symbols at the same frame.
            if duration == 0:
                same_frame += 1
                if token == self.blank or same_frame >= 10:
                    duration = 1
            if duration:
                frame += duration
                same_frame = 0
        stages['decode'] = (time.perf_counter()-stage_start)*1000
        words = []
        for token, stamp, finish in tokens:
            if token in self.special:continue
            piece = self.pieces[token]
            # A punctuation token can arrive during trailing silence. Preserve
            # its text without extending the last spoken word to that silence.
            lexical=any(char.isalnum() for char in piece)
            if words and not lexical:
                words[-1][0]+=piece.replace('▁',' ');continue
            if stamp>=audio_seconds:continue
            if piece.startswith('▁') or not words:
                words.append([piece.replace('▁', ' '), stamp,finish])
            else:
                words[-1][0] += piece
                words[-1][2]=max(words[-1][2],finish)
        text = ''.join(w[0] for w in words).strip()
        return {'text': text, 'words': [(w.strip(), t) for w, t,_ in words if w.strip()],
                'word_spans':[(w.strip(),t,end) for w,t,end in words if w.strip()],
                'language':self.language,'asr_model':self.model_id,
                'bias_token_changes':bias_changes,
                'cpu_ms': round((time.process_time()-cpu_start)*1000, 1),
                'stage_ms': {k: round(v, 1) for k, v in stages.items()},
                'infer_ms': round((time.perf_counter()-start)*1000, 1)}


if __name__ == '__main__':
    import argparse
    import soundfile as sf
    parser = argparse.ArgumentParser()
    parser.add_argument('wav')
    parser.add_argument('--device', choices=['NPU'], default='NPU')
    args = parser.parse_args()
    audio, sr = sf.read(args.wav, dtype='float32')
    assert sr == 16000
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    engine = Engine(lambda s: print(s, flush=True), args.device)
    print(json.dumps({'devices': engine.devices, 'openvino': ov.__version__}, ensure_ascii=False), flush=True)
    for i in range(3):
        print(json.dumps(engine.infer(audio[:min(len(audio), engine.max_samples)]), ensure_ascii=False), flush=True)
