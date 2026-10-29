"""Conservative sentence-prefix confirmation for text-only ASR hypotheses."""
import re
from text_rules import normalized,sentence_spans,terminal,rule_language

UNITS=re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\s\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+')


class TextAgreement:
    def __init__(self):self.confirmed=[];self.previous=''

    def offset(self,text):
        if not self.confirmed:return 0
        spans=list(UNITS.finditer(text));keys=[normalized(m.group()) for m in spans]
        count=len(self.confirmed)
        if keys[:count]==self.confirmed:return spans[count-1].end()
        # Usually the exact prefix suffices. When ASR revises an earlier word,
        # align only the bounded prefix, never match an arbitrary repeated suffix.
        keys=keys[:count+12];row=list(range(len(keys)+1))
        for i,word in enumerate(self.confirmed,1):
            current=[i]
            for j,key in enumerate(keys,1):
                current.append(min(row[j]+1,current[-1]+1,row[j-1]+(word!=key)))
            row=current
        end=min(range(len(row)),key=lambda j:(row[j],abs(j-count)))
        return spans[end-1].end() if end else 0

    def update(self,text,final=False,language='English'):
        """Return newly confirmed text, remaining preview and character offsets."""
        start=self.offset(text);tail=text[start:].strip();limit=0
        old=self.previous[self.offset(self.previous):].strip()
        if final:limit=len(tail)
        else:
            guards=rule_language(tail,language)
            # CJK punctuation is a separate unit; it must not masquerade as a
            # new word or break content agreement when a full stop arrives late.
            spans=[m for m in UNITS.finditer(tail) if normalized(m.group())]
            old_spans=[m for m in UNITS.finditer(old) if normalized(m.group())]
            keys=[normalized(m.group()) for m in spans]
            old_keys=[normalized(m.group()) for m in old_spans]
            stable=0
            for a,b in zip(keys,old_keys):
                if a!=b:break
                stable+=1
            # Formatting is not word content. New suffix words can confirm a
            # previously unpunctuated sentence without a third decoder pass.
            growing=len(keys)>len(old_keys) and stable==len(old_keys)
            for lo,hi in sentence_spans(tail,guards):
                part=tail[:hi]
                if not tail[hi:].strip() or not terminal(part.split()[-1],language=guards):break
                count=sum(m.end()<=hi for m in spans)
                if count>stable:break
                exact=old.startswith(part)
                if not exact and not (growing and len(keys)-count>=2):break
                limit=hi
        self.previous=text
        if not limit:return '',tail,start,start
        # Account for spaces skipped between the already-published prefix and tail.
        start=len(text)-len(text[start:].lstrip())
        end=start+limit
        self.confirmed=[normalized(m.group()) for m in UNITS.finditer(text[:end])]
        return tail[:limit].strip(),tail[limit:].strip(),start,end
