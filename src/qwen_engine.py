"""Qwen3-ASR 1.7B: all neural forward passes explicitly on Intel NPU.

Split model layout follows Nerdstorm/LiveTranscribe (MIT). Token embeddings
are an offline packed table. No CPU/GPU neural fallback exists here.
"""
import os,json,time,re
from pathlib import Path
import numpy as np
import openvino as ov
from qwen_tokenizer import load_tokenizer,checked_context
from model_io import read_ir
from app_paths import MODEL_ROOT
from model_manager import model_dir as resolve_model_dir



class QwenMel:
    def __init__(self):
        self.window=(.5-.5*np.cos(2*np.pi*np.arange(400)/400)).astype(np.float32)
        def mel(hz):return np.where(hz<1000,hz/(200/3),15+np.log(np.maximum(hz,1)/1000)/(np.log(6.4)/27))
        def hz(m):return np.where(m<15,m*(200/3),1000*np.exp((m-15)*(np.log(6.4)/27)))
        edges=hz(np.linspace(mel(np.array(0.)),mel(np.array(8000.)),130))
        frequencies=np.arange(201)*40.
        lower=(frequencies[None]-edges[:-2,None])/(edges[1:-1]-edges[:-2])[:,None]
        upper=(edges[2:,None]-frequencies[None])/(edges[2:]-edges[1:-1])[:,None]
        bank=(np.maximum(0,np.minimum(lower,upper))*2/(edges[2:]-edges[:-2])[:,None]).astype(np.float32)
        self.bank=bank

    def __call__(self,audio):
        audio=np.asarray(audio,dtype=np.float32).reshape(-1)
        if len(audio)<=200:audio=np.pad(audio,(0,201-len(audio)))
        padded=np.pad(audio,(200,200),mode='reflect')
        framed=np.lib.stride_tricks.sliding_window_view(padded,400)[::160]
        spectrum=np.fft.rfft(framed*self.window,axis=-1)
        power=(spectrum.real**2+spectrum.imag**2).astype(np.float32)
        mel=power@self.bank.T
        log=np.log10(np.maximum(mel,1e-10))
        return np.ascontiguousarray(((np.maximum(log,log.max()-8)+4)/4).T,dtype=np.float32)


