"""Isolated downloader checks using a local HTTP server; never uses user data."""
import hashlib,json,os,sys,threading,time
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler

temporary=TemporaryDirectory(prefix='captions-download-');base=Path(temporary.name)
os.environ['PARAKEET_DATA_DIR']=str(base/'data');os.environ['PARAKEET_MODEL_ROOT']=str(base/'models')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import model_manager as manager
payload=bytes(range(256))*8192;requests=[];ignore_range=False;fail_once=False;slow=False
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        global fail_once
        offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
        requests.append(offset)
        if fail_once:
            fail_once=False;self.send_error(503);return
        if ignore_range:offset=0
        self.send_response(206 if offset else 200)
        self.send_header('Content-Length',str(len(payload)-offset))
        if offset:self.send_header('Content-Range',f'bytes {offset}-{len(payload)-1}/{len(payload)}')
        self.end_headers()
        try:
            for i in range(offset,len(payload),256*1024):
                self.wfile.write(payload[i:i+256*1024])
                if slow:time.sleep(.1)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
item={'name':'fixture.dat','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'url':f'http://127.0.0.1:{server.server_port}/fixture'}
catalog={}
def fixture(name):
    catalog[name]={'name':name,'directory':name,'files':[dict(item)]};return base/'models'/name/'fixture.dat'
manager.catalog=lambda:catalog
def partial(name,data):
    target=fixture(name);target.parent.mkdir(parents=True);target.with_suffix('.dat.part').write_bytes(data);return target
results={}
try:
    target=fixture('fresh');manager.ensure_model('fresh');assert target.read_bytes()==payload
    results['fresh_download_verified']=True
    # Receipts avoid rehashing and networking on the next launch.
    receipts=list((base/'data/model-validation').glob('*.json'));mtime=receipts[0].stat().st_mtime_ns
    calls=len(requests);old_digest=manager.digest
    manager.digest=lambda *args:(_ for _ in ()).throw(AssertionError('warm cache rehashed'))
    manager.ensure_model('fresh');manager.digest=old_digest
    assert len(requests)==calls and receipts[0].stat().st_mtime_ns==mtime;results['warm_cache_no_rehash_or_write']=True
    # Remove the first catalog entry to force HTTP rather than local deduplication.
    catalog.clear();target=partial('resume',payload[:333333]);manager.ensure_model('resume')
    assert 333333 in requests and target.read_bytes()==payload;results['range_resume']=True
    catalog.clear();ignore_range=True;target=partial('ignore',payload[:12345]);manager.ensure_model('ignore');ignore_range=False
    assert target.read_bytes()==payload;results['server_without_range']=True
    catalog.clear();target=partial('corrupt',b'x'*len(payload));manager.ensure_model('corrupt')
    assert target.read_bytes()==payload;results['corrupt_complete_partial_replaced']=True
    catalog.clear();target=fixture('cancel');cancel=threading.Event();slow=True
    def report(value):
        if value['completed']>0:cancel.set()
    try:manager.ensure_model('cancel',cancel,report);raise AssertionError('cancellation ignored')
    except manager.DownloadCancelled:pass
    assert not target.exists() and target.with_suffix('.dat.part').exists();results['cancel_never_publishes_partial']=True
    slow=False;cancel.clear();manager.ensure_model('cancel',cancel);assert target.read_bytes()==payload
    results['resume_after_cancel']=True
    catalog.clear();target=fixture('bad-hash');catalog['bad-hash']['files'][0]['sha256']='0'*64
    try:manager.ensure_model('bad-hash');raise AssertionError('invalid hash accepted')
    except RuntimeError:pass
    assert not target.exists();results['bad_hash_rejected']=True
    catalog.clear();fail_once=True;target=fixture('retry');manager.ensure_model('retry');assert target.read_bytes()==payload
    results['temporary_network_error_retried']=True
    clone=fixture('clone');calls=len(requests);manager.ensure_model('clone')
    assert clone.read_bytes()==payload and len(requests)==calls;results['shared_assets_reused']=True
    print(json.dumps(results,indent=2))
finally:server.shutdown();server.server_close()
