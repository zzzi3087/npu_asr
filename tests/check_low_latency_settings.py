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
    from storage import default_settings
    from settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    original = default_settings()
    snapshot = copy.deepcopy(original)
    dialog = SettingsDialog(original)
    choices = [dialog.interval.itemData(i) for i in range(dialog.interval.count())]
    assert choices == [.5, .75, 1., 1.5, 2.], choices
    for interval in choices:
        settings = copy.deepcopy(original)
        settings['interval'] = interval
        filename = Path(directory) / 'settings.json'
        export_configuration(settings, filename)
        assert import_configuration(filename)['interval'] == interval
        dialog.load_draft(settings)
        assert dialog.interval.currentData() == interval
        assert dialog.draft()['interval'] == interval
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
