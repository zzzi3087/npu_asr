"""Low-cost online sentence assembly after ASR word-prefix stabilization.

Heuristic implementation, inspired by LocalAgreement and streaming punctuation:
punctuation needs right context / repeated evidence; a pause alone is insufficient.
No language model, network call, or change to NPU acoustic inference is required.
"""
import re
from dataclasses import dataclass

ABBREVIATIONS = set('mr mrs ms dr prof sr jr st mt vs etc e.g i.e fig no inc ltd co corp dept approx est a.m p.m u.s u.k ph.d'.split())
DANGLING = set('a an the of to for with from at by in on into onto about and or but because if when while although whether which that whose than as this these those my your his her our their would could should will can may might must not'.split())
STARTERS = set('i you he she it we they this that these those there here however meanwhile therefore furthermore finally now then well yes no okay ok but and so'.split())
CLAUSE_STARTERS = set('but because while although however meanwhile therefore so yet'.split())
NUMBER_WORDS = set('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion percent dollars euros pounds'.split())
SHORT_REPLIES = {'yes','no','okay','ok','thanks','thank you','good morning','good afternoon','good evening','good night',
    'of course','all right','that is right',"that's right",'i agree','i understand','i see',"let's go",'sorry','sure','hello','hi'}
MODES = {
    'balanced':dict(soft=6.,hard=10.,target=22,maximum=34,idle=1.8),
    'sentence':dict(soft=10.,hard=14.,target=30,maximum=46,idle=2.2),
    'fast':dict(soft=4.,hard=7.,target=16,maximum=26,idle=1.4),
}


def plain(text):
    return text.lower().strip('.,!?;:…“”"()[]{}')


def terminal(text,next_text=''):
    clean=text.rstrip('"\'”’)]}')
    if not clean or clean[-1] not in '.?!':return ''
    if clean.endswith('...'):return ''
    if clean[-1] in '?!':return clean[-1]
    bare=clean[:-1].lower()
    if bare in ABBREVIATIONS or re.fullmatch(r'(?:[a-z]\.)+[a-z]?',bare):return ''
    if len(bare)==1 and bare.isalpha():return ''
    if re.search(r'https?://|www\.|@',clean,re.I):return ''
    if re.fullmatch(r'[\d,.]+',bare) and next_text and plain(next_text)[:1].isdigit():return ''
    return '.'


def number_part(text):
    pieces=re.split(r'[\s-]+',plain(text))
    return bool(pieces) and all(p in NUMBER_WORDS or re.fullmatch(r'[\d.,%]+',p) for p in pieces)


@dataclass
class Word:
    text: str
    stamp: float
    uncertain: bool=False
    stable_terminal: bool=False


