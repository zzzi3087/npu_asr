import os,sys
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')
if sys.stdout is None:sys.stdout=open(os.devnull,'w')
if sys.stderr is None:sys.stderr=open(os.devnull,'w')
_dll_handles=[]
for relative in ('openvino/libs','onnxruntime/capi','_soundfile_data','_sounddevice_data/portaudio-binaries'):
    folder=Path(sys._MEIPASS)/relative
    if folder.is_dir():_dll_handles.append(os.add_dll_directory(str(folder)))
