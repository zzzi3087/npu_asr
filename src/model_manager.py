"""On-demand pinned model downloads with resume, verification and cancellation."""
import hashlib,json,os,shutil,time,threading
from pathlib import Path
import requests
from filelock import FileLock,Timeout
from app_paths import APP_DIR,RESOURCE_DIR,DATA,MODEL_ROOT

_resolved={}


class DownloadCancelled(RuntimeError):pass


def catalog():
    return json.loads((RESOURCE_DIR/'model-catalog.json').read_text('utf-8'))['models']


def check_cancel(cancel):
    if cancel and cancel.is_set():raise DownloadCancelled('模型下载已取消；已下载的内容保留，下次可继续。')


def digest(path,cancel=None):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(4*1024*1024):check_cancel(cancel);value.update(block)
    return value.hexdigest()


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    partial=path.with_name(path.name+'.tmp')
    partial.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');partial.replace(path)


def _valid_size(path,item):
    try:return path.is_file() and path.stat().st_size==item['size']
    except OSError:return False


def model_dir(model_id):
    spec=catalog()[model_id]
    if model_id in _resolved:return _resolved[model_id]
    candidates=[APP_DIR/'models'/spec['directory'],MODEL_ROOT/spec['directory']]
    if model_id=='silero-vad' and not getattr(__import__('sys'),'frozen',False):candidates.insert(0,APP_DIR)
    for folder in candidates:
        if all(_valid_size(folder/item['name'],item) for item in spec['files']):return folder
    return MODEL_ROOT/spec['directory']


def model_file(model_id,name):return model_dir(model_id)/name


def needs_download(model_id):
    spec=catalog()[model_id];folder=model_dir(model_id)
    return any(not _valid_size(folder/item['name'],item) for item in spec['files'])


def _download(item,target,client,cancel,progress):
    partial=target.with_name(target.name+'.part')
    offset=partial.stat().st_size if partial.exists() else 0
    if offset>item['size']:offset=0
    if offset==item['size']:
        if digest(partial,cancel)==item['sha256']:partial.replace(target);progress(item['size']);return
        partial.replace(partial.with_name(partial.name+'.invalid-'+str(time.time_ns())));offset=0
    for attempt in range(3):
        check_cancel(cancel)
        offset=partial.stat().st_size if partial.exists() and partial.stat().st_size<=item['size'] else 0
        try:
            headers={'Range':f'bytes={offset}-'} if offset else {}
            with client.get(item['url'],headers=headers,stream=True,timeout=(12,20)) as response:
                response.raise_for_status()
                if offset and response.status_code==206:
                    if not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):raise RuntimeError('服务器返回了不匹配的续传位置')
                    mode='ab'
                else:offset=0;mode='wb'
                total=offset;progress(total)
                with partial.open(mode) as stream:
                    for block in response.iter_content(256*1024):
                        check_cancel(cancel)
                        if not block:continue
                        stream.write(block);total+=len(block)
                        if total>item['size']:raise RuntimeError('下载文件超过预期大小')
                        progress(total)
            if partial.stat().st_size!=item['size']:raise requests.ConnectionError('下载不完整')
            if digest(partial,cancel)!=item['sha256']:
                # Keep the file for diagnosis; an invalid download is never published.
                partial.replace(partial.with_name(partial.name+'.invalid-'+str(time.time_ns())))
                raise RuntimeError('模型校验失败，请重试下载')
            partial.replace(target);return
        except (requests.RequestException,OSError) as error:
            if attempt==2:raise RuntimeError('模型下载未完成，请检查网络后重试；已下载内容会保留。') from error
            if cancel and cancel.wait(attempt+1):check_cancel(cancel)


def ensure_model(model_id,cancel=None,report=None,only=None):
    spec=catalog()[model_id]
    items=[item for item in spec['files'] if only is None or item['name'] in only]
    # A complete existing installation is reused; writes go to per-user storage.
    folder=model_dir(model_id)
    if only is not None and not folder.exists():folder=MODEL_ROOT/spec['directory']
    folder.mkdir(parents=True,exist_ok=True)
    receipt_name=hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:16]+'-'+model_id+'.json'
    receipt_path=DATA/'model-validation'/receipt_name
    try:receipt=json.loads(receipt_path.read_text('utf-8'))
    except (OSError,ValueError):receipt={}
    all_bytes=sum(item['size'] for item in items);completed=0;last_report=0
    def emit(item,current,stage='download',force=False):
        nonlocal last_report
        now=time.monotonic()
        if report and (force or now-last_report>=.3):
            report({'model':spec['name'],'file':item['name'],'completed':completed+current,'total':all_bytes,'stage':stage})
            last_report=now
    with requests.Session() as client:
        client.headers['User-Agent']='LiveCaptions/0.1.0 model-downloader'
        for item in items:
            check_cancel(cancel)
            name=item['name']
            if Path(name).name!=name or not name:raise RuntimeError('无效的模型清单路径')
            target=folder/name
            # Locks are per file, so settings can fetch small tokenizers independently.
            lock=FileLock(str(MODEL_ROOT/'.locks'/hashlib.sha256(str(target).encode()).hexdigest())+'.lock')
            Path(lock.lock_file).parent.mkdir(parents=True,exist_ok=True)
            while True:
                try:lock.acquire(timeout=.5);break
                except Timeout:check_cancel(cancel)
            try:
                valid=False;worked=False
                if _valid_size(target,item):
                    stamp=target.stat().st_mtime_ns
                    valid=receipt.get(name)==[item['sha256'],item['size'],stamp]
                    if not valid:
                        worked=True;emit(item,0,'verify',True);valid=digest(target,cancel)==item['sha256']
                if not valid:
                    # Avoid modifying a read-only/legacy bundle if validation fails.
                    if folder.resolve()!=(MODEL_ROOT/spec['directory']).resolve():
                        _resolved[model_id]=MODEL_ROOT/spec['directory']
                        return ensure_model(model_id,cancel,report,only)
                    worked=True
                    remaining=item['size']-(target.with_name(name+'.part').stat().st_size if target.with_name(name+'.part').exists() else 0)
                    if shutil.disk_usage(folder).free<max(0,remaining)+32*1024*1024:raise RuntimeError('磁盘空间不足，无法下载模型')
                    reused=False
                    for other_id,other in catalog().items():
                        if other_id==model_id:continue
                        for candidate in other['files']:
                            if candidate['sha256']!=item['sha256']:continue
                            source=model_dir(other_id)/candidate['name']
                            if _valid_size(source,item) and digest(source,cancel)==item['sha256']:
                                staged=target.with_name(name+'.part')
                                try:staged.hardlink_to(source)
                                except OSError:shutil.copyfile(source,staged)
                                staged.replace(target);reused=True;break
                        if reused:break
                    if not reused:
                        emit(item,0,'download',True)
                        _download(item,target,client,cancel,lambda current:emit(item,current))
                record=[item['sha256'],item['size'],target.stat().st_mtime_ns]
                if receipt.get(name)!=record:receipt[name]=record;atomic_json(receipt_path,receipt)
                if worked:emit(item,item['size'],'verified',True)
                completed+=item['size']
            finally:lock.release()
    if only is None:
        _resolved[model_id]=folder
        if model_id=='parakeet' and not (folder/'manifest.json').exists():atomic_json(folder/'manifest.json',{'revision':spec['revision'],'repository':spec['source'],
            'sha256':{item['name']:item['sha256'] for item in items}})
    return folder
