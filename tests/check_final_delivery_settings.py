"""Isolated forced final-delivery settings, validation and offscreen UI checks."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

with tempfile.TemporaryDirectory() as directory:
    os.environ['PARAKEET_DATA_DIR'] = directory
    from PySide6.QtWidgets import QApplication
    from config_io import export_configuration, import_configuration, validate_configuration
    from storage import default_settings, load_settings, save_settings
    from settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    directory = Path(directory)
    settings_path = directory / 'settings.json'

    original = default_settings()
    assert original['final_delivery_enabled'] is True
    assert original['final_delivery_wait'] == 4.
    assert original['final_delivery_words'] == 35

    # Safe load: malformed or out-of-range persisted values fall back to defaults.
    settings_path.write_text(json.dumps({
        'final_delivery_enabled': 'yes', 'final_delivery_wait': float('nan'),
        'final_delivery_words': True}), 'utf-8')
    loaded = load_settings(directory)
    assert loaded['final_delivery_enabled'] is True
    assert loaded['final_delivery_wait'] == 4.
    assert loaded['final_delivery_words'] == 35

    cases = [(dict(final_delivery_wait=v), 'final_delivery_wait', 4.)
             for v in (True, 0.49, 60.01, float('inf'), '4')]
    cases += [(dict(final_delivery_words=v), 'final_delivery_words', 35)
              for v in (True, 0, 501, 35.0, '35')]
    for patch, key, fallback in cases:
        settings_path.write_text(json.dumps(dict(original, **patch)), 'utf-8')
        assert load_settings(directory)[key] == fallback, (key, patch)
    for value in (0.5, 60):
        settings_path.write_text(json.dumps(dict(original, final_delivery_wait=value)), 'utf-8')
        assert load_settings(directory)['final_delivery_wait'] == float(value)
    for value in (1, 500):
        settings_path.write_text(json.dumps(dict(original, final_delivery_words=value)), 'utf-8')
        assert load_settings(directory)['final_delivery_words'] == value

    # Safe save: out-of-range drafts are normalized before they reach disk.
    bad = copy.deepcopy(original)
    bad.update(final_delivery_enabled='on', final_delivery_wait=999, final_delivery_words=False)
    save_settings(bad, directory)
    saved = json.loads(settings_path.read_text('utf-8'))
    assert saved['final_delivery_enabled'] is True
    assert saved['final_delivery_wait'] == 4.
    assert saved['final_delivery_words'] == 35
    good = copy.deepcopy(original)
    good.update(final_delivery_enabled=False, final_delivery_wait=.5, final_delivery_words=500)
    save_settings(good, directory)
    saved = json.loads(settings_path.read_text('utf-8'))
    assert saved['final_delivery_enabled'] is False
    assert saved['final_delivery_wait'] == .5
    assert saved['final_delivery_words'] == 500

    # Strict import validation: booleans are not numbers and ranges are enforced.
    invalid = [{'final_delivery_enabled': 1}, {'final_delivery_enabled': 'true'}]
    invalid += [{'final_delivery_wait': v} for v in
                (True, .49, 60.01, float('nan'), float('inf'), '4', None)]
    invalid += [{'final_delivery_words': v} for v in
                (True, 0, 501, 35.0, '35', None)]
    for patch in invalid:
        candidate = copy.deepcopy(original)
        candidate.update(patch)
        try:
            validate_configuration(candidate)
        except ValueError:
            pass
        else:
            raise AssertionError(('accepted invalid final delivery settings', patch))
    for patch in ({'final_delivery_wait': .5}, {'final_delivery_wait': 60},
                  {'final_delivery_words': 1}, {'final_delivery_words': 500}):
        candidate = copy.deepcopy(original)
        candidate.update(patch)
        valid = validate_configuration(candidate)
        for key, value in patch.items():
            assert valid[key] == value

    # Export/import roundtrip preserves disabled state and custom thresholds.
    custom = copy.deepcopy(original)
    custom.update(final_delivery_enabled=False, final_delivery_wait=7.5, final_delivery_words=12)
    export_configuration(custom, settings_path)
    imported = import_configuration(settings_path)
    assert imported['final_delivery_enabled'] is False
    assert imported['final_delivery_wait'] == 7.5
    assert imported['final_delivery_words'] == 12

    snapshot = copy.deepcopy(original)
    dialog = SettingsDialog(original)
    for model in ('parakeet', 'parakeet-v3', 'qwen3-asr-1.7b', 'mega-asr'):
        dialog.asr_model.setCurrentIndex(dialog.asr_model.findData(model))
        assert model == dialog.asr_model.currentData()
        for field in (dialog.final_delivery, dialog.final_delivery_wait, dialog.final_delivery_words):
            assert dialog.appearance.isRowVisible(field), (model, field)
    dialog.final_delivery.setChecked(False)
    assert not dialog.final_delivery_wait.isEnabled()
    assert not dialog.final_delivery_words.isEnabled()
    assert dialog.final_delivery.isEnabled()
    dialog.final_delivery.setChecked(True)
    assert dialog.final_delivery_wait.isEnabled() and dialog.final_delivery_words.isEnabled()

    dialog.final_delivery.setChecked(False)
    dialog.final_delivery_wait.setValue(6.5)
    dialog.final_delivery_words.setValue(42)
    draft = dialog.draft()
    assert draft['final_delivery_enabled'] is False
    assert draft['final_delivery_wait'] == 6.5
    assert draft['final_delivery_words'] == 42

    restored = copy.deepcopy(original)
    restored.update(final_delivery_enabled=True, final_delivery_wait=.5, final_delivery_words=500)
    dialog.load_draft(restored)
    assert dialog.final_delivery.isChecked()
    assert dialog.final_delivery_wait.value() == .5
    assert dialog.final_delivery_words.value() == 500
    assert dialog.final_delivery_wait.isEnabled() and dialog.final_delivery_words.isEnabled()
    assert dialog.draft()['final_delivery_wait'] == .5
    assert dialog.draft()['final_delivery_words'] == 500

    dialog.reject()
    assert original == snapshot
    print('final delivery settings roundtrip, validation and offscreen UI checks passed')
