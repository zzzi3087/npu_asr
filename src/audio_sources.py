"""Explicit microphone / Windows playback-loopback selection. No silent fallback."""
from contextlib import contextmanager
import numpy as np
import sounddevice as sd


def normalize_audio_source(value=None,strict=False,legacy=''):
    default={'kind':'microphone','device':legacy or ''}
    if value is None:return default
    if not isinstance(value,dict):
        if strict:raise ValueError('音频来源配置必须是对象')
        return default
    kind=value.get('kind','microphone');device=value.get('device','')
    if kind not in ('microphone','desktop') or not isinstance(device,str) or len(device)>1000:
        if strict:raise ValueError('音频来源类型或设备名称无效')
        return default
    return {'kind':kind,'device':device}


def microphone_devices():
    devices=sd.query_devices();hosts=sd.query_hostapis();result=[]
    for i,info in enumerate(devices):
        if info['max_input_channels']:
            host=hosts[info['hostapi']]['name'];label=info['name']+' · '+('WASAPI' if 'WASAPI' in host else host)
            result.append({'index':i,'name':label,'rate':int(info['default_samplerate']),'host':host})
    return result


def desktop_devices():
    import pyaudiowpatch as pa
    with pa.PyAudio() as manager:
        return [{'index':int(d['index']),'name':d['name'],'rate':int(d['defaultSampleRate'])}
                for d in manager.get_loopback_device_info_generator()]


def source_label(value):
    value=normalize_audio_source(value)
    return ('桌面音频' if value['kind']=='desktop' else '麦克风')+' · '+(value['device'] or '系统默认设备')


class InputHandle:
    def __init__(self,stream,rate,channels,name,desktop=False):
        self.stream=stream;self.rate=rate;self.channels=channels;self.name=name;self.desktop=desktop
    @property
    def active(self):return self.stream.is_active() if self.desktop else self.stream.active
    def stop(self):
        if self.desktop:self.stream.stop_stream()
        else:self.stream.stop()


@contextmanager
def capture_source(source,callback):
    """callback receives owned float32 [frames,channels] blocks and status text."""
    source=normalize_audio_source(source,strict=True)
    if source['kind']=='microphone':
        devices=microphone_devices()
        if source['device']:
            info=next((d for d in devices if d['name']==source['device']),None)
            if info is None:raise RuntimeError('选定麦克风已不可用，请在设置 → 收音设置中重新选择。')
        else:
            hosts=sd.query_hostapis()
            default=next((int(h['default_input_device']) for h in hosts if 'WASAPI' in h['name'] and h['default_input_device']>=0),int(sd.default.device[0]))
            info=next((d for d in devices if d['index']==default),None)
            if info is None:raise RuntimeError('未发现默认麦克风，请在设置 → 收音设置中选择设备。')
        extra=sd.WasapiSettings(auto_convert=True) if 'WASAPI' in info['host'] else None
        def receive(data,frames,timing,status):callback(data.copy(),str(status) if status else '')
        with sd.InputStream(device=info['index'],samplerate=info['rate'],channels=1,dtype='float32',
                blocksize=int(info['rate']*.032),latency='high',callback=receive,extra_settings=extra) as stream:
            yield InputHandle(stream,info['rate'],1,info['name'])
        return
    import pyaudiowpatch as pa
    with pa.PyAudio() as manager:
        if source['device']:
            info=next((d for d in manager.get_loopback_device_info_generator() if d['name']==source['device']),None)
            if info is None:raise RuntimeError('选定播放设备已不可用，请在设置 → 收音设置中重新选择。')
        else:
            try:info=manager.get_default_wasapi_loopback()
            except (OSError,LookupError) as error:raise RuntimeError('未找到可采集的默认播放设备，请在收音设置中刷新设备。') from error
        rate=int(info['defaultSampleRate']);channels=int(info['maxInputChannels'])
        if channels<1:raise RuntimeError('播放设备未提供回环输入通道')
        def receive(data,frames,timing,status):
            callback(np.frombuffer(data,dtype=np.float32).reshape(-1,channels),str(status) if status else '')
            return (None,pa.paContinue)
        with manager.open(format=pa.paFloat32,channels=channels,rate=rate,input=True,
                input_device_index=int(info['index']),frames_per_buffer=int(rate*.032),stream_callback=receive) as stream:
            yield InputHandle(stream,rate,channels,info['name'],True)
