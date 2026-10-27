"""Incremental sentence decisions, separate from acoustic window boundaries."""
from dataclasses import dataclass
import re
import math
from text_rules import (plain,normalized,terminal,number_part,complete_text,
    joins_continuation,sentence_spans,sentence_boundary,rule_language,join_text,CLAUSE_STARTERS,CLOSERS)

MODES={
    'balanced':dict(endpoint=.64,idle=1.2,incomplete=2.4,soft=8.,hard=26.,maximum=100),
    'sentence':dict(endpoint=.8,idle=1.6,incomplete=3.2,soft=12.,hard=34.,maximum=140),
    'fast':dict(endpoint=.48,idle=.85,incomplete=1.8,soft=5.,hard=20.,maximum=80),
}


def endpoint_samples(mode,quiet):
    return int((MODES.get(mode,MODES['balanced'])['endpoint']+(.128 if quiet else 0))*16000)


def confirmation_wait(value=3.):
    return float(value) if type(value) in (int,float) and math.isfinite(value) and .5<=value<=6 else 3.


class Hypothesis:
    def __init__(self,words=()):
        self.words=list(words);self.index={}
        for i,(text,stamp) in enumerate(self.words):
            self.index.setdefault(normalized(text),[]).append((stamp,i))

    def find(self,word):
        matches=self.index.get(normalized(word.text),())
        nearest=min(matches,key=lambda item:abs(item[0]-word.stamp),default=None)
        return nearest[1] if nearest is not None and abs(nearest[0]-word.stamp)<=.5 else None


@dataclass
class Word:
    text:str
    stamp:float
    uncertain:bool=False
    stable_terminal:bool=False
    end:float|None=None
    stable_context:bool=False


class SentenceAssembler:
    def __init__(self,mode='balanced',language='English',max_wait=6.):
        self.mode=MODES.get(mode,MODES['balanced'])
        self.max_wait=confirmation_wait(max_wait)
        self.language=language
        self.words=[];self.hypothesis=Hypothesis();self.previous=Hypothesis()
        self.next_check=float('inf');self._text='';self.scans=0
        self.sequence=0

    def update(self,new,hypothesis,now,uncertain=False,final=False,acoustic_end=False,idle=0.,pauses=(),ends=None):
        if not new and hypothesis is None and not final and now<self.next_check:return []
        self.words.extend(Word(text,stamp,uncertain) for text,stamp in new)
        if hypothesis is not None:
            self.hypothesis=Hypothesis(hypothesis)
            for word in self.words:
                current=self.hypothesis.find(word);old=self.previous.find(word)
                if current is not None:
                    word.text=self.hypothesis.words[current][0]
                    mark=terminal(word.text,language=self.language)
                    word.stable_terminal=bool(mark) and old is not None and mark==terminal(self.previous.words[old][0],language=self.language)
                    current_tail=self.hypothesis.words[current+1:current+3]
                    old_tail=self.previous.words[old+1:old+3] if old is not None else []
                    word.stable_context=(len(current_tail)==len(old_tail)==2 and
                        all(normalized(a)==normalized(b) and abs(t-u)<=.5 for (a,t),(b,u) in zip(current_tail,old_tail)))
                    if ends and current<len(ends):word.end=ends[current]
            self.previous=self.hypothesis
        results=[]
        while self.words:
            self.scans+=1
            count,reason=self.boundary(now,final,acoustic_end,idle,pauses)
            if not count:break
            chosen=self.words[:count];self.words=self.words[count:]
            start=int(chosen[0].stamp*1000);end=min(now,chosen[-1].end if chosen[-1].end is not None else chosen[-1].stamp+.6)
            if self.words:end=min(end,self.words[0].stamp)
            results.append({'text':' '.join(w.text for w in chosen),'audio_start_ms':start,
                'audio_end_ms':max(start+1,int(end*1000)),
                'uncertain':any(w.uncertain for w in chosen) or reason in {'limit','confirmation_timeout'},'boundary_reason':reason,'sentence_id':self.sequence})
            self.sequence+=1
        self._text=' '.join(w.text for w in self.words)
        if self.words:
            wait=self.mode['idle'] if complete_text(self._text,self.language) else self.mode['incomplete']
            self.next_check=now+max(.032,wait-idle)
        else:self.next_check=float('inf')
        return results

    def lookahead(self,index):
        if index+1<len(self.words):return self.words[index+1].text,self.words[index+1].stamp
        pos=self.hypothesis.find(self.words[index])
        if pos is not None and pos+1<len(self.hypothesis.words):return self.hypothesis.words[pos+1]
        return '',self.words[index].stamp

    def pause_after(self,index,next_stamp,pauses):
        stamp=self.words[index].stamp
        return max((end-start for start,end in pauses if start>=stamp+.06 and end<=next_stamp+.16),default=0.)

    def right_context(self,index):
        pos=self.hypothesis.find(self.words[index])
        if pos is not None:return ' '.join(w for w,_ in self.hypothesis.words[pos+1:pos+9])
        return ' '.join(w.text for w in self.words[index+1:index+9])

    def safe_cut(self,count,language):
        if count<=0 or count>len(self.words):return False
        left=' '.join(w.text for w in self.words[:count])
        return complete_text(left,language) and (count==len(self.words) or not joins_continuation(left,self.right_context(count-1),language))

    def boundary(self,now,final,acoustic_end,idle,pauses):
        text=' '.join(w.text for w in self.words);language=rule_language(text,self.language)
        for index,word in enumerate(self.words):
            next_text,next_stamp=self.lookahead(index)
            mark=terminal(word.text,next_text,language)
            if not mark:continue
            prefix=' '.join(w.text for w in self.words[:index+1])
            if not sentence_boundary(prefix,self.right_context(index),language):continue
            # In speech require agreement and right context. A genuine acoustic
            # endpoint can confirm a complete sentence in a single pass.
            # Stable following words also establish a boundary when the model
            # adds punctuation one pass later. No third recognition is needed.
            supported=final or acoustic_end or word.stable_terminal or (word.stable_context and not word.uncertain)
            if supported and (next_text or acoustic_end or final):return index+1,'sentence'
            ended=word.end if word.end is not None else word.stamp+.2
            if now-ended>=self.max_wait and len(self.right_context(index).split())>=2:
                return index+1,'confirmation_timeout'
        if final:return len(self.words),'stop'
        wait=self.mode['idle'] if complete_text(text,language) else self.mode['incomplete']
        if idle>=wait:return len(self.words),'pause'
        age=now-self.words[0].stamp
        hard=age>=self.mode['hard'] or len(self.words)>=self.mode['maximum']
        if not hard and (age<self.mode['soft'] or len(self.words)<14):return 0,''
        candidates=[]
        for count in range(4,len(self.words)):
            if not self.safe_cut(count,language):continue
            last=self.words[count-1].text;following=self.words[count].text
            pause=self.pause_after(count-1,self.words[count].stamp,pauses)
            prefix=' '.join(w.text for w in self.words[:count])
            if not complete_text(prefix,language):continue
            # A comma or elapsed word count alone does not justify a split.
            mark=last.rstrip(CLOSERS)[-1:]
            strong=mark in (';',':') or (mark==',' and (pause>=.35 or plain(following) in CLAUSE_STARTERS))
            if strong or pause>=.6:candidates.append((pause+(1 if strong else 0),count))
        if candidates:return max(candidates)[1],'clause'
        if hard:
            # Bounded fallback for pathological unpunctuated streams only.
            # Keep recent context and visibly mark an uncertain boundary.
            for count in range(len(self.words)-4,3,-1):
                if self.safe_cut(count,language):
                    return count,'limit'
            if len(self.words)>self.mode['maximum']*2:return len(self.words),'limit'
        return 0,''

    @property
    def text(self):return self._text


