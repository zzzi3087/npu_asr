"""Prepare a public, hash-pinned catalog; local upload paths stay outside the repo."""
import argparse,hashlib,json,os,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tools'))
from download_models import REVISION

def create(source_root,output,upload_manifest):
    source_root=Path(source_root);assets={};models={}
    base='https://github.com/zzzi3087/npu_asr/releases/download/models-v1/'
    def entry(path,url=None):
        if path.suffix in ('.xml','.json','.txt'):
            text=path.read_text('utf-8')
            if re.search(r'(?i)(?:[a-z]:[\\/]+Users[\\/]+|/home/|/Users/)',text):raise RuntimeError('模型元数据中存在本机路径：'+path.name)
        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        name=sha[:16]+'-'+path.name
        if url is None:assets[name]={'name':name,'path':str(path.resolve()),'sha256':sha,'size':path.stat().st_size}
        return {'name':path.name,'size':path.stat().st_size,'sha256':sha,'url':url or base+name}
    folder=source_root/REVISION
    files=[entry(folder/('parakeet_'+part+ext),f'https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v2-ov/resolve/{REVISION}/parakeet_{part}{ext}')
        for part in ('melspectogram','encoder','decoder','joint') for ext in ('.xml','.bin')]
    files.append(entry(folder/'parakeet_vocab.json',f'https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v2-ov/resolve/{REVISION}/parakeet_vocab.json'))
    files.append(entry(folder/'tokenizer.model'))
    models['parakeet']={'name':'Parakeet v2','directory':REVISION,'source':'FluidInference/parakeet-tdt-0.6b-v2-ov',
        'revision':REVISION,'license':'CC-BY-4.0','files':files}
    qwen_files=['manifest.json','vocab.json','merges.txt','tokenizer_config.json','config.json',
        'audio-conv.xml','audio-conv.bin','audio-encoder.xml','audio-encoder.bin','text.xml','text.bin']
    for ident,directory,name in [('qwen3-asr-1.7b','qwen3-asr-1.7b-npu','Qwen3-ASR 1.7B'),('mega-asr','mega-asr-1.7b-npu','Mega-ASR')]:
        folder=source_root/directory
        files=[entry(folder/item) for item in qwen_files]
        table=entry(source_root/'qwen3-asr-1.7b-npu'/'embeddings.npy');files.append(table)
        models[ident]={'name':name,'directory':directory,'source':json.loads((folder/'manifest.json').read_text())['source'],
            'revision':'8a10bfd0cccb0c8b3be2852cae1e3ee73d6ffbf1','license':'Apache-2.0','files':files}
    models['silero-vad']={'name':'语音活动检测','directory':'silero-vad','source':'snakers4/silero-vad',
        'revision':'vendored-sha256','license':'MIT','files':[entry(source_root/'silero-vad'/'silero_vad.onnx')]}
    Path(output).write_text(json.dumps({'format':1,'models':models},ensure_ascii=False,indent=2),'utf-8')
    Path(upload_manifest).write_text(json.dumps({'assets':list(assets.values())},ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'models':len(models),'unique_upload_assets':len(assets),'upload_bytes':sum(x['size'] for x in assets.values())}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,default=Path(os.environ['LOCALAPPDATA'])/'ParakeetNPUSubtitles/models')
    parser.add_argument('--output',type=Path,default=ROOT/'resources'/'model-catalog.json');parser.add_argument('--upload-manifest',type=Path,required=True)
    args=parser.parse_args();create(args.source_root,args.output,args.upload_manifest)
