"""Offline model-path and startup-failure regression; no model downloads or audio."""
import os,sys,json,argparse,hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
temporary=TemporaryDirectory(prefix='captions-model-startup-');root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
output=args.artifacts or Path(temporary.name);output.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(temporary.name)/'data')
sys.path.insert(0,str(root/'src'));sys.path.insert(0,str(root/'tools'))
from model_io import read_ir
from download_models import model_directory,REQUIRED_FILES,REVISION
from install_local_parakeet import install
from PySide6.QtCore import QTimer,QEventLoop
from PySide6.QtWidgets import QApplication
from storage import DATA,default_settings,save_settings
from audio_worker import AudioWorker
from app import Window

class Core:
    def __init__(self,failures=()):self.calls=[];self.failures=list(failures)
    def read_model(self,path):
        self.calls.append(path)
        if self.failures:raise RuntimeError(self.failures.pop(0))
        return 'model'

path=Path(temporary.name)/'模型路径'/ '测试.xml';path.parent.mkdir();path.write_bytes(b'<net/>')
bundle=Path(temporary.name)/'program';fallback=Path(temporary.name)/'cache'
assert model_directory(bundle,fallback)==fallback
local=bundle/'models'/REVISION;local.mkdir(parents=True);(local/'manifest.json').write_text('{}')
assert model_directory(bundle,fallback)==fallback
for name in REQUIRED_FILES:(local/name).write_bytes(b'x')
assert model_directory(bundle,fallback)==local
source=Path(temporary.name)/'verified-source';source.mkdir()
hashes={}
for name in REQUIRED_FILES:
    content=('fixture '+name).encode();(source/name).write_bytes(content);hashes[name]=hashlib.sha256(content).hexdigest()
(source/'manifest.json').write_text(json.dumps({'revision':REVISION,'sha256':hashes}))
installed=Path(temporary.name)/'installed';report=install(source,installed)
assert report['verified_files']==len(REQUIRED_FILES) and (installed/'manifest.json').exists()
(source/REQUIRED_FILES[0]).write_bytes(b'corrupt')
try:install(source,Path(temporary.name)/'must-not-publish');raise AssertionError('Corruption accepted')
except RuntimeError:assert not (Path(temporary.name)/'must-not-publish'/'manifest.json').exists()
core=Core()
try:read_ir(core,path);raise AssertionError('Missing weights accepted')
except RuntimeError as error:assert '测试.bin' in str(error) and not core.calls
path.with_suffix('.bin').write_bytes(b'\0')
assert read_ir(core,path)=='model' and isinstance(core.calls[0],Path)
core=Core(['Could not open the file: test.xml'])
assert read_ir(core,path)=='model' and len(core.calls)==2 and core.calls[-1]==path.resolve().as_posix()
core=Core(['Invalid graph'])
try:read_ir(core,path);raise AssertionError('Graph error hidden')
except RuntimeError:assert len(core.calls)==1
core=Core(['Could not open the file: test.xml']*2)
try:read_ir(core,path);raise AssertionError('Permanent failure hidden')
except RuntimeError:assert len(core.calls)==2
qt=QApplication([]);s=default_settings();s['translate']=False;save_settings(s)
worker=AudioWorker(s)
def fail(options):raise RuntimeError('Could not open the file: '+str(path))
worker._load_engine=fail
try:worker.load_engine(s);raise AssertionError('Load failure hidden')
except RuntimeError:pass
assert 'Could not open' in (DATA/'model-load-error.log').read_text('utf-8')
worker.emit('ready',{'load_error':'fixture'})
assert json.loads((DATA/'health.json').read_text())['state']=='model_load_failed'
ready=[]
def event(kind,value):
    if kind=='ready':
        ready.append(value);worker.quit_requested.set();worker.commands.put(None)
loop=QEventLoop();worker.event.connect(event);worker.finished.connect(loop.quit)
worker.start();QTimer.singleShot(5000,loop.quit);loop.exec();assert worker.wait(1000)
assert ready and ready[0].get('load_error') and not worker.ready
assert json.loads((DATA/'health.json').read_text())['state']=='model_load_failed'
w=Window(start_workers=False);w.show();qt.processEvents()
w.on_event('ready',{'load_error':'A long diagnostic path '+str(path)*50});qt.processEvents()
assert 'A long diagnostic' in w.state.toolTip() and len(w.state.text())<80
w.resize(860,620);qt.processEvents();assert w.width()==860
w.on_event('error','Could not open the file: '+str(path)*50);qt.processEvents()
assert w.width()==860 and len(w.state.text())<100 and str(path) in w.state.toolTip()
w.close();loop=QEventLoop();QTimer.singleShot(400,loop.quit);loop.exec()
assert not w.translator.isRunning()
summary=dict(missing_weight_detected_before_openvino=True,unicode_absolute_path=True,
    complete_program_local_model_preferred=True,incomplete_bundle_uses_cache=True,
    installer_verifies_source_and_copy=True,corrupt_source_not_published=True,
    file_open_failure_retried_once=True,graph_errors_not_retried=True,persistent_failure_propagates=True,
    startup_error_logged=True,failed_health_state=True,long_error_does_not_expand_window=True,
    no_audio_or_cloud_used=True)
(output/'model-startup-summary.json').write_text(json.dumps(summary,indent=2),'utf-8')
print(json.dumps(summary,indent=2),flush=True);temporary.cleanup()