class TextAssembler:
    """Carry unfinished Qwen/Mega phrases across independently decoded audio.

    Text-only models have no word timing. Approximate offsets are never used for
    matching or deduplication; all display text and its original spacing survive.
    """
    def __init__(self,mode='balanced'):
        self.mode=MODES.get(mode,MODES['balanced']);self.text='';self.start=0.;self.end=0.
        self.language=None;self.next_check=float('inf')
        self.window_tail=False
        self.sequence=0

    def update(self,text='',start=0.,end=0.,now=0.,idle=0.,final=False,language='auto',window_limit=False):
        output=[]
        if not text and not final and now<self.next_check:return output
        if text:
            if self.text and language!=self.language:
                output.append(self.event(self.text,self.start,self.end,'language'));self.text='';self.window_tail=False
            if not self.text:self.start=start
            if self.text and self.window_tail and terminal(self.text.split()[-1],language=language):
                # A decoder may invent a full stop at a capacity cut. This last
                # phrase was deliberately held. Use the next window's context
                # to join continuations, preserving genuine complete sentences.
                right=text.lstrip()
                continuing=(not complete_text(self.text,language) or joins_continuation(self.text,right,language))
                if continuing:self.text=re.sub(r'[.!?。！？]+(?=[\"\'”’）)\]}」』》]*$)','',self.text)
            self.language=language;self.text=join_text(self.text,text.strip());self.end=end
            self.window_tail=False
            original=self.text;span_language=rule_language(original,language);spans=list(sentence_spans(original,span_language));consumed=0
            for lo,hi in spans:
                part=original[lo:hi].strip();following=original[hi:].strip()
                # At capacity, retain the last phrase until new context or a
                # real endpoint arrives. Window size is not sentence length.
                if not sentence_boundary(part,following,span_language) or (window_limit and not following):break
                finish=self.start+(self.end-self.start)*hi/max(1,len(original))
                begin=self.start+(self.end-self.start)*lo/max(1,len(original))
                output.append(self.event(part,begin,finish,'sentence'));consumed=hi
            if consumed:
                self.start=self.start+(self.end-self.start)*consumed/max(1,len(original))
                self.text=original[consumed:].strip()
            self.window_tail=bool(self.text) and window_limit
        if self.text:
            wait=self.mode['idle'] if complete_text(self.text,self.language) else self.mode['incomplete']
            limited=len(self.text)>2400
            if final or idle>=wait or limited:
                output.append(self.event(self.text,self.start,self.end,'stop' if final else 'limit' if limited else 'pause'))
                self.text=''
                self.window_tail=False
            self.next_check=now+max(.032,wait-idle) if self.text else float('inf')
        return output

    def event(self,text,start,end,reason):
        sequence=self.sequence;self.sequence+=1
        return {'text':text,'audio_start_ms':int(start*1000),'audio_end_ms':max(int(start*1000)+1,int(end*1000)),
            'approximate_timestamps':True,'source_language':self.language or 'auto',
            'boundary_reason':reason,'uncertain':reason=='limit','sentence_id':sequence}