class QwenEngine:
    approximate_timestamps=True
    streaming_mode='utterance'
    def __init__(self,status=print,model_id='qwen3-asr-1.7b',language='auto',max_prompt=None,context='',model_dir=None):
        if model_id not in ('qwen3-asr-1.7b','mega-asr'):raise ValueError('不支持的 Qwen 转录模型')
        folder='mega-asr-1.7b-npu' if model_id=='mega-asr' else 'qwen3-asr-1.7b-npu'
        self.path=Path(model_dir) if model_dir is not None else resolve_model_dir(model_id)
        if not (self.path/'manifest.json').exists():raise RuntimeError('模型尚未转换或安装：'+folder)
        self.language=language;self.model_id=model_id
        self.tokenizer=load_tokenizer(str(self.path))
        self._context,self.context_tokens=checked_context(self.tokenizer,context)
        max_prompt=max_prompt or (512 if self.context else 256)
        if self.context and max_prompt<512:raise ValueError('领域上下文需要重新加载较大的 NPU 提示窗口')
        self.core=ov.Core()
        if 'NPU' not in self.core.available_devices:raise RuntimeError('未发现 NPU；不允许回退 CPU/GPU')
        self.manifest=json.loads((self.path/'manifest.json').read_text())
        self.width=self.manifest['text']['width'];self.audio_width=self.manifest['audio']['width']
        self.devices={};self.models={};self.requests={}
        self.max_prompt=max_prompt
        self.max_samples=17*16000 if max_prompt==256 else 20*16000
        cache=self.path/f'cache-npu-2026.3-p{max_prompt}';cache.mkdir(exist_ok=True)
        for name in ('audio-conv','audio-encoder','text'):
            status('加载 '+name+' → NPU（首次编译需等待）')
            start=time.perf_counter();model=read_ir(self.core,self.path/(name+'.xml'))
            config={'CACHE_DIR':str(cache/name),'PERFORMANCE_HINT':'LATENCY'}
            if name=='audio-conv':model.reshape({'chunks':[1,128,100]})
            elif name=='audio-encoder':model.reshape({'rows':[1,104,self.audio_width],'mask':[1,104]})
            else:config.update(NPU_USE_NPUW='YES',NPUW_LLM='YES',NPUW_DEVICES='NPU',
                NPUW_LLM_MAX_PROMPT_LEN=max_prompt,NPUW_LLM_MIN_RESPONSE_LEN=256)
            compiled=self.core.compile_model(model,'NPU',config)
            devices=compiled.get_property('EXECUTION_DEVICES')
            devices=[devices] if isinstance(devices,str) else list(devices)
            if not devices or any(not d.startswith('NPU') for d in devices):raise RuntimeError('禁止非 NPU 神经网络执行：'+name)
            self.devices[name]=devices;self.models[name]=compiled;self.requests[name]=compiled.create_infer_request()
            status(f'{name} 就绪：{devices}，{time.perf_counter()-start:.1f}s')
        table_path=self.path/'embeddings.npy'
        if not table_path.exists():table_path=MODEL_ROOT/'qwen3-asr-1.7b-npu/embeddings.npy'
        self.embeddings=np.load(table_path,mmap_mode='r',allow_pickle=False)
        if self.embeddings.shape[1]!=self.width:raise RuntimeError('嵌入查表维度不匹配')
        self.frontend=QwenMel()
        self._beam=any('beam_idx' in port.get_names() for port in self.models['text'].inputs)
        self.conv_input=self.requests['audio-conv'].get_tensor('chunks').data
        self.encoder_rows=self.requests['audio-encoder'].get_tensor('rows').data
        self.encoder_mask=self.requests['audio-encoder'].get_tensor('mask').data
        self.attention=np.ones((1,max_prompt+256),np.int64)
        self.position_ids=np.arange(max_prompt+256,dtype=np.int64)[None]
        self.beam=np.zeros(1,np.int32)
        self.next_embedding=np.empty((1,1,self.width),np.float32)

    @property
    def context(self):return self._context

    @context.setter
    def context(self,value):
        text,count=checked_context(self.tokenizer,value)
        if text and self.max_prompt<512:raise ValueError('领域上下文需要重新加载 NPU 提示窗口')
        self._context=text;self.context_tokens=count

    def infer(self,audio):
        if len(audio)>self.max_samples:raise ValueError('音频超过 NPU 窗口限制')
        start=time.perf_counter();cpu=time.process_time();stages={}
        features=self.frontend(audio);stages['features']=(time.perf_counter()-start)*1000
        conv=self.requests['audio-conv'];encoder=self.requests['audio-encoder']
        rows=[]
        for frame in range(0,features.shape[1],100):
            chunk=features[:,frame:frame+100]
            keep=(chunk.shape[1]+7)//8
            self.conv_input.fill(0);self.conv_input[0,:,:chunk.shape[1]]=chunk
            conv.infer();rows.append(conv.get_output_tensor(0).data[0,:keep].copy())
        rows=np.concatenate(rows,axis=0);hidden=[]
        for pos in range(0,len(rows),104):
            window=rows[pos:pos+104]
            self.encoder_rows.fill(0);self.encoder_rows[0,:len(window)]=window
            self.encoder_mask.fill(0);self.encoder_mask[0,:len(window)]=1
            encoder.infer();hidden.append(encoder.get_output_tensor(0).data[0,:len(window)].copy())
        hidden=np.concatenate(hidden,axis=0)
        stages['audio_npu']=(time.perf_counter()-start)*1000-stages['features']
        prompt='<|im_start|>system\n'+self.context+'<|im_end|>\n<|im_start|>user\n<|audio_start|>'+'<|audio_pad|>'*len(hidden)+'<|audio_end|><|im_end|>\n<|im_start|>assistant\n'
        if self.language!='auto':prompt+='language '+self.language+'<asr_text>'
        ids=self.tokenizer.encode(prompt).ids
        if len(ids)>self.max_prompt:raise RuntimeError('音频超过 NPU 提示窗口限制；不回退其他设备')
        embeddings=np.asarray(self.embeddings[ids],dtype=np.float32)[None]
        audio_id=self.manifest['text']['audio_token_id']
        positions=[i for i,token in enumerate(ids) if token==audio_id]
        embeddings[0,positions]=hidden
        decoder=self.requests['text'];decoder.reset_state();generated=[]
        for step in range(256):
            length=len(ids)+step
            feeds={'inputs_embeds':embeddings,'attention_mask':self.attention[:,:length],
                'position_ids':(self.position_ids[:,:len(ids)] if not step else self.position_ids[:,length-1:length])}
            if self._beam:feeds['beam_idx']=self.beam
            decoder.infer(feeds,share_inputs=True)
            token=int(np.argmax(decoder.get_tensor('logits').data[0,-1]))
            if token in (151643,151645):break
            generated.append(token)
            self.next_embedding[0,0]=self.embeddings[token]
            embeddings=self.next_embedding
        else:raise RuntimeError('NPU 生成达到长度上限，未完成转录；未启用 CPU/GPU 回退')
        raw=self.tokenizer.decode(generated,skip_special_tokens=False)
        detected=self.language
        if '<asr_text>' in raw:
            header,raw=raw.split('<asr_text>',1);detected=header.removeprefix('language ').strip()
        text=re.sub(r'<\|[^>]+\|>','',raw).strip()
        units=re.findall(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\s\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+',text)
        duration=len(audio)/16000
        return {'text':text,'language':detected,'words':[(word,duration*i/max(1,len(units))) for i,word in enumerate(units)],
            'context_tokens':self.context_tokens,'prompt_tokens':len(ids),
            'approximate_timestamps':True,'infer_ms':round((time.perf_counter()-start)*1000,1),
            'cpu_ms':round((time.process_time()-cpu)*1000,1),'stage_ms':stages}
