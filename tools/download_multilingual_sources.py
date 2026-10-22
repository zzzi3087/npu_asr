import os,json,hashlib,time,requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
j=json.loads((Path(__file__).parent/'Mega-ASR-metadata.json').read_text())
root=Path(os.environ['LOCALAPPDATA'])/'ParakeetNPUSubtitles/models/mega-asr-source'
root.mkdir(parents=True,exist_ok=True)
files=[x for x in j['siblings'] if x['rfilename'].startswith(('Qwen3-ASR-1.7B/','mega-asr-merged/','audio_quality_router/')) and not x['rfilename'].endswith(('.gitattributes','README.md'))]
def fetch(f):
    name=f['rfilename'];dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True)
    expected=f.get('lfs',{}).get('sha256')
    if dest.exists() and dest.stat().st_size==f['size']:return
    partial=dest.with_name(dest.name+'.part')
    for attempt in range(4):
        try:
            start=partial.stat().st_size if partial.exists() else 0
            url=f'https://huggingface.co/zhifeixie/Mega-ASR/resolve/{j["sha"]}/{name}'
            with requests.get(url,params={'download':'true','ts':int(time.time())},headers={'Range':f'bytes={start}-'} if start else {},stream=True,timeout=(20,60)) as r:
                r.raise_for_status()
                mode='ab' if start and r.status_code==206 else 'wb'
                with partial.open(mode) as file:
                    for chunk in r.iter_content(4*1024*1024):file.write(chunk)
            if partial.stat().st_size!=f['size']:raise ValueError('size mismatch')
            if expected:
                h=hashlib.sha256()
                with partial.open('rb') as file:
                    for chunk in iter(lambda:file.read(8*1024*1024),b''):h.update(chunk)
                if h.hexdigest()!=expected:raise ValueError('hash mismatch')
            partial.replace(dest);print('DONE',name,f['size'],flush=True);return
        except Exception as e:
            print('RETRY',name,type(e).__name__,attempt,flush=True)
            if attempt==3:raise
            time.sleep(2)
with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,files))
(root/'source-manifest.json').write_text(json.dumps({'repo':'zhifeixie/Mega-ASR','revision':j['sha'],'files':files},indent=2),'utf-8')
print('COMPLETE',root,flush=True)
