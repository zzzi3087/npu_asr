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
        from segmentation import TextAssembler,endpoint_samples
        from text_agreement import TextAgreement
        assert .76<endpoint_samples('balanced',True)/16000<.78
        assembly=TextAssembler()
        assert not assembly.update('The energy is approximately.',0,12,now=12,window_limit=True,language='English')
        joined=assembly.update('one hundred GeV. We continue.',12,15,now=16,idle=1,language='English')
        assert [event['text'] for event in joined]==['The energy is approximately one hundred GeV.','We continue.']
        agreement=TextAgreement();agreement.update('The sample is ready. We continue')
        assert agreement.update('The sample is ready. We continue today.')[0]=='The sample is ready.'
        result['incremental_sentence_segmentation']=True
        result['catalog_loaded']=set(model_manager.catalog())=={'parakeet','qwen3-asr-1.7b','mega-asr','silero-vad'}
        result['model_files_bundled']=[str(p.relative_to(RESOURCE_DIR)) for p in RESOURCE_DIR.rglob('*') if p.is_file() and p.suffix.lower() in {'.onnx','.bin','.npy','.npz','.model','.safetensors','.pt','.pth'}]
        assert not result['model_files_bundled'],result['model_files_bundled']
        qt=QApplication([]);w=Window(start_workers=False);w.show();qt.processEvents()
        assert w.settings_button.menu() is None and w.source_summary.menu()
        assert w.hide_source.text()=='不显示原文' and w.hide_translation.text()=='不显示译文'
        assert w.desktop.windowTitle()=='桌面字幕'
        from typography import render_captions
        from PySide6.QtWidgets import QTextBrowser
        fixture=QTextBrowser()
        rows=[dict(en='Physics fixture.',zh='',start_ms=0,uncertain=False,status='failed',error='fixture API unavailable')]
        render_captions(fixture,rows,w.colors,dict(w.settings,show_source=False,show_translation=True))
        assert 'Physics fixture.' in fixture.toPlainText() and 'unavailable' not in fixture.toPlainText()
        result['caption_visibility_and_error_fallback']=True
        w.on_event('download',{'model':'fixture','file':'fixture','completed':50,'total':100,'stage':'download'})
        assert w.start.text()=='取消下载';w.on_event('download_done',{})
        result['gui_created']=True
        from theme import PRESETS
        from settings_dialog import SettingsDialog
        from PySide6.QtCore import Qt
        dialog=SettingsDialog(w.settings,w)
        dialog.theme_preview.connect(w.apply_colors)
        dialog.theme_editor.choose_preset('米纸')
        assert len(dialog.theme_editor.cards)==4 and w.colors==PRESETS['米纸']
        dialog.theme_editor.follow.setChecked(True)
        dialog.theme_editor.system_changed(True)
        assert w.colors==PRESETS['暖岩']
        assert dialog.draft()['theme']=={'follow_system':True,'family':'warm'}
        dialog.reject();dialog.deleteLater();w.apply_display();qt.processEvents()
        result['four_palettes_and_system_theme']=True
        w.open_files();qt.processEvents()
        assert w.file_dialog.tree.columnCount()==4 and w.file_dialog.normalize.text()=='自动调整响度'
        live_model=w.settings['asr_model']
        w.file_dialog.model_combo.setCurrentIndex(w.file_dialog.model_combo.findData('qwen3-asr-1.7b'))
        assert w.settings['asr_model']==live_model and w.file_queue.asr_options['asr_model']=='qwen3-asr-1.7b'
        result['file_model_independent']=True
        w.file_dialog.hide();result['file_queue_gui_created']=True
        import tempfile,threading,subprocess
        import numpy as np,soundfile as sf
        from media_processing import media_tools,probe,analyze,prepare,process_options
        ffmpeg,ffprobe=media_tools()
        result['bundled_ffmpeg']=str(Path(ffmpeg).resolve()).startswith(str(RESOURCE_DIR.resolve()))
        assert result['bundled_ffmpeg']
        version=subprocess.check_output([ffmpeg,'-version'],**process_options()).decode('utf-8','replace').splitlines()[0]
        result['ffmpeg_version']=version
        with tempfile.TemporaryDirectory(prefix='captions-smoke-media-') as temporary:
            source=Path(temporary)/'fixture.wav'
            sf.write(source,.005*np.sin(np.arange(96000)*2*np.pi*440/48000),48000)
            cancel=threading.Event();stats=analyze(source,probe(source,cancel),{'normalize':True},cancel,lambda *_:None)
            prepared=prepare(source,Path(temporary),stats,cancel,lambda *_:None)
            info=sf.info(prepared)
            assert stats['gain_db']>0 and info.samplerate==16000 and info.channels==1
        result['bundled_audio_preprocessing']=True
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
                from file_transcription import transcribe_file
                events=[]
                transcribe_file(worker.engine,{'path':str(args.wav),'options':dict(options,normalize=True)},
                    threading.Event(),lambda kind,value:events.append((kind,value)))
                result['recognized_characters']=sum(len(value['text']) for kind,value in events if kind=='committed')
                assert result['recognized_characters']>0
                result['file_transcription_complete']=events[-1][1]['percent']==100
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
