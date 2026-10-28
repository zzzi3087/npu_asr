import copy, os, sys, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
tmp=tempfile.TemporaryDirectory();os.environ['PARAKEET_DATA_DIR']=tmp.name
from PySide6.QtWidgets import QApplication
from storage import default_settings
from settings_dialog import SettingsDialog
from file_queue import FileQueue
app=QApplication.instance() or QApplication([])
s=default_settings();s.update(asr_model='qwen3-asr-1.7b',source_language='Chinese',feature_backend='gpu',target_language='简体中文')
original=copy.deepcopy(s);d=SettingsDialog(s)
queue=FileQueue(path=Path(tmp.name)/'queue.json');queue.set_asr_options('parakeet-v3','French');queue_before=copy.deepcopy(queue.asr_options)
def model(value):d.asr_model.setCurrentIndex(d.asr_model.findData(value))
def language(value):d.source_language.setCurrentIndex(d.source_language.findData(value))
def row(field):return d.appearance.isRowVisible(field)
d.interval.setCurrentIndex(d.interval.findData(2.));d.confirmation_wait.setValue(4.)
d.domain_editor.flags['qwen'].setChecked(True);d.domain_editor.background.setPlainText('synthetic background');d.domain_editor.strength.setValue(1.4)
model('parakeet');assert d.source_language.currentData()=='English' and d.source_language.count()==1
assert all(row(w) for w in (d.interval,d.confirmation_wait,d.feature_backend,d.feature_note))
assert d.domain_editor.form.isRowVisible(d.domain_editor.strength) and not d.domain_editor.form.isRowVisible(d.domain_editor.background)
model('parakeet-v3');language('Ukrainian');model('qwen3-asr-1.7b');assert d.source_language.currentData()=='auto'
assert not any(row(w) for w in (d.interval,d.confirmation_wait,d.feature_backend,d.feature_note))
assert d.domain_editor.form.isRowVisible(d.domain_editor.background) and not d.domain_editor.form.isRowVisible(d.domain_editor.strength)
language('German');model('parakeet-v3');assert d.source_language.currentData()=='German';model('mega-asr');assert d.source_language.currentData()=='German'
model('parakeet');draft=d.draft();assert draft['feature_backend']=='gpu' and draft['interval']==2. and draft['confirmation_wait']==4.
assert draft['domain_bias']['qwen'] and draft['domain_bias']['strength']==1.4 and draft['domain_bias']['profiles'][0]['background']=='synthetic background'
assert draft['target_language']=='简体中文' and queue.asr_options==queue_before and s==original
for m,l,expected in [('parakeet','Chinese','English'),('qwen3-asr-1.7b','Ukrainian','auto'),('qwen3-asr-1.7b','English','English'),('qwen3-asr-1.7b','German','German')]:
    t=copy.deepcopy(s);t.update(asr_model=m,source_language=l);d.load_draft(t);assert d.source_language.currentData()==expected
# Unrelated service and gain changes leave the chosen language intact.
d.protocol.setCurrentIndex(d.protocol.findData('anthropic'));d.agc_enabled.toggle();assert d.source_language.currentData()=='German'
d.reject();assert s==original and queue.asr_options==queue_before
print('model settings checks passed')
