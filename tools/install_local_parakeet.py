"""Install a verified program-local copy from the existing pinned model cache."""
import hashlib,json,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from download_models import DEFAULT_MODEL_DIR,REVISION,REQUIRED_FILES


def install(source=DEFAULT_MODEL_DIR,target=None):
    target=Path(target) if target is not None else Path(__file__).resolve().parents[1]/'models'/REVISION
    source=Path(source)
    manifest=json.loads((source/'manifest.json').read_text('utf-8'))
    hashes=dict(manifest['sha256'])
    if manifest.get('revision')!=REVISION or any(name not in hashes for name in REQUIRED_FILES):
        raise RuntimeError('原模型清单不完整或版本不匹配')
    tokenizer_source=source/'tokenizer-source.json'
    if tokenizer_source.is_file():
        tokenizer=json.loads(tokenizer_source.read_text('utf-8'))
        for artifact in tokenizer.get('artifacts',[]):
            if Path(artifact.get('file','')).name=='tokenizer.model':
                hashes['tokenizer.model']=artifact['sha256']
        with tokenizer_source.open('rb') as stream:hashes['tokenizer-source.json']=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest['sha256']=hashes
    if any(Path(name).name!=name for name in hashes):raise RuntimeError('模型清单包含无效文件名')
    files=list(hashes)
    for name in files:
        if not (source/name).is_file():raise RuntimeError('原模型文件缺失：'+name)
    for name in files:
        with (source/name).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=hashes[name]:raise RuntimeError('原模型校验失败：'+name)
    target.mkdir(parents=True,exist_ok=True)
    needed=sum((source/name).stat().st_size for name in files if not (target/name).is_file())
    if shutil.disk_usage(target).free<needed+100*1024*1024:raise RuntimeError('空间不足，无法创建程序目录模型副本')
    for name in files:
        final=target/name
        if final.is_file():
            with final.open('rb') as stream:existing=hashlib.file_digest(stream,'sha256').hexdigest()
            if existing==hashes[name]:continue
        partial=final.with_name(final.name+'.part')
        shutil.copyfile(source/name,partial)
        with partial.open('rb') as stream:copied=hashlib.file_digest(stream,'sha256').hexdigest()
        if copied!=hashes[name]:raise RuntimeError('本地副本校验失败：'+name)
        partial.replace(final)
    # Publish the marker only after every file has been verified.
    marker=target/'manifest.json.part';marker.write_text(json.dumps(manifest,indent=2),'utf-8')
    marker.replace(target/'manifest.json')
    return {'local_model_directory':str(target),'verified_files':len(files),
        'total_bytes':sum((target/name).stat().st_size for name in files),'source':'existing-cache','downloaded':False}


if __name__=='__main__':print(json.dumps(install(),ensure_ascii=False,indent=2),flush=True)
