"""Offline boundary delivery contracts; no models, audio devices or API calls."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from segmentation import SentenceAssembler
from text_agreement import TextAgreement
from progressive_captions import DraftCaptions,StablePrefix


def words(text):return [(word,index*.2) for index,word in enumerate(text.split())]


def test_short_late_punctuation():
    for first,second,expected in [
        ('Ready We can continue','Ready. We can continue','Ready.'),
        ('The sensor is ready We are measuring','The sensor is ready. We are measuring','The sensor is ready.'),
    ]:
        assembler=SentenceAssembler()
        assert not assembler.update(words(first),words(first),1.5)
        result=assembler.update([],words(second),2.)
        assert [row['text'] for row in result]==[expected],result


def test_text_growth_and_revision():
    agreement=TextAgreement()
    assert not agreement.update('测量已经完成我们开始',language='Chinese')[0]
    assert agreement.update('测量已经完成。我们开始下一轮',language='Chinese')[0]=='测量已经完成。'
    for old,new in [
        ('The sensor is ready We begin','the sensor is ready. We begin measuring'),
        ('Ready We begin','Ready. We begin measuring'),
    ]:
        agreement=TextAgreement()
        assert not agreement.update(old)[0]
        result=agreement.update(new)[0]
        assert result and result.endswith('.'),(old,new,result)
    for old,new in [
        ('The sensor is ready. We begin','the sensor is ready. We begin'),
        ('The sensor is ready We begin','The sensor is ready. We begin'),
        ('The sensor is ready. We begin','The sensor is faulty. We begin measuring'),
        ('The energy is approximately One hundred GeV','The energy is approximately. One hundred GeV today'),
    ]:
        agreement=TextAgreement();assert not agreement.update(old)[0]
        assert not agreement.update(new)[0],(old,new)


def test_draft_lifecycle():
    d=DraftCaptions()
    event=dict(sentence_id=0,text='The detector measures particles',audio_start_ms=0,audio_end_ms=2000)
    row=d.add('one',event);old=row['id'];assert d.translated(old,'first',True)
    row=d.add('one',dict(event,text=event['text']+' during each experiment',audio_end_ms=4000))
    new=row['id'];assert new!=old and not d.translated(old,'stale',True)
    assert d.translated(new,'second',True)
    d.link('one',0,'final',event['text']+' during each experiment.')
    final=dict(id='final',en=event['text']+' during each experiment.',zh='',status='failed')
    assert d.rows('one',[final])[0]['zh']=='second'
    d.finish('final');assert not d.translated(new,'late',True)
    assert d.rows('one',[dict(final,status='done',zh='complete')])[0]['zh']=='complete'
    row=d.add('two',event);d.discard_session('two')
    assert not d.translated(row['id'],'deleted',True)


def test_reversible_candidates():
    p=StablePrefix(limit=1)
    first=p.offer('The sensor is ready. We begin',0,1.,0.,'English')
    assert first and first['text']=='The sensor is ready.' and first['translate']
    # The exhausted request budget must not freeze the visible source.
    shorter=p.offer('Ready. We begin',0,1.2,0.,'English')
    assert shorter and shorter['text']=='Ready.' and not shorter['translate']
    assert shorter['revisable']
    withdrawn=p.offer('Ready for the next measurement',0,1.4,0.,'English')
    assert withdrawn and withdrawn['withdraw']
    assert p.offer('Ready for the next measurement',0,1.6,0.,'English') is None
    for text in ('Dr. Smith measures particles','The energy is 125. GeV',
                 'The energy is approximately. One hundred GeV'):
        assert StablePrefix().offer(text,0,1.,0.,'English') is None,text
    p=StablePrefix()
    text='The detector measures many different particles, and the scientists compare these observations against detailed predictions'
    assert p.offer(text,0,2.,0.,'English') is None
    candidate=p.offer(text,0,3.,0.,'English')
    assert candidate and candidate['text'].endswith(','),candidate


def test_revisable_draft_replacement():
    d=DraftCaptions()
    event=dict(sentence_id=0,text='The sensor is ready.',audio_start_ms=0,audio_end_ms=1000,
               revisable=True,translate=True)
    row=d.add('one',event);old=row['id'];assert d.translated(old,'ready',True)
    for text in ('Ready.','The sensor is faulty.'):
        prior=row['id']
        row=d.add('one',dict(event,text=text,translate=False))
        assert row and row['en']==text and row['zh']=='' and row['id']!=prior
        assert not d.translated(prior,'outdated',True)
        assert d.rows('one',[])[0]['en']==text
    current=row['id']
    removed=d.add('one',dict(sentence_id=0,withdraw=True))
    assert removed and removed['id']==current and not d.entries
    assert not d.translated(current,'withdrawn',True)


if __name__=='__main__':
    test_short_late_punctuation()
    test_text_growth_and_revision()
    test_draft_lifecycle()
    test_reversible_candidates()
    test_revisable_draft_replacement()
    print(json.dumps(dict(short_late_punctuation=True,normalized_growth=True,
        speculative_boundary_guard=True,stale_draft_guard=True)))
