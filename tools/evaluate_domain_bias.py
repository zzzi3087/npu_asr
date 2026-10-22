"""Paired local NPU evaluation on a user-owned short-clip manifest.

Manifest: [{"audio":"clip.wav","language":"English","terms":["Feynman"]}].
Use terms=[] for an utterance where none of the listed terms was spoken.
Output includes local transcripts; no network API is used.
"""
import os,sys,json,argparse,gc
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,soundfile as sf,soxr
from storage import load_settings
from domain_bias import active_domain,build_context
from engine import Engine
from qwen_engine import QwenEngine
from languages import LANGUAGES

def normalized(text):return ''.join(c for c in text.casefold() if c.isalnum())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path);parser.add_argument('--domain',help='profile ID or exact name; defaults to selected profile')
    parser.add_argument('--models',nargs='+',choices=['parakeet','qwen3-asr-1.7b','mega-asr'],default=['qwen3-asr-1.7b','mega-asr'])
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    config,profile=active_domain(load_settings()['domain_bias'])
    if args.domain:
        matches=[p for p in config['profiles'] if args.domain in (p['id'],p['name'])]
        if len(matches)!=1:parser.error('domain not found')
        profile=matches[0]
    data=json.loads(args.manifest.read_text('utf-8-sig'))
    if not isinstance(data,list) or not 1<=len(data)<=100:parser.error('manifest must contain 1–100 clips')
    clips=[]
    for index,item in enumerate(data):
        if not isinstance(item,dict) or item.get('language','auto') not in {p[0] for p in LANGUAGES}:parser.error('invalid manifest language')
        path=Path(item['audio']);path=path if path.is_absolute() else args.manifest.parent/path
        audio,sr=sf.read(path,dtype='float32');audio=audio.mean(axis=1) if audio.ndim>1 else audio
        audio=soxr.resample(audio,sr,16000)
        if not 4000<=len(audio)<=192000:parser.error('each clip must be 0.25–12 seconds; cut clips without silently truncating')
        terms=item.get('terms',[])
        if not isinstance(terms,list) or any(not isinstance(t,str) or not t.strip() for t in terms):parser.error('terms must be strings')
        clips.append((dict(item,id=item.get('id',str(index)),terms=terms),audio))
    results=[]
    for model in args.models:
        e=Engine() if model=='parakeet' else QwenEngine(model_id=model,max_prompt=512)
        for item,audio in clips:
            language=item.get('language','auto')
            if model=='parakeet' and language not in ('English','auto'):
                results.append(dict(model=model,sample=item['id'],skipped='Parakeet is English only'));continue
            for enabled in (False,True):
                if model=='parakeet':e.configure_terms([t for t in profile['terms'] if all(ord(c)<128 for c in t)] if enabled else (),config['strength'])
                else:e.language=language;e.context=build_context(profile) if enabled else ''
                out=e.infer(audio)
                results.append(dict(model=model,sample=item['id'],enabled=enabled,expected_terms=item['terms'],devices=e.devices,
                    **{k:v for k,v in out.items() if k!='words'}))
        del e;gc.collect()
    summaries=[];all_terms=set(profile['terms'])|{t for item,audio in clips for t in item['terms']}
    for model in args.models:
        for enabled in (False,True):
            rows=[r for r in results if r['model']==model and r.get('enabled')==enabled and 'skipped' not in r]
            summaries.append(dict(model=model,enabled=enabled,clips=len(rows),
                term_hits=sum(normalized(t) in normalized(r['text']) for r in rows for t in r['expected_terms']),
                expected_terms=sum(len(r['expected_terms']) for r in rows),
                negative_clips=sum(not r['expected_terms'] for r in rows),
                negative_clips_with_listed_term=sum(not r['expected_terms'] and any(normalized(t) in normalized(r['text']) for t in all_terms) for r in rows)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(domain=profile,summary=summaries,results=results),ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(summaries,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
