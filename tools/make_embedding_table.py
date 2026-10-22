import os,json
from pathlib import Path
import numpy as np
from safetensors import safe_open
root=Path(os.environ['LOCALAPPDATA'])/'ParakeetNPUSubtitles/models'
source=root/'mega-asr-source/Qwen3-ASR-1.7B'
index=json.loads((source/'model.safetensors.index.json').read_text())['weight_map']
name='thinker.model.embed_tokens.weight'
with safe_open(source/index[name],framework='pt') as weights:
    table=weights.get_tensor(name).half().numpy()
    np.save(root/'qwen3-asr-1.7b-npu/embeddings.npy',table,allow_pickle=False)
print(table.shape,table.dtype)
