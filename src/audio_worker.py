import json
import os
from pathlib import Path
import queue
import threading
import time
import traceback
import sys
import numpy as np
import sounddevice as sd
import soxr
from PySide6.QtCore import QThread, Signal
from engine import Engine
from streaming import Recognizer
from storage import DATA
from audio_gain import normalize_agc
from domain_bias import model_bias
from audio_sources import capture_source,normalize_audio_source,microphone_devices
from download_models import MODEL_DIR
from model_manager import ensure_model,needs_download,DownloadCancelled

class AudioWorker(QThread):
    event = Signal(str, object)

    def __init__(self,options=None):
        super().__init__()
        self.commands = queue.Queue()
        self.stop_capture = threading.Event()
        self.quit_requested = threading.Event()
        self.ready = False
        self.metrics = {'committed_segments': 0, 'inferences': 0, 'capture_blocks': 0, 'errors': 0, 'uncertain_segments': 0}
        self.last_health = 0
        self.options=dict(options or {})
        self.live_gain=normalize_agc(self.options.get('agc'))
        self.engine_key=None
        self.download_cancel=threading.Event()
        self.file_cancel=threading.Event()
        self.file_task_active=False

    def update_gain(self,value):
        self.live_gain=normalize_agc(value)

    def load_engine(self,options):
        try:self._load_engine(options)
        except DownloadCancelled:raise
        except Exception:
            # No audio, transcript, translation profile or credentials in this log.
            try:
                DATA.mkdir(parents=True,exist_ok=True)
                header=json.dumps({'asr_model':options.get('asr_model','parakeet'),
                    'feature_backend':options.get('feature_backend','numpy'),'time':time.time(),
                    'pid':os.getpid(),'python':sys.executable,'working_directory':os.getcwd(),
                    'parakeet_model_directory':str(MODEL_DIR)},ensure_ascii=False)
                (DATA/'model-load-error.log').write_text(header+'\n'+traceback.format_exc(),'utf-8')
            except OSError:pass  # Preserve the actual model error if logging fails.
            raise

    def _load_engine(self,options):
        from languages import PARAKEET_MODELS,supported_language
        model_id=options.get('asr_model','parakeet')
        language=options.get('source_language','auto')
        feature_backend=options.get('feature_backend','numpy') if model_id=='parakeet' else 'numpy'
        bias=model_bias(options.get('domain_bias'),model_id)
        engine_key=(model_id,feature_backend,bool(bias['context']))
        if not supported_language(model_id,language):
            raise RuntimeError('所选模型不支持此转录语言，请更换模型或选择自动识别')
        if self.engine_key!=engine_key:
            import gc
            self.engine=None;gc.collect()
            self.engine_key=None
            self.download_cancel.clear()
            if self.quit_requested.is_set():raise DownloadCancelled('正在退出')
            if self.file_task_active and self.file_cancel.is_set():raise DownloadCancelled('文件任务已取消')
            if any(needs_download(ident) for ident in (model_id,'silero-vad')):
                import openvino as ov
                if 'NPU' not in ov.Core().available_devices:
                    raise RuntimeError('未发现 Intel NPU，请先安装 Intel NPU 驱动；不会下载模型或回退 CPU/GPU。')
            paths={}
            try:
                for ident in (model_id,'silero-vad'):
                    paths[ident]=ensure_model(ident,self.download_cancel,lambda progress:self.event.emit('download',progress))
            finally:self.event.emit('download_done',{})
            if model_id in PARAKEET_MODELS:
                self.engine=Engine(lambda message:self.event.emit('status',message),feature_backend=feature_backend,model_dir=paths[model_id],model_id=model_id,language=language)
                self.engine.infer(np.zeros(32000,np.float32))
            elif model_id in ('qwen3-asr-1.7b','mega-asr'):
                from qwen_engine import QwenEngine
                self.engine=QwenEngine(lambda message:self.event.emit('status',message),model_id,language,context=bias['context'],model_dir=paths[model_id])
            else:raise RuntimeError('不支持的转录模型；不回退 CPU/GPU')
            self.engine_key=engine_key
        if model_id not in PARAKEET_MODELS:self.engine.language=language;self.engine.context=bias['context']
        else:
            self.engine.language='English' if model_id=='parakeet' else language
            self.engine.configure_terms(bias['terms'],bias['strength'])

    def emit(self, kind, value):
        if kind == 'committed':
            self.metrics['committed_segments'] += 1
            self.metrics['uncertain_segments'] += int(value.get('uncertain', False))
        if kind == 'error':
            self.metrics['errors'] += 1
        if kind == 'metrics':
            self.metrics['inferences'] += 1
            self.metrics.update(value)
        self.event.emit(kind, value)
        if kind in ('ready', 'listening', 'stopped', 'error') or (kind == 'metrics' and time.monotonic()-self.last_health > 5):
            failed_load=kind=='ready' and isinstance(value,dict) and value.get('load_error')
            self.health('model_load_failed' if failed_load else kind)

    def health(self, state):
        # Diagnostics intentionally contain no microphone audio or transcript text.
        DATA.mkdir(exist_ok=True)
        self.last_health = time.monotonic()
        engine=getattr(self,'engine',None)
        (DATA / 'health.json').write_text(json.dumps({**self.metrics, 'state': state,
            'updated': time.time(), 'asr_devices': engine.devices if engine else {},
            'model_directory':str(getattr(engine,'model_dir',getattr(engine,'path','')))}, indent=2))

    def run(self):
        com_initialized=False
        if os.name=='nt':
            import ctypes
            # PortAudio is initialized by the GUI thread, but WASAPI opens its
            # stream on this QThread. That thread needs its own COM apartment.
            com_initialized=ctypes.windll.ole32.CoInitializeEx(None,0) in (0,1)
        try:
            load_error=None
            cancelled=False
            try:self.load_engine(self.options)
            except DownloadCancelled as error:load_error=str(error);cancelled=True
            except Exception as error:
                load_error=str(error);self.metrics['errors']+=1
            self.ready = load_error is None
            self.emit('ready',{'load_error':load_error,'download_cancelled':cancelled} if load_error else self.engine.devices)
            while not self.quit_requested.is_set():
                item = self.commands.get()
                if item is None:
                    break
                command, args = item
                if command == 'start':
                    self.stop_capture.clear()
                    try:
                        self.load_engine(args)
                        self.capture(args)
                    except DownloadCancelled as error:self.event.emit('download_cancelled',str(error))
                    except Exception as error:
                        self.emit('error', str(error))
                    finally:
                        self.emit('stopped', None)
                elif command == 'file':
                    from file_transcription import transcribe_file
                    from media_processing import FileCancelled,check_cancel
                    def event(kind,value):self.event.emit('file_event',{'id':args['id'],'kind':kind,'value':value})
                    status='completed';error=''
                    self.file_task_active=True
                    try:
                        check_cancel(self.file_cancel)
                        self.load_engine(args['options'])
                        check_cancel(self.file_cancel)
                        transcribe_file(self.engine,args,self.file_cancel,event)
                    except (FileCancelled,DownloadCancelled) as exc:status='cancelled';error=str(exc)
                    except Exception as exc:status='failed';error=str(exc)
                    finally:
                        self.file_task_active=False
                        event('done',{'status':status,'error':error})
        except Exception as error:
            self.emit('error', str(error))
        finally:
            if com_initialized:ctypes.windll.ole32.CoUninitialize()

    def capture(self, options):
        source=options.get('audio_source')
        if source is None and 'device' in options:
            source={'kind':'microphone','device':next((d['name'] for d in microphone_devices() if d['index']==options['device']),'')}
        source=normalize_audio_source(source,strict=True)
        blocks = queue.Queue(maxsize=512)
        failure = []
        count = 0

        last_callback=[time.monotonic()]
        def callback(indata, status):
            last_callback[0]=time.monotonic()
            if status:
                failure.append(str(status))
            try:
                blocks.put_nowait(indata)
            except queue.Full:
                failure.append('录音缓冲已满，停止以避免静默丢音。')

        cls=Recognizer
        if getattr(self.engine,'streaming_mode','')=='utterance':
            from qwen_streaming import QwenRecognizer
            cls=QwenRecognizer
        recognizer = cls(self.engine, self.emit, quiet=options['quiet'], interval=options['interval'],
            segmentation=options.get('segmentation','balanced'),agc=self.live_gain,gain_provider=lambda:self.live_gain,
            progressive=options.get('progressive_translation',True))
        remainder = np.zeros(0, np.float32)
        last_meter = 0
        with capture_source(source,callback) as stream:
            samplerate=stream.rate
            idle_audio=np.zeros((int(samplerate*.05),stream.channels),np.float32)
            resampler=soxr.ResampleStream(samplerate,16000,1,dtype='float32',quality='HQ')
            self.emit('listening',stream.name)
            while True:
                stopping = self.stop_capture.is_set() or self.quit_requested.is_set()
                if stopping and stream.active:
                    stream.stop()
                if failure:
                    raise RuntimeError('音频采集异常：' + failure[0])
                try:
                    raw = blocks.get(timeout=.05)
                except queue.Empty:
                    if stopping:
                        break
                    if not stream.active:
                        raise RuntimeError('音频来源已断开或音频流停止，请检查收音设置。')
                    # Some playback endpoints suspend silent loopback packets.
                    # Advance VAD with silence so a final sentence can still finish.
                    if source['kind']=='desktop' and time.monotonic()-last_callback[0]>.15:
                        raw=idle_audio
                    else:continue
                mono = raw.mean(axis=1)
                converted = resampler.resample_chunk(mono)
                remainder = np.concatenate([remainder, converted])
                while len(remainder) >= 512:
                    recognizer.push(remainder[:512])
                    remainder = remainder[512:]
                count += 1
                self.metrics['capture_blocks'] += 1
                if time.monotonic()-last_meter >= .2:
                    rms = float(np.sqrt(np.mean(mono**2)))
                    self.event.emit('level', min(100, int(rms*600)))
                    last_meter = time.monotonic()
                # Drain pending audio first; preview work is coalesced, audio is retained.
                if blocks.empty():
                    recognizer.poll()
            tail = np.concatenate([remainder, resampler.resample_chunk(np.zeros(0, np.float32), last=True)])
            for offset in range(0, len(tail), 512):
                recognizer.push(np.pad(tail[offset:offset+512], (0, max(0, 512-len(tail[offset:offset+512])))))
            recognizer.poll(stop=True)


