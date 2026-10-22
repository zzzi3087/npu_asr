"""Run the inspected upstream exporter with bounded output paths and RAM usage."""
import os,sys,importlib.util,shutil
from pathlib import Path
import torch
torch.set_num_threads(4);torch.set_num_interop_threads(1)
root=Path(os.environ['LOCALAPPDATA'])/'ParakeetNPUSubtitles/models'
output=(root/'qwen3-asr-1.7b-npu').resolve()
allowed={output,output.with_name(output.name+'.part')}
original_remove=shutil.rmtree
def bounded_remove(path,*args,**kwargs):
    resolved=Path(path).resolve()
    if resolved not in allowed:raise ValueError('Refusing delete outside exact export targets')
    return original_remove(resolved,*args,**kwargs)
shutil.rmtree=bounded_remove
spec=importlib.util.spec_from_file_location('qwen_export',Path(__file__).with_name('live-export.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.argv=['export','--model',str(root/'mega-asr-source/Qwen3-ASR-1.7B'),'--out',str(output),
    '--source','zhifeixie/Mega-ASR/Qwen3-ASR-1.7B@8a10bfd0cccb0c8b3be2852cae1e3ee73d6ffbf1','--skip-checks']
module.main()
