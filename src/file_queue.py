"""Event-driven, persistent serial file queue. No credentials or audio copies."""
import json
from pathlib import Path
import uuid
from PySide6.QtCore import QObject,Signal,QTimer
from app_paths import DATA
from languages import LANGUAGES,ASR_MODELS,supported_language

FILE_MODELS=ASR_MODELS


def file_asr_options(value=None,strict=False):
    value=value if isinstance(value,dict) else {}
    model=value.get('asr_model','parakeet');language=value.get('source_language','auto')
    if model not in {ident for _,ident in FILE_MODELS}:
        if strict:raise ValueError('请选择支持的本地文件转录模型')
        model='parakeet'
    if language not in {ident for ident,_,_ in LANGUAGES}:
        if strict:raise ValueError('请选择支持的本地文件转录语言')
        language='auto'
    if not supported_language(model,language):
        if strict:raise ValueError('所选文件转录模型不支持此语言，请选择其他模型或自动识别')
        language='auto'
    return {'asr_model':model,'source_language':language}

ACTIVE={'loading','analyzing','preparing','transcribing'}
FINISHED={'completed','failed','cancelled','interrupted'}
LABELS={'queued':'等待中','loading':'准备 NPU','analyzing':'检查响度','preparing':'预处理',
    'transcribing':'NPU 转录','completed':'转录完成','failed':'失败','cancelled':'已取消','interrupted':'上次中断'}


class FileQueue(QObject):
    changed=Signal()
    dispatch=Signal(object)
    error=Signal(str)

    def __init__(self,parent=None,path=None):
        super().__init__(parent)
        self.path=Path(path) if path else DATA/'file-queue.json'
        self.jobs=[];self.active=None;self.available=False;self.paused=False
        self.preprocessing={'normalize':True,'highpass':False}
        self.asr_options=file_asr_options()
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text('utf-8'))
                self.asr_options=file_asr_options(data.get('asr_options'))
                for raw in data.get('jobs',[])[:1000]:
                    if not isinstance(raw,dict) or not raw.get('id') or not raw.get('path'):continue
                    if raw.get('status') in ACTIVE:raw['status']='interrupted';raw['error']='上次退出时未完成，可重试；已生成的字幕保留在历史中'
                    if raw.get('status') not in LABELS:continue
                    self.jobs.append(raw)
                self.preprocessing.update({k:bool(data.get('preprocessing',{}).get(k,v)) for k,v in self.preprocessing.items()})
                # Reopening the app must never start reading personal files unattended.
                self.paused=bool(self.jobs)
            except (ValueError,OSError,TypeError):self.paused=True

    def save(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps({'version':2,'asr_options':self.asr_options,'preprocessing':self.preprocessing,'jobs':self.jobs},ensure_ascii=False,allow_nan=False),encoding='utf-8')
            temp.replace(self.path)
        except OSError:self.error.emit('文件队列未能保存，请检查本机磁盘空间；本次运行仍可继续处理')

    def set_asr_options(self,model,language):
        options=file_asr_options({'asr_model':model,'source_language':language},strict=True)
        if options!=self.asr_options:
            self.asr_options=options;self.save();self.changed.emit()

    def add(self,paths,options):
        known={job['path'] for job in self.jobs if job['status'] in ACTIVE|{'queued'}}
        count=0
        for raw in paths:
            try:path=Path(raw).expanduser().resolve()
            except (OSError,RuntimeError):continue
            if not path.is_file() or str(path) in known:continue
            if len(self.jobs)>=1000:
                self.error.emit('队列最多保留 1000 项，其余文件未添加；请先清除已结束项');break
            # Explicit allowlist: profiles/API keys never enter this manifest.
            names=('feature_backend','domain_bias','quiet','agc','segmentation')
            snapshot=json.loads(json.dumps({k:options[k] for k in names if k in options}))
            snapshot.update(file_asr_options(self.asr_options,strict=True))
            snapshot.update(self.preprocessing)
            self.jobs.append({'id':uuid.uuid4().hex,'path':str(path),'name':path.name,'options':snapshot,
                'status':'queued','percent':0,'stage_percent':0,'session':None,'error':'','analysis':None})
            known.add(str(path));count+=1
        self.save();self.changed.emit();self.schedule()
        return count

    def find(self,ident):return next((job for job in self.jobs if job['id']==ident),None)

    def schedule(self):QTimer.singleShot(0,self.start_next)

    def set_available(self,available):
        self.available=bool(available)
        if available:self.schedule()
        self.changed.emit()

    def start_next(self):
        if self.paused or self.active or not self.available:return
        job=next((job for job in self.jobs if job['status']=='queued'),None)
        if not job:return
        self.active=job['id'];job.update(status='loading',percent=0,error='')
        self.save();self.changed.emit();self.dispatch.emit(job)

    def update(self,ident,kind,value):
        job=self.find(ident)
        if not job or ident!=self.active:return
        if kind=='analysis':job['analysis']=value;self.save()
        elif kind=='progress':
            job.update({k:v for k,v in value.items() if k!='stage'})
            previous=job['status'];job['status']=value['stage']
            if previous!=job['status']:self.save()
        elif kind=='done':
            job.update(status=value['status'],error=value.get('error',''))
            if job['status']=='completed':job['percent']=100
            self.active=None;self.save();self.schedule()
        self.changed.emit()

    def pause(self,paused):
        self.paused=bool(paused);self.changed.emit()
        if not paused:self.schedule()

    def retry(self,ident):
        job=self.find(ident)
        if job and ident!=self.active and job['status'] in FINISHED:
            job.update(status='queued',percent=0,stage_percent=0,error='',session=None,analysis=None)
            self.save();self.changed.emit();self.schedule()

    def remove(self,ident):
        if ident==self.active:return
        self.jobs=[job for job in self.jobs if job['id']!=ident]
        self.save();self.changed.emit()

    def clear_finished(self):
        self.jobs=[job for job in self.jobs if job['status'] not in FINISHED or job['id']==self.active]
        self.save();self.changed.emit()

    def move(self,ident,direction):
        job=self.find(ident)
        if not job or job['status']!='queued':return
        queued=[entry for entry in self.jobs if entry['status']=='queued'];index=queued.index(job)+direction
        if not 0<=index<len(queued):return
        other=queued[index];a=self.jobs.index(job);b=self.jobs.index(other)
        self.jobs[a],self.jobs[b]=self.jobs[b],self.jobs[a]
        self.save();self.changed.emit()
