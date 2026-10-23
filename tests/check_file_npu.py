"""Explicit end-to-end file transcription on actual Intel NPU; no microphone/API."""
import argparse,json,os,sys,time,threading
from pathlib import Path
from tempfile import TemporaryDirectory
parser=argparse.ArgumentParser();parser.add_argument('wav',type=Path)
parser.add_argument('--models',nargs='+',choices=['parakeet','qwen3-asr-1.7b','mega-asr'],default=['parakeet'])
args=parser.parse_args()
temporary=TemporaryDirectory(prefix='captions-file-npu-')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('PARAKEET_MODEL_ROOT',str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ParakeetNPUSubtitles/models'))
os.environ['PARAKEET_DATA_DIR']=str(Path(temporary.name)/'data')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import soundfile as sf,numpy as np
from audio_worker import AudioWorker
from storage import default_settings
from file_transcription import transcribe_file
audio,rate=sf.read(args.wav,dtype='float32')
if audio.ndim>1:audio=audio.mean(axis=1)
quiet=Path(temporary.name)/'quiet-fixture.wav';sf.write(quiet,audio*.05,rate,subtype='FLOAT')
worker=AudioWorker();cancel=threading.Event()
for model in args.models:
    options=default_settings()|{'asr_model':model,'source_language':'English','normalize':True,'highpass':False}
    worker.load_engine(options);events=[];start=time.monotonic()
    transcribe_file(worker.engine,{'path':str(quiet),'options':options},cancel,lambda k,v:events.append((k,v)))
    segments=[v for k,v in events if k=='committed'];stats=next(v for k,v in events if k=='analysis')
    assert segments and all(v['audio_start_ms']<v['audio_end_ms'] for v in segments)
    assert all(max(v['audio_start_ms'],v['audio_end_ms'])<=len(audio)/rate*1000+10 for v in segments)
    for component,devices in worker.engine.devices.items():
        if component!='melspectogram':assert devices and all(d.startswith('NPU') for d in devices)
    print(json.dumps({'model':model,'asr_devices':worker.engine.devices,'segments':len(segments),
        'characters':sum(len(v['text']) for v in segments),'gain_db':stats['gain_db'],
        'audio_seconds':round(len(audio)/rate,2),'processing_seconds':round(time.monotonic()-start,2),
        'completed':events[-1][1]['percent']==100}),flush=True)
