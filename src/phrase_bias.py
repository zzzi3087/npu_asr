"""Conservative TDT token-prefix bias; ASR neural calls remain on NPU.

This CPU table is an independent greedy-decoder experiment, not NeMo GPU-PB.
Blank/duration scores are untouched and strong acoustic alternatives win.
"""
from collections import deque
import hashlib
import numpy as np


class PhraseBias:
    def __init__(self,phrases,vocab,tokenizer_path,strength=.6):
        import sentencepiece as spm
        self.strength=float(strength)
        self.blank=len(vocab);self.multilingual=self.blank>1024
        if not 0<=self.strength<=2:raise ValueError('词组加权强度须为 0–2')
        if str(tokenizer_path).endswith('.json'):
            from tokenizers import Tokenizer
            processor=Tokenizer.from_file(str(tokenizer_path))
            size=processor.get_vocab_size(with_added_tokens=False);piece=processor.id_to_token
            encode=lambda text:processor.encode(text,add_special_tokens=False).ids
        else:
            with open(tokenizer_path,'rb') as handle:
                if hashlib.file_digest(handle,'sha256').hexdigest()!='5a3a82c48998709f9bc9f5db2924d99a899d20bf82550ca52272da7c7557b0a0':
                    raise ValueError('Parakeet 分词器校验失败，请重新安装固定修订的分词器')
            processor=spm.SentencePieceProcessor(model_file=str(tokenizer_path))
            size=processor.vocab_size();piece=processor.id_to_piece;encode=lambda text:processor.encode(text,out_type=int)
        if size!=self.blank or any(piece(i)!=vocab.get(str(i)) for i in range(self.blank)):
            raise ValueError('Parakeet 分词器与当前 NPU 模型词表不一致')
        children=[{}];depth=[0];terminal=[False];self.skipped=[]
        sequences=set()
        for phrase in phrases:
            if not self.multilingual and any(ord(c)>127 for c in phrase):self.skipped.append(phrase);continue
            for text in dict.fromkeys((phrase,phrase.lower(),phrase[:1].upper()+phrase[1:])):
                ids=tuple(encode(text))
                if not ids or any(i>=self.blank or piece(i).startswith('<') for i in ids) or len(ids)>64:self.skipped.append(phrase);continue
                sequences.add(ids)
        for ids in sorted(sequences):
            node=0
            for token in ids:
                if token not in children[node]:
                    children[node][token]=len(children);children.append({});depth.append(depth[node]+1);terminal.append(False)
                node=children[node][token]
            terminal[node]=True
        if not sequences:raise ValueError('词表中没有可由当前 Parakeet 分词器表示的词组')
        if len(children)>2560:raise ValueError('词表过大，请缩减到当前主题的少量词组')
        self.transitions=np.zeros((len(children),self.blank),np.int32)
        failure=[0]*len(children);pending=deque()
        for token,node in children[0].items():self.transitions[0,token]=node;pending.append(node)
        while pending:
            node=pending.popleft();self.transitions[node]=self.transitions[failure[node]]
            for token,child in children[node].items():
                failure[child]=int(self.transitions[failure[node],token]);self.transitions[node,token]=child;pending.append(child)
        depths=np.array(depth,np.float32)
        # Small start bonus and bounded continuation bonus. No retrospective
        # backoff penalty is used: greedy search cannot undo an earlier token.
        reward=np.where(depths==0,0,np.where(depths==1,.35,1.+.25*np.log(np.maximum(depths,1))))
        self.scores=(np.minimum(reward,1.8)*self.strength)[self.transitions].astype(np.float32)
        self.candidates=[np.flatnonzero(row).astype(np.int32) for row in self.scores]
        self.state_count=len(children);self.sequence_count=len(sequences)

    def choose(self,logits,state,original=None):
        if original is None:original=int(np.argmax(logits[:self.blank+1]))
        # Never create a label on a frame whose acoustic winner is blank.
        if original==self.blank:return original,state
        ids=self.candidates[state]
        if len(ids):
            values=logits[ids]+self.scores[state,ids]
            values[logits[ids]<logits[original]-2.]=-np.inf
            best=int(np.argmax(values))
            token=int(ids[best]) if values[best]>logits[original] else original
        else:token=original
        return token,int(self.transitions[state,token])
