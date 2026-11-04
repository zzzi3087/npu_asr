"""Forced final-delivery values travel from the settings UI through app start
options and the audio worker into both realtime recognizer classes. No mic."""
import os
import queue
import sys
import tempfile
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

with tempfile.TemporaryDirectory() as directory:
    os.environ['PARAKEET_DATA_DIR'] = directory
    from PySide6.QtWidgets import QApplication
    from storage import default_settings, save_settings
    from settings_dialog import SettingsDialog
    import audio_worker
    import qwen_streaming
    import streaming

    app = QApplication.instance() or QApplication([])
    save_settings(default_settings(), Path(directory))

    from app import Window
    window = Window(start_workers=False)

    class FakeWorker:
        def __init__(self):
            self.commands = queue.Queue()
            self.stop_capture = threading.Event()
            self.file_cancel = threading.Event()
            self.file_task_active = False
            self.download_cancel = threading.Event()
            self.engine_key = None
        def isRunning(self):
            return False

    class Recorder:
        last = None
        def __init__(self, *args, **kwargs):
            type(self).last = dict(kwargs)
            self.pushed = 0
            self.polls = 0
        def push(self, block):
            self.pushed += 1
        def poll(self, stop=False):
            self.polls += 1

    class ParakeetStub(Recorder):
        pass

    class QwenStub(Recorder):
        pass

    class FakeStream:
        rate = 16000
        channels = 1
        active = True
        name = 'stub'
        def stop(self):
            self.active = False

    class FakeCapture:
        def __enter__(self):
            return FakeStream()
        def __exit__(self, *args):
            return False

    skip = object()
    original_recognizer = getattr(audio_worker, 'Recognizer', skip)
    original_qwen = getattr(qwen_streaming, 'QwenRecognizer', skip)
    original_capture = getattr(audio_worker, 'capture_source', skip)
    original_vads = (getattr(streaming, 'Vad', skip), getattr(qwen_streaming, 'Vad', skip))

    class NoOpVad:
        def __call__(self, block):
            return 0.

    audio_worker.Recognizer = ParakeetStub
    qwen_streaming.QwenRecognizer = QwenStub
    audio_worker.capture_source = lambda source, callback: FakeCapture()
    streaming.Vad = qwen_streaming.Vad = NoOpVad
    engine = types.SimpleNamespace(max_samples=17 * 16000, language='English', model_id='stub')
    emit = lambda *args: None
    worker = audio_worker.AudioWorker()
    worker.emit = lambda kind, value: None
    try:
        for enabled, wait, words in ((False, 6.5, 47), (True, 59.5, 499)):
            dialog = SettingsDialog(window.settings)
            assert dialog.final_delivery_wait.minimum() == .5
            assert dialog.final_delivery_wait.maximum() == 60.
            assert dialog.final_delivery_words.minimum() == 1
            assert dialog.final_delivery_words.maximum() == 500
            dialog.final_delivery.setChecked(enabled)
            dialog.final_delivery_wait.setValue(wait)
            dialog.final_delivery_words.setValue(words)
            assert dialog.final_delivery_wait.isEnabled() is enabled
            assert dialog.final_delivery_words.isEnabled() is enabled
            draft = dialog.draft()
            dialog.reject()
            assert draft['final_delivery_enabled'] is enabled
            assert draft['final_delivery_wait'] == wait
            assert draft['final_delivery_words'] == words
            save_settings(draft, directory)

            window.settings = draft
            window.ready = True
            window.capturing = False
            window.worker = FakeWorker()
            window.toggle()
            command, options = window.worker.commands.get_nowait()
            assert command == 'start'
            assert options['final_delivery_enabled'] is enabled
            assert options['final_delivery_wait'] == wait
            assert options['final_delivery_words'] == words

            for mode, stub in (('', ParakeetStub), ('utterance', QwenStub)):
                worker.engine = types.SimpleNamespace(streaming_mode=mode, devices={})
                worker.stop_capture.set()
                worker.capture(options)
                params = stub.last
                assert params['final_delivery_enabled'] is enabled, (mode, params)
                assert params['final_delivery_wait'] == wait, (mode, params)
                assert params['final_delivery_words'] == words, (mode, params)

            # The real core constructors accept the same kwargs unchanged.
            if original_recognizer is not skip and original_qwen is not skip:
                for cls in (original_recognizer, original_qwen):
                    recognizer = cls(engine, emit, quiet=True, interval=1.5,
                        final_delivery_enabled=enabled, final_delivery_wait=wait,
                        final_delivery_words=words)
                    assert recognizer.delivery.enabled is enabled
                    assert recognizer.delivery.wait == wait
                    assert recognizer.delivery.words == words
    finally:
        if original_recognizer is not skip:
            audio_worker.Recognizer = original_recognizer
        if original_qwen is not skip:
            qwen_streaming.QwenRecognizer = original_qwen
        if original_capture is not skip:
            audio_worker.capture_source = original_capture
        if original_vads[0] is not skip:
            streaming.Vad = original_vads[0]
        if original_vads[1] is not skip:
            qwen_streaming.Vad = original_vads[1]
        window.worker = FakeWorker()
        window.closing = True
        window.translator.stop()
        window.translator.wait(2000)
        window.hide()
        window.deleteLater()
        app.processEvents()
        window.history.close()

    print('final delivery UI -> app options -> worker -> both recognizers passed')
