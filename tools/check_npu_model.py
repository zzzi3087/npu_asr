"""Validate one chosen transcription network on NPU using a local WAV."""
import argparse,json,sys,os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import soundfile as sf,soxr
from qwen_engine import QwenEngine
p=argparse.ArgumentParser();p.add_argument('wav');p.add_argument('--model',choices=['qwen3-asr-1.7b','mega-asr'],default='qwen3-asr-1.7b')
p.add_argument('--language',default='auto');args=p.parse_args()
audio,sr=sf.read(args.wav,dtype='float32')
if audio.ndim>1:audio=audio.mean(axis=1)
audio=soxr.resample(audio,sr,16000)
engine=QwenEngine(print,args.model,args.language)
print(json.dumps({'devices':engine.devices,'result':engine.infer(audio)},ensure_ascii=False))
