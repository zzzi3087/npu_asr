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
            for lo,hi in sentence_spans(tail,guards):
                part=tail[:hi]
                if not tail[hi:].strip() or not terminal(part.split()[-1],language=guards):break
                if not old.startswith(part):break
                limit=hi
        self.previous=text
        if not limit:return '',tail,start,start
        # Account for spaces skipped between the already-published prefix and tail.
        start=len(text)-len(text[start:].lstrip())
        end=start+limit
        self.confirmed=[normalized(m.group()) for m in UNITS.finditer(text[:end])]
        return tail[:limit].strip(),tail[limit:].strip(),start,end
