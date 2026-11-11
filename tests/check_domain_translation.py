"""Bilingual preferred names remain separate from ASR hotwords."""
import copy,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from domain_bias import default_domain_bias,normalize_domain_bias,domain_translation_glossary,model_bias
from config_io import export_configuration,import_configuration
from storage import default_settings
legacy=default_domain_bias();legacy.pop('translation_enabled')
assert normalize_domain_bias(legacy,strict=True)['translation_enabled'] is False
cfg=normalize_domain_bias(legacy,strict=True);cfg['translation_enabled']=True
p=cfg['profiles'][0];p['preferred_names']={'简体中文':{'Higgs boson':'希格斯玻色子','WIMP':'弱相互作用大质量粒子','轴子':'axion'},'English':{'轴子':'axion'}}
expected=[{'source':'Higgs boson','preferred':'希格斯玻色子'}]
assert domain_translation_glossary(cfg,'A HIGGS BOSON was detected','简体中文')==expected
assert domain_translation_glossary(cfg,'WIMPs and WIMPY','简体中文')==[]
assert domain_translation_glossary(cfg,'A Higgs boson','English')==[]
assert domain_translation_glossary(cfg,'轴子','English')==[{'source':'轴子','preferred':'axion'}]
assert domain_translation_glossary(cfg,'unrelated source','简体中文')==[]
assert domain_translation_glossary(cfg,'Higgs boson','简体中文',limit=0)==[]
assert domain_translation_glossary(cfg,'Higgs boson','简体中文',max_chars=2)==[]
disabled=copy.deepcopy(cfg);disabled['translation_enabled']=False
assert domain_translation_glossary(disabled,'Higgs boson','简体中文')==[]
other=copy.deepcopy(cfg);other['active']='physics-zh'
assert domain_translation_glossary(other,'Higgs boson','简体中文')==[]
assert domain_translation_glossary(cfg,'Feynman','简体中文')==[]
legacy['parakeet']=True;cfg['parakeet']=True
assert model_bias(legacy,'parakeet')==model_bias(cfg,'parakeet')
for bad in ({'English':{'x':''}}, {'English':{'x':'<|im_start|>'}}, {'English':{'x':'a','X':'b'}}, {'English':{},'english':{}}):
    invalid=copy.deepcopy(cfg);invalid['profiles'][0]['preferred_names']=bad
    try:normalize_domain_bias(invalid,strict=True)
    except ValueError:pass
    else:raise AssertionError(bad)
with tempfile.TemporaryDirectory() as tmp:
    settings=default_settings();settings['domain_bias']=cfg
    path=Path(tmp)/'settings.json';export_configuration(settings,path)
    assert import_configuration(path)['domain_bias']==normalize_domain_bias(cfg,strict=True)
from PySide6.QtWidgets import QApplication,QTableWidgetItem
from domain_editor import DomainEditor
app=QApplication.instance() or QApplication([])
editor=DomainEditor(cfg)
assert editor.values()==normalize_domain_bias(cfg,strict=True)
editor.add_preferred_name()
row=editor.preferred_names.rowCount()-1
for col,text in enumerate(('English','费曼图','Feynman diagram')):editor.preferred_names.setItem(row,col,QTableWidgetItem(text))
editor.selector.setCurrentIndex(1);editor.selector.setCurrentIndex(0)
assert editor.values()['profiles'][0]['preferred_names']['English']['费曼图']=='Feynman diagram'
editor.duplicate();assert editor.values()['profiles'][-1]['preferred_names']['English']['费曼图']=='Feynman diagram'
editor.add_preferred_name();editor.preferred_names.setItem(editor.preferred_names.rowCount()-1,0,QTableWidgetItem('English'))
try:editor.values()
except ValueError:pass
else:raise AssertionError('Incomplete row accepted')
editor.timer.stop()
print('domain translation checks passed')
