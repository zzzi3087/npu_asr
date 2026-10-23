"""Speech-aware live gain with bounded amplification, slew and peak protection."""
import math
import numpy as np

DEFAULT_AGC={'enabled':True,'target_db':-26.,'max_gain_db':36.,'manual_db':0.,'ceiling_db':-1.}
MINIMUM_GAIN=10**(-24/20)

def normalize_agc(value=None):
    result=dict(DEFAULT_AGC)
    if isinstance(value,dict):
        result['enabled']=bool(value.get('enabled',True))
        for key,lo,hi in [('target_db',-36,-16),('max_gain_db',0,48),('manual_db',-24,24),('ceiling_db',-6,-.2)]:
            v=value.get(key,result[key])
            if type(v) in (int,float) and math.isfinite(v) and lo<=v<=hi:result[key]=float(v)
    return result


class DynamicGain:
    def __init__(self,settings=None):
        self.gain=1.;self.input_db=-120.;self.output_db=-120.;self.limited=False
        self._block_size=0;self.configure(settings)
    def configure(self,settings):
        self.settings=normalize_agc(settings)
        p=self.settings
        self.manual=10**(p['manual_db']/20);self.maximum=10**(p['max_gain_db']/20)
        self.target=10**(p['target_db']/20);self.ceiling=10**(p['ceiling_db']/20)
    def process(self,block,speech=True):
        p=self.settings;block=np.asarray(block,dtype=np.float32)
        size=block.size
        if not size:return block.copy()
        if size!=self._block_size:
            # Capture uses 512-sample blocks. Rebuild only for a changed size.
            self._block_size=size;self._ramp=np.linspace(0,1,size,dtype=np.float32)
            self._envelope=np.empty(size,np.float32)
            self._attack=math.exp(-size/(16000*.025));self._release=math.exp(-size/(16000*.35))
        rms=math.sqrt(float(np.dot(block,block))/size);peak=float(np.max(np.abs(block)))
        self.input_db=20*math.log10(max(rms,1e-6));self.limited=False
        desired=self.manual
        if p['enabled']:
            desired=self.gain
            if speech and rms>1e-8:desired=min(self.maximum,self.target/rms)*self.manual
            desired=max(MINIMUM_GAIN,min(desired,self.maximum))
        ceiling=self.ceiling
        if peak>1e-9:desired=min(desired,ceiling/peak)
        previous=self.gain
        coefficient=self._attack if desired<previous else self._release
        self.gain=desired+(previous-desired)*coefficient
        if peak*self.gain>ceiling:
            self.gain=ceiling/peak;previous=min(previous,self.gain);self.limited=True
        np.multiply(self._ramp,self.gain-previous,out=self._envelope)
        self._envelope+=previous
        output=block*self._envelope
        output_peak=float(np.max(np.abs(output)))
        if output_peak>ceiling:
            output*=ceiling/output_peak;self.limited=True
        out_rms=math.sqrt(float(np.dot(output,output))/size)
        self.output_db=20*math.log10(max(out_rms,1e-6))
        return output
    def metrics(self):
        return {'gain_db':round(20*math.log10(max(self.gain,1e-9)),1),
            'input_dbfs':round(self.input_db,1),'output_dbfs':round(self.output_db,1),'gain_limited':self.limited}
