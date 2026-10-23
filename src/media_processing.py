"""Bounded, cancellable local-only FFmpeg analysis and PCM preparation."""
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from app_paths import RESOURCE_DIR


class FileCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():raise FileCancelled('文件任务已取消；已保存的字幕仍在历史中')


def media_tools():
    candidates=[RESOURCE_DIR/'media-tools'/'bin']
    if os.environ.get('PARAKEET_FFMPEG_DIR'):candidates.insert(0,Path(os.environ['PARAKEET_FFMPEG_DIR']))
    for folder in candidates:
        ffmpeg,ffprobe=folder/'ffmpeg.exe',folder/'ffprobe.exe'
        if ffmpeg.is_file() and ffprobe.is_file():return str(ffmpeg),str(ffprobe)
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if ffmpeg and ffprobe:return ffmpeg,ffprobe
    raise RuntimeError('缺少 FFmpeg/ffprobe，请使用完整安装包；源码运行可安装 FFmpeg 并加入 PATH')


def process_options():
    return {'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name=='nt' else {}


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=1)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def probe(path,cancel):
    _,ffprobe=media_tools();check_cancel(cancel)
    command=[ffprobe,'-v','error','-protocol_whitelist','file,pipe','-select_streams','a:0',
        '-show_entries','format=duration,size,format_name:stream=index,codec_name,sample_rate,channels,channel_layout,duration',
        '-of','json',str(path)]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,**process_options())
    deadline=time.monotonic()+60
    try:
        while True:
            check_cancel(cancel)
            try:out,error=process.communicate(timeout=.2);break
            except subprocess.TimeoutExpired:
                if time.monotonic()>deadline:raise RuntimeError('读取媒体信息超时，请检查文件或磁盘')
        if process.returncode:raise RuntimeError('无法读取音频文件，文件可能损坏或格式不受支持')
        info=json.loads(out)
        if not info.get('streams'):raise ValueError('该文件没有可转录的音轨')
        stream=info['streams'][0];fmt=info.get('format',{})
        duration=finite(stream.get('duration')) or finite(fmt.get('duration')) or 0.
        return {'duration':max(0.,duration),'sample_rate':int(stream.get('sample_rate',0)),
            'channels':int(stream.get('channels',0)),'codec':stream.get('codec_name','unknown'),
            'format':fmt.get('format_name','unknown'),'size':path.stat().st_size}
    finally:_stop(process)


def finite(value):
    try:number=float(value)
    except (ValueError,TypeError):return None
    return number if math.isfinite(number) else None


def run_ffmpeg(command,cancel,duration,progress):
    """Drain stdout separately; cancellation never waits for a progress line."""
    lines=queue.Queue(maxsize=128)
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,stdin=subprocess.DEVNULL,**process_options())
        def read():
            try:
                for raw in iter(process.stdout.readline,b''):
                    try:lines.put_nowait(raw.decode('utf-8','replace').strip())
                    except queue.Full:pass
            finally:process.stdout.close()
        reader=threading.Thread(target=read,daemon=True);reader.start()
        last=0.;seconds=0.
        try:
            while process.poll() is None or reader.is_alive() or not lines.empty():
                check_cancel(cancel)
                try:line=lines.get(timeout=.15)
                except queue.Empty:continue
                if line.startswith('out_time_us='):
                    try:seconds=max(seconds,int(line.partition('=')[2])/1e6)
                    except ValueError:continue
                    now=time.monotonic()
                    if now-last>=.2:
                        progress(min(.999,seconds/duration) if duration else None,seconds);last=now
            process.wait();check_cancel(cancel)
            errors.seek(0,2);length=errors.tell();errors.seek(max(0,length-131072))
            text=errors.read().decode('utf-8','replace')
            if process.returncode:
                # Do not put input paths or FFmpeg's unbounded diagnostics in UI/logs.
                raise RuntimeError('FFmpeg 无法解码或预处理该文件，请检查格式、可用磁盘空间和文件完整性')
            progress(1.,seconds)
            return text
        finally:
            _stop(process);reader.join(timeout=2)


def filters(options):
    channel=options.get('selected_channel')
    return ([f'pan=mono|c0=c{int(channel)}'] if channel is not None else [])+[
        'aformat=sample_fmts=flt:channel_layouts=mono']+(['highpass=f=80'] if options.get('highpass') else [])


