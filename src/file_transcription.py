"""Disk-backed file ASR; reuse the worker's exclusive NPU engine."""
from pathlib import Path
import tempfile
import time
import numpy as np
import soundfile as sf
from media_processing import check_cancel,probe,analyze,prepare
from streaming import Recognizer
from qwen_streaming import QwenRecognizer


def transcribe_file(engine,job,cancel,emit,recognizer_factory=None):
    path=Path(job['path'])
    if not path.is_file():raise FileNotFoundError('音频文件不存在或已被移动，请重新添加')
    options=job['options'];check_cancel(cancel)
    emit('progress',{'stage':'analyzing','percent':0,'stage_percent':0})
    info=probe(path,cancel)
    def progress(stage,base,weight):
        def update(fraction,seconds):
            emit('progress',{'stage':stage,'percent':base+weight*(fraction or 0),
                'stage_percent':None if fraction is None else fraction*100,'seconds':seconds})
        return update
    with tempfile.TemporaryDirectory(prefix='livecaptions-file-') as temp:
        stats=analyze(path,info,options,cancel,progress('analyzing',0,10))
        emit('analysis',stats);check_cancel(cancel)
        emit('progress',{'stage':'preparing','percent':10,'stage_percent':0})
        pcm=prepare(path,Path(temp),stats,cancel,progress('preparing',10,10))
        factory=recognizer_factory or (QwenRecognizer if getattr(engine,'streaming_mode','')=='utterance' else Recognizer)
        duration=[info['duration']]
        def result(kind,value):
            check_cancel(cancel)
            if kind=='committed':
                value=dict(value)
                # Last zero padding must not extend beyond the original file.
                end=int(duration[0]*1000)
                value['audio_start_ms']=max(0,min(int(value.get('audio_start_ms',0)),max(0,end-1)))
                value['audio_end_ms']=max(value['audio_start_ms']+1,min(int(value.get('audio_end_ms',end)),end))
            if kind in ('committed','metrics'):emit(kind,value)
        # Files need stable final text, rather than repeated live preview decoding.
        interval=999999 if getattr(engine,'streaming_mode','')=='utterance' else 6.
        recognizer=factory(engine,result,quiet=options.get('quiet',True),interval=interval,
            segmentation=options.get('segmentation','balanced'),agc=options.get('agc'))
        with sf.SoundFile(str(pcm)) as audio:
            if audio.samplerate!=16000 or audio.channels!=1:raise RuntimeError('预处理音频格式不正确')
            total=len(audio)
            if not total:raise ValueError('音频文件为空')
            duration[0]=total/16000;done=0;last=0.
            emit('progress',{'stage':'transcribing','percent':20,'stage_percent':0,'duration':duration[0]})
            while True:
                check_cancel(cancel)
                samples=audio.read(8192,dtype='float32')
                if not len(samples):break
                if not np.isfinite(samples).all():raise ValueError('音频包含无效采样值')
                for offset in range(0,len(samples),512):
                    check_cancel(cancel)
                    block=samples[offset:offset+512]
                    if len(block)<512:block=np.pad(block,(0,512-len(block)))
                    recognizer.push(block);recognizer.poll()
                done+=len(samples);now=time.monotonic()
                if now-last>=.2:
                    emit('progress',{'stage':'transcribing','percent':20+79*done/total,
                        'stage_percent':100*done/total,'seconds':done/16000,'duration':duration[0]});last=now
            check_cancel(cancel);recognizer.poll(stop=True);check_cancel(cancel)
    emit('progress',{'stage':'completed','percent':100,'stage_percent':100})