class SentenceAssembler:
    def __init__(self,mode='balanced'):
        self.mode=MODES.get(mode,MODES['balanced'])
        self.words=[]
        self.previous=[]
        self.hypothesis=[]

    @staticmethod
    def find(word,hypothesis):
        # Match punctuation revisions to the same acoustic word, preserving
        # distinct ordinary repetitions. Number normalization happens upstream.
        from streaming import normalized
        matches=[(text,stamp) for text,stamp in hypothesis
                 if abs(stamp-word.stamp)<=.5 and normalized(text)==normalized(word.text)]
        return min(matches,key=lambda item:abs(item[1]-word.stamp)) if matches else None

    def update(self,new,hypothesis,now,uncertain=False,final=False,acoustic_end=False,idle=0.,pauses=()):
        self.words.extend(Word(text,stamp,uncertain) for text,stamp in new)
        if hypothesis is not None:
            self.hypothesis=hypothesis
            for word in self.words:
                current=self.find(word,hypothesis)
                old=self.find(word,self.previous)
                if current:
                    word.text=current[0]
                    word.stable_terminal=bool(terminal(current[0])) and bool(old) and terminal(current[0])==terminal(old[0])
            self.previous=list(hypothesis)
        results=[]
        while self.words:
            count,reason=self.boundary(now,final,acoustic_end,idle,pauses)
            if not count:break
            chosen=self.words[:count]
            self.words=self.words[count:]
            start=int(chosen[0].stamp*1000)
            end=min(now,chosen[-1].stamp+.6)
            if self.words:end=min(end,self.words[0].stamp)
            results.append({'text':' '.join(w.text for w in chosen),'audio_start_ms':start,
                'audio_end_ms':max(start+200,int(end*1000)), 'uncertain':any(w.uncertain for w in chosen),
                'boundary_reason':reason})
        return results

    def lookahead(self,index):
        word=self.words[index]
        if index+1<len(self.words):return self.words[index+1].text,self.words[index+1].stamp
        match=self.find(word,self.hypothesis)
        if match:
            pos=self.hypothesis.index(match)
            if pos+1<len(self.hypothesis):return self.hypothesis[pos+1]
        return '',word.stamp

    def pause_after(self,index,next_stamp,pauses):
        stamp=self.words[index].stamp
        return max((end-start for start,end in pauses if start>=stamp+.06 and end<=next_stamp+.2),default=0.)

    def complete(self,index):
        text=' '.join(plain(w.text) for w in self.words[:index+1])
        if text in SHORT_REPLIES:return True
        last=plain(self.words[index].text)
        if last in DANGLING:return False
        count=index+1
        if self.words[index].text.rstrip('\"\'”’)]}').endswith(('?','!')):return True
        if count>=5:return True
        tokens=text.split()
        finite={'is','are','was','were','am','has','have','had','do','does','did','agree','understand','know','think','works','sounds','arrived','ready',
                'see','go','stop','come','look','listen','wait','help','try','start','finished','done','left','won','lost','failed','passed'}
        return count>=2 and (bool(set(tokens)&finite) or tokens[0] in {'previously',"it's","that's","we're","they're","i'm","you're"})

    def safe_cut(self,index):
        if index<=0 or index>len(self.words):return False
        last=self.words[index-1].text
        if plain(last) in DANGLING:return False
        if index<len(self.words):
            next_word=self.words[index].text
            if number_part(last) and number_part(next_word):return False
            if last[:1].isupper() and next_word[:1].isupper() and plain(next_word) not in STARTERS:return False
        return True

    def boundary(self,now,final,acoustic_end,idle,pauses):
        # Emit the earliest completed sentence, never merge all periods into a
        # single batch. The right-context word need not itself be committed yet.
        for index,word in enumerate(self.words):
            next_text,next_stamp=self.lookahead(index)
            mark=terminal(word.text,next_text)
            if not mark or not self.complete(index):continue
            pause=self.pause_after(index,next_stamp,pauses)
            next_plain=plain(next_text)
            if mark=='.' and next_text:
                if next_text[:1].islower() and next_plain in DANGLING:continue
                if word.text[:1].isupper() and next_text[:1].isupper() and next_plain not in STARTERS and pause<.35:continue
            enough_context=bool(next_text) or acoustic_end or idle>=.35 or final
            supported=word.stable_terminal or final or (acoustic_end and idle>=.8)
            if supported and enough_context:return index+1,'sentence'
        if final:return len(self.words),'stop'
        if idle>=self.mode['idle']:
            return len(self.words),'pause'
        age=now-self.words[0].stamp
        hard=age>=self.mode['hard'] or len(self.words)>=self.mode['maximum']
        soft=age>=self.mode['soft'] and len(self.words)>=18
        if not (hard or soft):return 0,''
        candidates=[]
        for count in range(5,len(self.words)):
            if not self.safe_cut(count):continue
            word=self.words[count-1]
            next_word=self.words[count]
            pause=self.pause_after(count-1,next_word.stamp,pauses)
            score=0.
            if word.text.rstrip('"\'”’)]}').endswith((';',':')):score=4.
            elif word.text.rstrip('"\'”’)]}').endswith(','):score=3.
            if pause>=.35:score=max(score,3.+min(pause,1.))
            if plain(next_word.text) in CLAUSE_STARTERS:score=max(score,2.)
            if score:candidates.append((score-abs(count-self.mode['target'])/18.,count))
        if candidates:
            return max(candidates)[1],'clause'
        if hard:
            for count in range(min(len(self.words),self.mode['target']),0,-1):
                if self.safe_cut(count):return count,'length'
            if age>=self.mode['hard']+2:return len(self.words),'length'
        return 0,''

    @property
    def text(self):
        return ' '.join(w.text for w in self.words)
