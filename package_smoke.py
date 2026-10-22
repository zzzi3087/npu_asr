"""Explicit offline/isolated checks of the installed executable."""
import argparse,json,os,sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--report',type=Path,required=True);parser.add_argument('--model',choices=['parakeet','qwen3-asr-1.7b','mega-asr'])
    parser.add_argument('--wav',type=Path);parser.add_argument('--catalog-file',type=Path)
    args=parser.parse_args();os.environ['QT_QPA_PLATFORM']='offscreen'
    result={};w=None
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer,QEventLoop
        from app_paths import RESOURCE_DIR,VERSION
        from app import Window
        import model_manager
        if args.catalog_file:
            test_catalog=json.loads(args.catalog_file.read_text('utf-8'))['models']
            model_manager.catalog=lambda:test_catalog
        result['version']=VERSION
        result['catalog_loaded']=set(model_manager.catalog())=={'parakeet','qwen3-asr-1.7b','mega-asr','silero-vad'}
        result['model_files_bundled']=[str(p.relative_to(RESOURCE_DIR)) for p in RESOURCE_DIR.rglob('*') if p.is_file() and p.suffix.lower() in {'.onnx','.bin','.npy','.npz','.model','.safetensors','.pt','.pth'}]
        assert not result['model_files_bundled'],result['model_files_bundled']
        qt=QApplication([]);w=Window(start_workers=False);w.show();qt.processEvents()
        assert w.settings_button.menu() is None and w.source_summary.menu()
        w.on_event('download',{'model':'fixture','file':'fixture','completed':50,'total':100,'stage':'download'})
        assert w.start.text()=='取消下载';w.on_event('download_done',{})
        result['gui_created']=True
        import sounddevice as sd,pyaudiowpatch,openvino as ov
        result['audio_backends_loaded']=bool(sd.query_hostapis()) and hasattr(pyaudiowpatch,'PyAudio')
        core=ov.Core();result['devices']=core.available_devices
        from openvino import opset13 as op
        param=op.parameter([1,16],ov.Type.f32);relu=op.relu(param)
        compiled=core.compile_model(ov.Model([relu],[param]),'NPU',{'PERFORMANCE_HINT':'LATENCY'})
        devices=compiled.get_property('EXECUTION_DEVICES')
        result['npu_runtime']=[devices] if isinstance(devices,str) else list(devices)
        assert all(device.startswith('NPU') for device in result['npu_runtime'])
        if args.model:
            from audio_worker import AudioWorker
            options=dict(w.settings,asr_model=args.model,source_language='English',translate=False,feature_backend='numpy')
            worker=AudioWorker(options);worker.load_engine(options)
            result['asr_devices']=worker.engine.devices
            from streaming import Vad
            import numpy as np
            result['vad_loaded']=isinstance(Vad()(np.zeros(512,np.float32)),float)
            if args.wav:
                import soundfile as sf,soxr
                audio,rate=sf.read(args.wav,dtype='float32')
                if audio.ndim>1:audio=audio.mean(axis=1)
                audio=soxr.resample(audio,rate,16000)
                decoded=worker.engine.infer(audio)
                result['recognized_characters']=len(decoded['text']);assert result['recognized_characters']>0
        w.close();loop=QEventLoop();QTimer.singleShot(400,loop.quit);loop.exec()
        assert not w.translator.isRunning();result['ok']=True
    except BaseException as error:
        import traceback
        result.update(ok=False,error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc())
    finally:
        if w is not None:
            w.close()
            if w.translator.isRunning():w.translator.wait(5000)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    return 0 if result.get('ok') else 1
