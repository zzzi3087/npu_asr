"""Apply every released Mega block delta before exporting every neural pass."""
import os,sys,importlib.util,shutil,json
from pathlib import Path
import torch
from safetensors.torch import load_file
torch.set_num_threads(4);torch.set_num_interop_threads(1)
root=Path(os.environ['LOCALAPPDATA'])/'ParakeetNPUSubtitles/models'
output=(root/'mega-asr-1.7b-npu').resolve()
allowed={output,output.with_name(output.name+'.part')}
original_remove=shutil.rmtree
def bounded_remove(path,*args,**kwargs):
    resolved=Path(path).resolve()
    if resolved not in allowed:raise ValueError('Refusing delete outside exact export targets')
    return original_remove(resolved,*args,**kwargs)
shutil.rmtree=bounded_remove
spec=importlib.util.spec_from_file_location('qwen_export',Path(__file__).with_name('live-export.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original_load=module.load_checkpoint
audit=[]
def merged_load(folder,config):
    weights=original_load(folder,config)
    adapter=root/'mega-asr-source/mega-asr-merged'
    cfg=json.loads((adapter/'adapter_config.json').read_text())
    blocks=json.loads((adapter/'mega_lora_blocks.json').read_text())
    state=load_file(adapter/'adapter_model.safetensors')
    consumed=set()
    for name,a in state.items():
        if not name.endswith('.lora_A.weight'):continue
        bname=name.replace('.lora_A.weight','.lora_B.weight')
        b=state[bname].float();a=a.float();raw=name.removeprefix('base_model.model.').removesuffix('.lora_A.weight')
        target=raw.removeprefix('thinker.')+'.weight'
        if target.startswith('layers.'):target='model.'+target
        if target not in weights:raise ValueError('Missing merge target '+target)
        if 'embed_tokens' in target:raise ValueError('Embedding delta requires a separate LUT')
        parts=blocks.get(raw) or blocks.get(raw.removeprefix('thinker.'))
        if parts:
            delta=torch.zeros_like(weights[target]);covered=[]
            for part in parts:
                lo,hi=part['start'],part['end'];rank=part.get('rank',hi-lo);alpha=part.get('alpha',rank)
                delta.add_(b[:,lo:hi]@a[lo:hi],alpha=float(alpha)/rank)
                covered.extend(range(lo,hi))
            if sorted(covered)!=list(range(a.shape[0])):raise ValueError('Incomplete/overlapping rank blocks '+raw)
        else:
            rank=cfg.get('rank_pattern',{}).get(raw,cfg['r'])
            alpha=cfg.get('alpha_pattern',{}).get(raw,cfg['lora_alpha'])
            delta=(b@a)*(alpha/rank)
        if cfg.get('fan_in_fan_out'):delta=delta.T
        if delta.shape!=weights[target].shape or not torch.isfinite(delta).all():raise ValueError('Invalid delta '+raw)
        weights[target].add_(delta)
        audit.append({'target':target,'rank':a.shape[0],'blocks':len(parts or []),'delta_norm':float(delta.norm())})
        consumed.update((name,bname))
    if consumed!=set(state):raise ValueError('Unmerged adapter tensors')
    print('MERGED',len(audit),'targets;',len(consumed),'adapter tensors; audio and text both included',flush=True)
    return weights
module.load_checkpoint=merged_load
sys.argv=['export','--model',str(root/'mega-asr-source/Qwen3-ASR-1.7B'),'--out',str(output),
    '--source','zhifeixie/Mega-ASR@8a10bfd0cccb0c8b3be2852cae1e3ee73d6ffbf1/static-robust','--skip-checks']
module.main()
(output/'merge-audit.json').write_text(json.dumps({'targets':audit,'missing':[],'mode':'fixed robust; router disabled'},indent=2),'utf-8')
