"""Real FFmpeg preprocessing and cancellation, using synthetic audio only."""
import hashlib,json,os,subprocess,sys,threading,time
from pathlib import Path
from tempfile import TemporaryDirectory
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import soundfile as sf
from media_processing import probe,analyze,prepare,media_tools,FileCancelled,run_ffmpeg,process_options
from file_transcription import transcribe_file

cancel=threading.Event();ffmpeg,_=media_tools();noop=lambda *_:None
with TemporaryDirectory(prefix='captions-media-test-') as temp:
    root=Path(temp);rate=48000;clock=np.arange(rate*4)/rate
    quiet=root/'quiet.wav';sf.write(quiet,.008*np.sin(2*np.pi*440*clock),rate,subtype='FLOAT')
    before=hashlib.sha256(quiet.read_bytes()).hexdigest();info=probe(quiet,cancel)
    stats=analyze(quiet,info,{'normalize':True},cancel,noop)
    assert stats['lufs']<-40 and stats['gain_db']>15 and stats['gain_db']<=24,stats
    pcm=prepare(quiet,root,stats,cancel,noop);samples,prepared_rate=sf.read(pcm)
    assert prepared_rate==16000 and samples.ndim==1 and len(samples)==64000 and np.max(np.abs(samples))<.8
    assert hashlib.sha256(quiet.read_bytes()).hexdigest()==before
    output_stats=analyze(pcm,probe(pcm,cancel),{'normalize':False},cancel,noop)
    assert -22<output_stats['lufs']<-18,output_stats
    # Loud audio must be attenuated, and silence must never be boosted.
    loud=root/'loud.wav';sf.write(loud,.999*np.sin(2*np.pi*440*clock),rate,subtype='FLOAT')
    hot=analyze(loud,probe(loud,cancel),{'normalize':True},cancel,noop);assert hot['gain_db']<0
    assert hot['true_peak']+hot['gain_db']<=-1.9
    silence=root/'silence.wav';sf.write(silence,np.zeros(32000,np.float32),16000)
    silent=analyze(silence,probe(silence,cancel),{'normalize':True},cancel,noop)
    assert silent['gain_db']==0 and silent['warnings']
    assert prepare(silence,root,silent,cancel,noop)==silence
    # A compressed stereo input goes through FFmpeg, not SoundFile directly.
    compressed=root/'stereo.m4a';stereo=root/'stereo.wav'
    sf.write(stereo,np.stack([.08*np.sin(2*np.pi*440*clock),.06*np.sin(2*np.pi*550*clock)],axis=1),rate)
    subprocess.run([ffmpeg,'-v','error','-nostdin','-i',str(stereo),'-c:a','aac',str(compressed)],check=True,**process_options())
    compressed_stats=analyze(compressed,probe(compressed,cancel),{'normalize':True,'highpass':True},cancel,noop)
    decoded=prepare(compressed,root,compressed_stats,cancel,noop);detail=sf.info(decoded)
    assert detail.samplerate==16000 and detail.channels==1 and abs(detail.duration-4)<.05
    antiphase=root/'antiphase.wav';wave=.1*np.sin(2*np.pi*440*clock)
    sf.write(antiphase,np.stack([wave,-wave],axis=1),rate,subtype='FLOAT')
    guarded=analyze(antiphase,probe(antiphase,cancel),{'normalize':True},cancel,noop)
    assert guarded['selected_channel'] in (0,1) and guarded['lufs'] is not None and guarded['warnings']
    restored=prepare(antiphase,root,guarded,cancel,noop)
    assert np.max(np.abs(sf.read(restored)[0]))>.03
    # Cancel a running child process promptly, even between progress lines.
    timer=threading.Timer(.35,cancel.set);timer.start();start=time.monotonic()
    try:
        run_ffmpeg([ffmpeg,'-v','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=16000',
            '-t','120','-progress','pipe:1','-f','null','-'],cancel,120,noop)
        raise AssertionError('cancel ignored')
    except FileCancelled:pass
    finally:timer.cancel()
    assert time.monotonic()-start<3;cancel.clear()
    # The file driver remains block-bounded, flushes EOF, and clips timestamps.
    seen=[];instances=[]
    class Engine:
        streaming_mode='utterance'
    class Recognizer:
        def __init__(self,engine,emit,**options):self.emit=emit;self.blocks=0;self.options=options;instances.append(self)
        def push(self,block):assert len(block)==512;self.blocks+=1
        def poll(self,stop=False):
            if stop:self.emit('committed',{'text':'Synthetic fixture.','audio_start_ms':0,'audio_end_ms':999999})
    job={'path':str(compressed),'options':{'normalize':True,'highpass':False}}
    transcribe_file(Engine(),job,cancel,lambda k,v:seen.append((k,v)),Recognizer)
    segment=next(v for k,v in seen if k=='committed')
    assert 3950<=segment['audio_end_ms']<=4100
    assert instances[0].options['interval']==999999 and instances[0].blocks<140
    values=[v['percent'] for k,v in seen if k=='progress'];assert values==sorted(values) and values[-1]==100
    broken=root/'broken.mp3';broken.write_bytes(b'not an audio file')
    try:probe(broken,cancel);raise AssertionError('bad file accepted')
    except RuntimeError:pass
    print(json.dumps({'real_ffmpeg_loudness_analysis':True,'quiet_audio_normalized_lufs':round(output_stats['lufs'],1),
        'peak_protection':True,'silence_not_amplified':True,'source_unchanged':True,'aac_stereo_to_16khz_mono':True,
        'opposite_polarity_channels_preserve_speech':True,
        'running_process_cancellation':True,'bounded_block_reads':True,'original_timestamps':True,'invalid_file_rejected':True}))