def analyze(path,info,options,cancel,progress):
    ffmpeg,_=media_tools()
    channel_guard=info['channels']>1 and options.get('selected_channel') is None
    chain=(['astats@source=metadata=0:reset=0:measure_perchannel=RMS_level:measure_overall=none'] if channel_guard else [])
    chain+=filters(options)+['astats@mono=metadata=0:reset=0:measure_perchannel=none:measure_overall=RMS_level+Peak_level+DC_offset',
        'loudnorm=I=-20:TP=-2:LRA=11:print_format=json']
    command=[ffmpeg,'-hide_banner','-nostdin','-nostats','-threads','1','-filter_threads','1','-stats_period','0.25',
        '-progress','pipe:1','-protocol_whitelist','file,pipe','-i',str(path),'-map','0:a:0','-vn','-sn','-dn',
        '-af',','.join(chain),'-f','null','-']
    span=.5 if channel_guard else 1.
    text=run_ffmpeg(command,cancel,info['duration'],lambda fraction,seconds:progress(None if fraction is None else fraction*span,seconds))
    matches=re.findall(r'\{\s*"input_i".*?\}',text,re.S)
    if not matches:raise RuntimeError('FFmpeg 未返回响度测量结果')
    stats=json.loads(matches[-1])
    def measurement(label):
        found=re.findall(r'\[astats@mono @[^\]]+\] '+re.escape(label)+r':\s*([-+\w.]+)',text)
        return finite(found[-1]) if found else None
    result=dict(info,lufs=finite(stats.get('input_i')),true_peak=finite(stats.get('input_tp')),
        loudness_range=finite(stats.get('input_lra')),rms_db=measurement('RMS level dB'),
        sample_peak=measurement('Peak level dB'),dc_offset=measurement('DC offset'))
    if channel_guard:
        levels=[finite(v) for v in re.findall(r'\[astats@source @[^\]]+\] RMS level dB:\s*([-+\w.]+)',text)]
        candidates=[(level,index) for index,level in enumerate(levels) if level is not None]
        if candidates:
            strongest,index=max(candidates)
            if strongest>-65 and (result['rms_db'] is None or result['rms_db']<strongest-15):
                # Opposite-polarity channels can erase speech in a simple downmix.
                selected=analyze(path,info,dict(options,selected_channel=index),cancel,
                    lambda fraction,seconds:progress(None if fraction is None else .5+.5*fraction,seconds))
                selected['warnings'].insert(0,f'混合声道出现明显抵消，已改用第 {index+1} 个声道以保留语音')
                return selected
        progress(1.,info['duration'])
    warnings=[];gain=0.
    reference=result['lufs'] if result['lufs'] is not None else result['rms_db']
    peak=result['true_peak'] if result['true_peak'] is not None else result['sample_peak']
    if reference is None or reference<-85:
        warnings.append('音频接近静音，未放大底噪；可能没有可识别的语音')
    elif options.get('normalize',True):
        target=-20. if result['lufs'] is not None else -26.
        wanted=target-reference
        if abs(wanted)>=2. or (peak is not None and peak>-1.):
            gain=min(24.,wanted,-2.-peak if peak is not None else 0.)
            if wanted>24.:warnings.append('已限制最大增益为 24 dB；极低音量仍可能影响识别')
    if peak is not None and peak>=-.1:warnings.append('原音频可能已有削波失真，音量调整不能修复已损失的信息')
    if abs(result['dc_offset'] or 0)>0.02:warnings.append('检测到明显直流偏置，可尝试开启 80 Hz 低频过滤')
    result.update(gain_db=round(gain,2),warnings=warnings,highpass=bool(options.get('highpass')),
        selected_channel=options.get('selected_channel'))
    return result


def prepare(path,directory,stats,cancel,progress):
    # Reuse compatible PCM without another encode; never modify the input file.
    if stats['codec'].startswith('pcm_') and stats['sample_rate']==16000 and stats['channels']==1 and not stats['highpass'] and abs(stats['gain_db'])<.01 and path.suffix.lower() in ('.wav','.wave'):
        progress(1.,stats['duration']);return path
    free=shutil.disk_usage(directory).free
    if stats['duration'] and free<stats['duration']*64000+64*1024*1024:
        raise RuntimeError('临时磁盘空间不足，16 kHz 单声道缓存每小时约需 230 MB')
    ffmpeg,_=media_tools();target=directory/'prepared.wav'
    chain=filters(stats)
    if abs(stats['gain_db'])>=.01:chain.append(f'volume={stats["gain_db"]:.2f}dB')
    command=[ffmpeg,'-hide_banner','-nostdin','-nostats','-threads','1','-filter_threads','1','-stats_period','0.25',
        '-progress','pipe:1','-protocol_whitelist','file,pipe','-i',str(path),'-map','0:a:0','-vn','-sn','-dn',
        '-af',','.join(chain),'-ac','1','-ar','16000','-c:a','pcm_f32le','-rf64','auto','-y',str(target)]
    run_ffmpeg(command,cancel,stats['duration'],progress)
    return target
