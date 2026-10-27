"""Stateful parity, clipping and buffer ownership for the optional C++ kernels."""
import argparse,json,os,subprocess,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from audio_gain import DynamicGain,quiet_gain
from native_audio import LIBRARY


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--require-native',action='store_true')
    args=parser.parse_args()
    if args.require_native:assert LIBRARY is not None,'Build native/audio_native.cpp first'
    native=DynamicGain();reference=DynamicGain(native=False)
    rng=np.random.default_rng(1701);max_error=0.;cases=0;previous=None;saved=None
    for index in range(3600):
        if index%73==0:
            settings={'enabled':bool(index%2),'target_db':float(rng.uniform(-36,-16)),
                'max_gain_db':float(rng.uniform(0,48)),'manual_db':float(rng.uniform(-24,24)),
                'ceiling_db':float(rng.uniform(-6,-.2))}
            native.configure(settings);reference.configure(settings)
        size=(0,1,64,512,1024,4096,16384,17000)[index%8]
        block=(rng.normal(0,10**rng.uniform(-8,0),size)).astype(np.float32)
        if index%11==0:block.fill(0)
        if index%13==0:block=block[::2] # Non-contiguous NumPy fallback and back again.
        original=block.copy();speech=bool(index%3)
        actual=native.process(block,speech);expected=reference.process(block,speech)
        np.testing.assert_allclose(actual,expected,atol=2e-6,rtol=2e-5)
        np.testing.assert_array_equal(block,original)
        if previous is not None:np.testing.assert_array_equal(previous,saved)
        assert not np.shares_memory(actual,block)
        previous=actual;saved=actual.copy()
        if size and actual.size:
            max_error=max(max_error,float(np.max(np.abs(actual-expected))))
            assert float(np.max(np.abs(actual)))<=native.ceiling+2e-6
        np.testing.assert_allclose([native.gain,native.input_db,native.output_db],
            [reference.gain,reference.input_db,reference.output_db],atol=2e-5,rtol=2e-5)
        if block.size:
            rms=float(np.sqrt(np.mean(block*block)));peak=float(np.max(np.abs(block)))
            expected_quiet=1. if rms<1e-9 else max(1.,min(32.,.025/rms,.9/max(peak,1e-9)))
            np.testing.assert_allclose(quiet_gain(block),expected_quiet,atol=2e-5,rtol=2e-5)
        else:assert quiet_gain(block)==1.
        cases+=1
    env=dict(os.environ,PARAKEET_NATIVE_AUDIO='0',PYTHONPATH=str(ROOT/'src'))
    subprocess.run([sys.executable,'-c',
        'from audio_gain import DynamicGain; assert DynamicGain().backend == "numpy"'],env=env,check=True)
    print(json.dumps({'cases':cases,'backend':native.backend,'max_sample_error':max_error,
        'fallback_and_buffer_ownership':True}))


if __name__=='__main__':main()
