"""Fetch only the pinned tokenizer from NVIDIA's uncompressed NeMo archive.

HTTP ranges skip the 2.47 GB weight file. No archive path is extracted to disk.
"""
import io,requests,tarfile,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from download_models import MODEL_DIR
REV='ae9ad07059c7c739ffaf932226a8fe64ae2620b0'
URL=f'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2/resolve/{REV}/parakeet-tdt-0.6b-v2.nemo'
class RemoteTar(io.RawIOBase):
    def __init__(self):self.pos=0;self.length=2472222720;self.cache={}
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,pos,whence=0):
        self.pos=pos if whence==0 else self.pos+pos if whence==1 else self.length+pos
        return self.pos
    def read(self,size=-1):
        if size<0 or size>2**20:raise ValueError('metadata reader only')
        result=[]
        while size and self.pos<self.length:
            base=self.pos//65536*65536
            if base not in self.cache:
                end=min(self.length-1,base+65535)
                r=requests.get(URL+'?rangeblock='+str(base),headers={'Range':f'bytes={base}-{end}'},timeout=30)
                r.raise_for_status()
                assert r.status_code==206 and r.headers.get('Content-Range','').startswith(f'bytes {base}-'),r.headers
                self.cache[base]=r.content
            data=self.cache[base];off=self.pos-base;n=min(size,len(data)-off)
            result.append(data[off:off+n]);self.pos+=n;size-=n
        return b''.join(result)
stream=RemoteTar();tar=tarfile.open(fileobj=stream,mode='r:')
found=[]
for item in tar:
    print(item.name,item.size,item.offset_data,flush=True)
    if item.name.endswith('tokenizer.model'):
        assert item.size<2**20
        data=tar.extractfile(item).read();path=MODEL_DIR/'tokenizer.model'
        assert hashlib.sha256(data).hexdigest()=='5a3a82c48998709f9bc9f5db2924d99a899d20bf82550ca52272da7c7557b0a0'
        MODEL_DIR.mkdir(parents=True,exist_ok=True)
        partial=path.with_suffix('.model.part');partial.write_bytes(data);partial.replace(path)
        found.append({'file':str(path),'archive_member':item.name,'sha256':hashlib.sha256(data).hexdigest()})
assert len(found)==1
(MODEL_DIR/'tokenizer-source.json').write_text(json.dumps({'archive_url':URL,'revision':REV,'artifacts':found},indent=2))
print(json.dumps(found),flush=True)
