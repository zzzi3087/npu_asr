"""Isolated settings roundtrip and offscreen interval control checks."""
import copy
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

with tempfile.TemporaryDirectory() as directory:
    os.environ['PARAKEET_DATA_DIR'] = directory
    from PySide6.QtWidgets import QApplication
    from config_io import export_configuration, import_configuration, validate_configuration
    from storage import default_settings, load_settings, save_settings
    from settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    original = default_settings()
    snapshot = copy.deepcopy(original)
    dialog = SettingsDialog(original)
    choices = [dialog.interval.itemData(i) for i in range(dialog.interval.count())]
    assert choices == [.5, .75, 1., 1.5, 2.], choices
    for model in ('parakeet', 'qwen3-asr-1.7b', 'mega-asr'):
        settings = copy.deepcopy(original)
        settings.update(asr_model=model, source_language='auto', interval=1.5)
        dialog.load_draft(settings)
        assert dialog.appearance.isRowVisible(dialog.interval)
        assert dialog.appearance.isRowVisible(dialog.confirmation_wait) == (model == 'parakeet')
        assert dialog.appearance.isRowVisible(dialog.feature_backend) == (model == 'parakeet')
        assert dialog.appearance.isRowVisible(dialog.feature_note) == (model == 'parakeet')
        if model == 'parakeet':
            assert '提前复核' in dialog.interval.toolTip()
        else:
            assert '首个预览' in dialog.interval.toolTip()
            assert '3 秒' in dialog.model_note.text()
            assert '至少约 3 秒' not in dialog.model_note.text()
        for interval in choices:
            settings = copy.deepcopy(original)
            settings.update(asr_model=model, source_language='auto', interval=interval)
            filename = Path(directory) / 'settings.json'
            export_configuration(settings, filename)
            restored = import_configuration(filename)
            assert restored['asr_model'] == model and restored['interval'] == interval
            save_settings(settings, Path(directory))
            loaded = load_settings(Path(directory))
            assert loaded['asr_model'] == model and loaded['interval'] == interval
            dialog.load_draft(settings)
            assert dialog.interval.currentData() == interval
            draft = dialog.draft()
            assert draft['asr_model'] == model and draft['interval'] == interval
    for invalid in (.49, .6, .9, 2.1, float('nan'), True):
        settings = copy.deepcopy(original)
        settings['interval'] = invalid
        try:
            validate_configuration(settings)
        except ValueError:
            pass
        else:
            raise AssertionError(('accepted invalid interval', invalid))
    dialog.reject()
    assert original == snapshot
    print('isolated low latency settings roundtrip and offscreen choices passed')
