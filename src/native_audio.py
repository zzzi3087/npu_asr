"""Optional bounded C++ audio kernels; no models, threads or filesystem writes."""
import ctypes as c
import os
import numpy as np
from app_paths import APP_DIR,RESOURCE_DIR,FROZEN


class GainState(c.Structure):
    _fields_=[(name,c.c_double) for name in ('gain','input_db','output_db','manual','maximum',
        'target','ceiling','attack','release')]+[(name,c.c_int32) for name in ('enabled','limited','block_size')]


def load_library():
    if os.name!='nt' or os.environ.get('PARAKEET_NATIVE_AUDIO','1')=='0':return None
    path=(RESOURCE_DIR/'native' if FROZEN else APP_DIR/'build'/'native')/'audio_native.dll'
    if not path.is_file():return None
    try:
        # These sub-millisecond kernels do not call Python or block. Retaining
        # the GIL avoids a thread handoff on every 32 ms microphone block.
        lib=c.PyDLL(str(path),winmode=0x1100)
        lib.lc_audio_abi.restype=c.c_uint32;lib.lc_gain_state_size.restype=c.c_size_t
        if lib.lc_audio_abi()!=1 or lib.lc_gain_state_size()!=c.sizeof(GainState):return None
        lib.lc_gain_process.argtypes=[c.POINTER(GainState),c.c_void_p,c.c_void_p,c.c_size_t,c.c_int]
        lib.lc_gain_process.restype=c.c_int
        lib.lc_quiet_gain.argtypes=[c.c_void_p,c.c_size_t,c.c_double,c.c_double]
        lib.lc_quiet_gain.restype=c.c_double
        return lib
    except (OSError,AttributeError):return None


LIBRARY=load_library()


class NativeGain:
    def __init__(self):self.state=GainState(gain=1.,input_db=-120.,output_db=-120.)

    def configure(self,enabled,manual,maximum,target,ceiling):
        s=self.state;s.enabled=enabled;s.manual=manual;s.maximum=maximum;s.target=target;s.ceiling=ceiling

    def process(self,block,speech):
        output=np.empty_like(block)
        if not LIBRARY.lc_gain_process(c.byref(self.state),block.ctypes.data,output.ctypes.data,block.size,speech):
            raise RuntimeError('原生音频处理参数无效')
        return output


def native_quiet_gain(block,maximum,target):
    return LIBRARY.lc_quiet_gain(block.ctypes.data,block.size,maximum,target)
