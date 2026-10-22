"""Audit a release runtime for local paths, credential literals and model weights."""
import argparse,io,json,marshal,re,types,zipfile
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader
WEIGHTS={'.onnx','.bin','.npy','.npz','.model','.safetensors','.pt','.pth'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args()
    root=args.directory.resolve();findings=[];count=0;code_count=0
    home=str(Path.home());variants={home,home.replace('\\','/'),home.replace('\\','\\\\')}
    needles=[s.encode(encoding).lower() for s in variants for encoding in ('utf-8','utf-16le','utf-16be')]
    def scan(data,name):
        lower=data.lower()
        if any(needle in lower for needle in needles):findings.append({'file':name,'reason':'local_home_path'})
        if re.search(rb'(?<![A-Za-z0-9])(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})',data):
            findings.append({'file':name,'reason':'credential_shaped_literal'})
    def code(value,name):
        nonlocal code_count
        if not isinstance(value,types.CodeType):return
        code_count+=1;scan(value.co_filename.encode('utf-8'),name)
        for item in value.co_consts:
            if isinstance(item,types.CodeType):code(item,name)
            elif isinstance(item,str):scan(item.encode('utf-8'),name)
            elif isinstance(item,bytes):scan(item,name)
    for path in root.rglob('*'):
        if not path.is_file():continue
        name=str(path.relative_to(root));count+=1
        if path.suffix.lower() in WEIGHTS:findings.append({'file':name,'reason':'model_weight'})
        if path.name.lower() in ('settings.json','history.sqlite3','.env','health.json'):findings.append({'file':name,'reason':'personal_runtime_data'})
        data=path.read_bytes();scan(data,name)
        if path.suffix=='.zip':
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for filename in archive.namelist():
                    block=archive.read(filename);scan(block,name+'/'+filename)
                    if filename.endswith('.pyc'):code(marshal.loads(block[16:]),name+'/'+filename)
    archive=CArchiveReader(root/'LiveCaptions.exe');pyz=archive.open_embedded_archive('PYZ.pyz')
    for name in pyz.toc:code(pyz.extract(name),name)
    for name,entry in archive.toc.items():
        if entry[-1]=='s':code(marshal.loads(archive.extract(name)),name)
    result={'files':count,'code_objects':code_count,'model_weights':0,'findings':findings,'passed':not findings}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if findings:raise SystemExit(1)
if __name__=='__main__':main()
