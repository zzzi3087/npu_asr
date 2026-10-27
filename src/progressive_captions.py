"""Bounded, optional long-sentence previews; no timers or persistent draft rows."""
import uuid
import math
from text_agreement import UNITS
from text_rules import normalized,complete_text,joins_continuation,rule_language


def preview_limit(value=2):return value if type(value) is int and 1<=value<=10 else 2
def preview_interval(value=1.):return float(value) if type(value) in (int,float) and math.isfinite(value) and .5<=value<=5 else 1.


class StablePrefix:
    def __init__(self,limit=2,interval=1.):
        self.interval=preview_interval(interval)
        self.limit=preview_limit(limit);self.sentence=None;self.previous=[]
        self.sent=0;self.sent_at=-float('inf');self.sent_stable=0;self.sent_count=0

    def offer(self,text,sentence,now,start,language='auto'):
        if sentence!=self.sentence:
            self.sentence=sentence;self.previous=[];self.sent=0;self.sent_at=-float('inf');self.sent_stable=0;self.sent_count=0
        if self.sent>=self.limit:return None
        spans=list(UNITS.finditer(text));keys=[normalized(m.group()) for m in spans]
        stable=0
        for current,old in zip(keys,self.previous):
            if current!=old:break
            stable+=1
        self.previous=keys
        cjk=sum('\u3400'<=m.group()[0]<='\u9fff' or '\u3040'<=m.group()[0]<='\u30ff' for m in spans)>len(spans)/2
        minimum,small,large=(24,16,40) if cjk else (14,10,24)
        if now-start<6 or stable<minimum:return None
        if self.sent and (now-self.sent_at<self.interval or stable<self.sent_stable+(6 if cjk else 4)):return None
        small=max(small,self.sent_count+(8 if cjk else 6)) if self.sent else small
        large+=self.sent_count
        target=min(max(small,stable//2 if not self.sent else stable-4),large,stable-4);count=None
        guards=rule_language(text,language)
        for candidate in sorted(range(small,min(large,stable-4)+1),key=lambda n:abs(n-target)):
            word=spans[candidate-1].group();following=spans[candidate].group()
            prefix=text[:spans[candidate-1].end()]
            if complete_text(prefix,guards) and not joins_continuation(prefix,following,guards):
                count=candidate;break
        if count is None:return None
        self.sent+=1;self.sent_at=now;self.sent_stable=stable;self.sent_count=count
        return {'sentence_id':sentence,'text':text[:spans[count-1].end()].strip(),
            'audio_start_ms':int(start*1000),'audio_end_ms':int(now*1000),
            'source_language':language,'approximate_timestamps':True}


class DraftCaptions:
    def __init__(self):self.entries={}

    def add(self,sid,value):
        key=(sid,value['sentence_id'])
        previous=self.entries.get(key)
        if previous:
            if previous['final_id'] or value['text']==previous.get('pending_text',previous['en']):return None
            if len(list(UNITS.finditer(value['text'])))<=len(list(UNITS.finditer(previous['en']))):return None
            # Keep the readable old translation while the longer preview runs.
            # Rotate the request ID, so a late old result cannot replace it.
            previous.update(id='draft:'+uuid.uuid4().hex,pending_text=value['text'],status='pending',
                end_ms=value['audio_end_ms'])
            return previous
        while len(self.entries)>=16:self.entries.pop(next(iter(self.entries)))
        row=dict(id='draft:'+uuid.uuid4().hex,session_id=sid,sentence_id=value['sentence_id'],
            en=value['text'],zh='',start_ms=value['audio_start_ms'],end_ms=value['audio_end_ms'],
            status='pending',error='',uncertain=False,timing='approximate',provisional=True,final_id=None)
        self.entries[key]=row
        return row

    def find(self,ident):return next((r for r in self.entries.values() if r['id']==ident),None)

    def link(self,sid,sentence,ident,text=''):
        row=self.entries.get((sid,sentence))
        if row:
            if text and len(list(UNITS.finditer(text)))<max(len(list(UNITS.finditer(row['en']))),len(list(UNITS.finditer(row.get('pending_text',''))))):
                # A late punctuation revision may split inside the old preview.
                # Never let that preview cover text from a second final row.
                self.remove_request(row['id'])
            else:row['final_id']=ident
        return row

    def translated(self,ident,text,success):
        row=self.find(ident)
        if not row:return False
        if success:row.update(en=row.pop('pending_text',row['en']),zh=text,status='done')
        elif row['zh']:
            row.pop('pending_text',None);row['status']='done'
        else:
            # Failed optional translations must not strand a raw half-sentence.
            self.remove_request(ident)
        return True

    def remove_request(self,ident):
        self.entries={k:r for k,r in self.entries.items() if r['id']!=ident}

    def finish(self,ident):self.entries={k:r for k,r in self.entries.items() if r['final_id']!=ident}

    def discard_session(self,sid):self.entries={k:r for k,r in self.entries.items() if k[0]!=sid}

    def rows(self,sid,rows):
        entries=[r for r in self.entries.values() if r['session_id']==sid]
        replacements={r['final_id']:r for r in entries if r['final_id']}
        output=[]
        for row in rows:
            draft=replacements.get(row['id'])
            if draft and row['status']!='done' and (draft['zh'] or row['status'] in ('queued','pending')):
                # Keep durable source/error metadata while showing the prior
                # readable draft until the full translation succeeds.
                row=dict(row,en=draft['en'],zh=draft['zh'],provisional=True)
            output.append(row)
        output.extend(dict(r) for r in entries if not r['final_id'])
        return output
