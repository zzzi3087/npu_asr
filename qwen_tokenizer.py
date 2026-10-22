"""Shared tokenizer-only preflight: no ASR model is executed on CPU."""
import json
from functools import lru_cache
from pathlib import Path
from tokenizers import Tokenizer,models,pre_tokenizers,decoders,AddedToken,Regex

@lru_cache(maxsize=2)
def load_tokenizer(folder):
    folder=Path(folder)
    vocab=json.loads((folder/'vocab.json').read_text('utf-8'))
    merges=[tuple(line.split()) for line in (folder/'merges.txt').read_text('utf-8').splitlines() if line and not line.startswith('#')]
    tokenizer=Tokenizer(models.BPE(vocab=vocab,merges=merges))
    tokenizer.pre_tokenizer=pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"),behavior='isolated'),
        pre_tokenizers.ByteLevel(add_prefix_space=False,use_regex=False)])
    tokenizer.decoder=decoders.ByteLevel()
    tokens=json.loads((folder/'tokenizer_config.json').read_text('utf-8'))['added_tokens_decoder']
    for key,value in sorted(tokens.items(),key=lambda item:int(item[0])):
        token=value['content']
        tokenizer.add_special_tokens([AddedToken(token,special=value.get('special',True),normalized=False)])
        if tokenizer.token_to_id(token)!=int(key):raise RuntimeError('特殊 token 编号不匹配')
    return tokenizer

def checked_context(tokenizer,text):
    if not isinstance(text,str) or len(text)>4096:raise ValueError('领域上下文过长')
    text=text.strip();ids=tokenizer.encode(text).ids
    special={i for i,t in tokenizer.get_added_tokens_decoder().items() if t.special}
    if special.intersection(ids):raise ValueError('领域上下文不能包含模型控制标记')
    if len(ids)>160:raise ValueError(f'领域背景与词表占用 {len(ids)} token，最多 160；请精简当前领域词表')
    return text,len(ids)
