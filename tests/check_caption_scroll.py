"""Offline caption follow/scroll/anchor and incremental-render regression.

No microphone, NPU, model download, credential read or external network is used.
All sessions and exports live in a temporary data directory.
"""
import argparse,json,os,random,statistics,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
temporary=TemporaryDirectory(prefix='caption-scroll-',ignore_cleanup_errors=True)
parser=argparse.ArgumentParser();parser.add_argument('--artifacts',type=Path);args=parser.parse_args()
output=args.artifacts or Path(temporary.name);output.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(Path(temporary.name)/'data')
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tools'))

from PySide6.QtCore import QEvent,QEventLoop,QPoint,QPointF,QCoreApplication,QTimer,Qt
from PySide6.QtGui import QFont,QFontDatabase,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QAbstractSlider,QMessageBox,QTextBrowser

from caption_view import CaptionView,row_identity
from storage import DATA,History,default_settings,export_session,save_settings
from theme import DEFAULT_COLORS
from typography import normalize_typography,render_captions
import settings_dialog
settings_dialog.get_key=lambda profile:(_ for _ in ()).throw(AssertionError('credential access forbidden in this test'))
from app import Window

qt=QApplication([])
for path in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf'):
    if Path(path).exists():QFontDatabase.addApplicationFont(path)
DATA.mkdir(parents=True)
base_settings=default_settings();base_settings['translate']=False;base_settings['desktop_captions']['enabled']=False
save_settings(base_settings)

REF=QTextBrowser();REF.resize(900,600);REF.show()
SETTINGS={'typography':normalize_typography(),'english_size':24,'chinese_size':22}


def settle(ms=30):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def new_window():
    window=Window(start_workers=False);window.show();settle(60)
    window.settings['translate']=False
    return window


def add_row(window,sid,index,zh=None,status='done'):
    en=f'Sentence {index}. The experiment searches for dark matter and axions in the universe today.'
    ident=window.history.add(sid,en,index*1000,index*1000+900,status=status)
    if zh is not None:window.history.update(ident,'done',zh)
    return ident


def make_session(window,count,translations=True):
    sid=window.history.create_session(f'Fixture {time.perf_counter_ns()}',window.profile())
    window.current_session=window.view_session=sid;window.capturing=True
    for index in range(count):
        zh=f'第{index}句：实验探索暗物质、轴子与宇宙的起源。' if translations and index%2==0 else None
        add_row(window,sid,index,zh=zh)
    window.render_now();settle(50)
    return sid


def expected_rows(window,sid):
    return window.drafts.rows(sid,window.history.rows(sid,window.row_limit))


def assert_matches(window,rows,tag,html=False):
    REF.document().setDefaultFont(window.browser.document().defaultFont())
    render_captions(REF,rows,window.colors,dict(window.settings,**window.appearance))
    assert [row_identity(row) for row in window.browser.visible_rows()]==[row_identity(row) for row in rows],tag
    assert window.browser.toPlainText()==REF.toPlainText(),tag
    if html:assert window.browser.document().toHtml()==REF.document().toHtml(),tag


def wheel(browser,delta):
    event=QWheelEvent(QPointF(20,20),QPointF(120,120),QPoint(0,0),QPoint(0,delta),
        Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    qt.sendEvent(browser.viewport(),event)


def rows_for(count,start=0):
    return [dict(id=str(index),logical_id=None,
        en=f'Sentence {index}. The experiment searches for dark matter and axions in the universe today.',
        zh=(f'第{index}句：实验探索暗物质、轴子与宇宙的起源。' if index%2==0 else ''),
        start_ms=index*1000,end_ms=index*1000+900,status='done',error='',
        uncertain=index%17==0,timing='approximate' if index%19==0 else 'word',
        provisional=index%23==0) for index in range(start,count)]


# 1. Deterministic demonstration of the legacy ordering bug and the new intent.
def test_legacy_ordering():
    data=rows_for(120)
    settings=dict(SETTINGS)
    base=QTextBrowser();base.resize(900,600);base.show();settle(20)
    render_captions(base,data,DEFAULT_COLORS,settings);settle(20)
    bar=base.verticalScrollBar();bar.setValue(bar.maximum());settle(20)
    position,follow=bar.value(),bar.value()>=bar.maximum()-30
    assert follow
    render_captions(base,data[1:]+rows_for(121,120),DEFAULT_COLORS,settings)
    settled_max=bar.maximum()
    # The legacy ordering restored against a transient, one-row-short range;
    # the later settled range is taller and the value is never corrected.
    bar.setRange(0,max(0,settled_max-180));bar.setValue(bar.maximum() if follow else position)
    bar.setRange(0,settled_max)
    legacy_gap=bar.maximum()-bar.value()
    assert legacy_gap>30,legacy_gap
    view=CaptionView();view.resize(900,600);view.show();settle(20)
    view.set_captions(data,DEFAULT_COLORS,settings,reset=True);settle(20)
    view_bar=view.verticalScrollBar();view_bar.setValue(view_bar.maximum());settle(20)
    view.set_captions(data[1:]+rows_for(121,120),DEFAULT_COLORS,settings);settle(20)
    settled_max=view_bar.maximum()
    view_bar.setRange(0,max(0,settled_max-180));settle(5)
    view_bar.setRange(0,settled_max);settle(20)
    assert view_bar.value()==view_bar.maximum()
    view.close();base.close();settle(20)
    return legacy_gap


# 2. Long live stream: appends, translations, range changes, widths and fonts.
def test_long_follow():
    window=new_window();sid=make_session(window,120)
    bar=window.browser.verticalScrollBar()
    baseline=window.browser.full_rebuild_count;font_changes=0;timings=[]
    for step in range(420):
        add_row(window,sid,120+step,zh=(f'第{120+step}句：新译文。' if step%2==0 else None))
        if step%3==0:
            rows=window.history.rows(sid,window.row_limit)
            if rows:window.history.update(rows[len(rows)//2]['id'],'done','中途更新的中文译文。')
        if step%40==39:
            narrow=(step//40)%2==0
            window.resize(860 if narrow else 1220,620 if narrow else 830);settle(10)
        if step%100==99:
            window.appearance['english_size']=24+(step//100)%3
            window.appearance['chinese_size']=22+(step//100)%2
            window.apply_colors(window.colors);font_changes+=1;settle(15)
        started=time.perf_counter();window.render_now();timings.append((time.perf_counter()-started)*1000)
        if step%10==9:settle(10)
    settle(40)
    assert window.browser.following
    assert bar.value()==bar.maximum(),(bar.value(),bar.maximum())
    assert window.browser.full_rebuild_count<=baseline+font_changes+2
    assert window.browser.incremental_update_count>=400
    assert_matches(window,expected_rows(window,sid),'long-follow',html=True)
    result=dict(updates=420,full_rebuilds=window.browser.full_rebuild_count,
        incremental=window.browser.incremental_update_count,font_changes=font_changes,
        median_update_ms=round(statistics.median(timings),2),
        p95_update_ms=round(sorted(timings)[int(len(timings)*.95)],2))
    window.close();settle(80)
    return result


# 3. Actual wheel/key/scrollbar actions pause and resume; programmatic moves do not.
def test_user_intent():
    window=new_window();sid=make_session(window,160)
    bar=window.browser.verticalScrollBar();assert window.browser.following
    wheel(window.browser,120);settle(50)
    assert not window.browser.following
    before=len(window.browser.visible_rows())
    for index in range(160,165):add_row(window,sid,index,zh=f'暂停新增{index}。')
    window.render_now();settle(30)
    assert len(window.browser.visible_rows())==before
    assert not window.browser.following
    value=bar.value()
    for index in range(165,170):add_row(window,sid,index,zh=f'暂停新增{index}。')
    window.render_now();settle(30)
    assert abs(bar.value()-value)<=2
    # Programmatic value/range changes must never resume following.
    bar.setValue(bar.maximum());settle(20);assert not window.browser.following
    maximum=bar.maximum();bar.setRange(0,maximum+200);settle(20)
    assert not window.browser.following
    bar.setRange(0,maximum);settle(20);assert not window.browser.following
    # Keyboard navigation.
    window.browser.setFocus();QTest.keyClick(window.browser,Qt.Key.Key_PageUp);settle(40)
    assert not window.browser.following
    QTest.keyClick(window.browser,Qt.Key.Key_End);settle(180)
    assert window.browser.following and bar.value()==bar.maximum()
    QTest.keyClick(window.browser,Qt.Key.Key_Home);settle(40)
    assert not window.browser.following
    QTest.keyClick(window.browser,Qt.Key.Key_End,Qt.KeyboardModifier.ControlModifier);settle(180)
    assert window.browser.following and bar.value()==bar.maximum()
    # Scrollbar thumb signals.
    bar.sliderPressed.emit();bar.sliderMoved.emit(0);bar.setValue(0);bar.sliderReleased.emit();settle(60)
    assert not window.browser.following
    bar.sliderPressed.emit();bar.setValue(bar.maximum());bar.sliderReleased.emit();settle(180)
    assert window.browser.following and bar.value()==bar.maximum()
    # A thumb drag defers document edits; the latest coalesced update applies on release.
    bar.setValue(bar.maximum());settle(20)
    visible_before=len(window.browser.visible_rows());revision=window.browser.document().revision()
    bar.sliderPressed.emit()
    for index in range(200,205):add_row(window,sid,index,zh=f'拖动期间新增{index}。')
    window.render_now();settle(30)
    assert len(window.browser.visible_rows())==visible_before
    assert window.browser.document().revision()==revision
    bar.sliderReleased.emit();settle(200)
    assert window.browser.following
    assert window.browser.visible_rows()[-1]['en'].startswith('Sentence 204.')
    window.close();settle(80)


# Large trailing padding still counts a fully visible last row as bottom; a
# partially visible (truncated) last row must not resume.
def test_bottom_semantics():
    typography=normalize_typography();typography.update(paragraph_gap=60,padding_y=40)
    settings={'typography':typography,'english_size':24,'chinese_size':22}
    view=CaptionView();view.resize(900,600);view.show();settle(20)
    view.set_captions(rows_for(60),DEFAULT_COLORS,settings,reset=True);settle(20)
    bar=view.verticalScrollBar()
    block=view.document().lastBlock()
    while block.isValid() and not block.text() and block.previous().isValid():block=block.previous()
    bottom=view.document().documentLayout().blockBoundingRect(block).bottom()
    target=max(0,int(bottom-view.viewport().height()))
    assert target<bar.maximum()-2,(target,bar.maximum())
    wheel(view,120);settle(40);assert not view.following
    bar.setValue(target);settle(10)
    assert view._bottom_reached()
    wheel(view,-1);settle(60)
    assert view.following
    wheel(view,120);settle(40);assert not view.following
    bar.setValue(max(0,target-250));settle(10)
    assert not view._bottom_reached()
    view.close();settle(20)


# 4. A paused reader keeps the same row and within-row offset through edits and load-more.
def test_reading_anchor():
    window=new_window();sid=make_session(window,200)
    wheel(window.browser,120);settle(60)
    assert not window.browser.following
    first=window.browser.reading_anchor();assert first
    for index in range(200,220):add_row(window,sid,index,zh=f'阅读期间新增{index}。')
    window.render_now();settle(50)
    second=window.browser.reading_anchor()
    assert second['key']==first['key'] and second['ordinal']==first['ordinal']
    assert abs(second['offset']-first['offset'])<=2,(first,second)
    visible=window.browser.visible_rows()
    target=next(row for row in visible if row.get('id') and not str(row['id']).startswith('draft:'))
    window.history.update(target['id'],'done','阅读位置附近的更新译文。')
    window.render_now();settle(50)
    third=window.browser.reading_anchor()
    assert third['key']==first['key'] and abs(third['offset']-first['offset'])<=2
    before=len(window.browser.visible_rows())
    window.load_more_rows();settle(180)
    fourth=window.browser.reading_anchor()
    assert fourth['key']==first['key'] and abs(fourth['offset']-first['offset'])<=2
    assert len(window.browser.visible_rows())>before
    assert not window.browser.following
    after_load_more=len(window.browser.visible_rows())
    rebuilds=window.browser.full_rebuild_count
    for index in range(220,230):add_row(window,sid,index,zh=f'大窗口暂停新增{index}。')
    window.render_now();settle(40)
    fifth=window.browser.reading_anchor()
    assert len(window.browser.visible_rows())==after_load_more
    assert window.browser.full_rebuild_count==rebuilds
    assert fifth['key']==first['key'] and abs(fifth['offset']-first['offset'])<=2
    assert not window.browser.following
    QTest.keyClick(window.browser,Qt.Key.Key_End);settle(200)
    assert window.browser.following
    assert window.browser.visible_rows()[-1]['en'].startswith('Sentence 229.')
    window.close();settle(80)
    return dict(anchor_rows=before,rows_after_load_more=after_load_more)


# 5. Draft revisions and final replacement use one logical row.
def test_draft_identity():
    window=new_window();sid=make_session(window,30)
    value=dict(sentence_id=30,text='A provisional sentence about axions.',revisable=True,
        translate=False,audio_start_ms=30000,audio_end_ms=32000,source_language='English')
    window.on_event('provisional',value);window.render_now();settle(30)
    rows=window.browser.visible_rows();assert len(rows)==31 and rows[-1].get('logical_id')
    logical=rows[-1]['logical_id'];rebuilds=window.browser.full_rebuild_count
    window.on_event('provisional',dict(value,text='A provisional sentence about axions and dark matter.'))
    window.render_now();settle(30)
    rows=window.browser.visible_rows()
    assert len(rows)==31 and rows[-1]['logical_id']==logical
    assert 'dark matter' in rows[-1]['en']
    window.on_event('committed',dict(text='A provisional sentence about axions and dark matter.',
        sentence_id=30,audio_start_ms=30000,audio_end_ms=33000))
    window.render_now();settle(30)
    rows=window.browser.visible_rows()
    assert len(rows)==31 and rows[-1]['logical_id']==logical and not rows[-1].get('provisional')
    assert len(window.history.rows(sid))==31
    assert window.browser.full_rebuild_count==rebuilds
    # A draft visible before pausing must finalize in place without losing the snapshot.
    window2=new_window();sid2=make_session(window2,30)
    value2=dict(value,sentence_id=30)
    window2.on_event('provisional',value2);window2.render_now();settle(30)
    logical2=window2.browser.visible_rows()[-1]['logical_id']
    wheel(window2.browser,120);settle(60);assert not window2.browser.following
    count=len(window2.browser.visible_rows())
    window2.on_event('committed',dict(text='A provisional sentence about axions.',
        sentence_id=30,audio_start_ms=30000,audio_end_ms=33000))
    window2.render_now();settle(30)
    rows=window2.browser.visible_rows()
    assert not window2.browser.following and len(rows)==count
    assert rows[-1]['logical_id']==logical2 and not rows[-1].get('provisional')
    assert len(window2.history.rows(sid2))==31
    window.close();window2.close();settle(100)
    return dict(logical_id=logical,one_row_after_final=True)


# 6. 500 random live operations compared with a complete reference render.
def test_random_updates():
    random.seed(7)
    window=new_window();sid=make_session(window,20);ids=[row['id'] for row in window.history.rows(sid,20)]
    for step in range(500):
        choice=random.random()
        if choice<.40:
            index=1000+step
            zh=f'随机第{index}句译文。' if random.random()<.7 else None
            ids.append(add_row(window,sid,index,zh=zh))
        elif choice<.65 and ids:
            window.history.update(random.choice(ids),'done',f'随机更新译文 {step}。')
        elif choice<.70:
            window.appearance['english_size']=22+step%7
            window.appearance['chinese_size']=20+step%6
            window.apply_colors(window.colors);settle(10)
        elif choice<.75:
            if random.random()<.5:window.hide_source.setChecked(not window.hide_source.isChecked())
            else:window.hide_translation.setChecked(not window.hide_translation.isChecked())
            settle(15)
        elif choice<.80:
            narrow=step%2==0
            window.resize(860 if narrow else 1220,620 if narrow else 830);settle(10)
        window.render_now();settle(1)
        rows=expected_rows(window,sid)
        assert_matches(window,rows,f'random-{step}',html=(step%25==0))
    rows=expected_rows(window,sid)
    assert_matches(window,rows,'random-final',html=True)
    window.hide_source.setChecked(False);window.hide_translation.setChecked(False);settle(40)
    window.close();settle(100)
    return dict(operations=500,last_rows=len(rows))


# 7. Custom fonts, colors and spacing must match the full renderer byte for byte.
def test_formatting():
    typography=normalize_typography()
    typography.update(paragraph_gap=60,padding_y=20,padding_x=14,bilingual_gap=12,
        meta_gap=9,english_line=1.6,chinese_line=1.5)
    colors=dict(DEFAULT_COLORS);colors.update(english='#123456',chinese='#654321',
        subtitle_meta='#777777',warning='#aa2200',muted='#555555')
    settings={'typography':typography,'english_size':31,'chinese_size':27,
        'show_source':True,'show_translation':True}
    data=rows_for(12)
    data.append(dict(id='extra',logical_id=None,en='Provisional source.',zh='临时译文。',
        start_ms=12000,end_ms=13000,status='pending',error='',uncertain=True,
        timing='approximate',provisional=True))
    view=CaptionView();view.resize(900,600);view.show();settle(20)
    REF.document().setDefaultFont(view.document().defaultFont())
    view.set_captions([],colors,settings,reset=True);settle(10)
    render_captions(REF,[],colors,settings);settle(5)
    assert view.document().toHtml()==REF.document().toHtml()
    view.set_captions(data,colors,settings,reset=True);settle(20)
    render_captions(REF,data,colors,settings);settle(5)
    assert view.document().toHtml()==REF.document().toHtml()
    view.set_captions(data[1:]+rows_for(14,12),colors,settings);settle(20)
    render_captions(REF,data[1:]+rows_for(14,12),colors,settings);settle(5)
    assert view.document().toHtml()==REF.document().toHtml()
    for source,translation in ((False,True),(True,False),(False,False)):
        changed=dict(settings,show_source=source,show_translation=translation)
        rows=data[1:]+rows_for(14,12)
        view.set_captions(rows,colors,changed);settle(20)
        render_captions(REF,rows,colors,changed);settle(5)
        assert view.document().toHtml()==REF.document().toHtml()
    view.close();settle(20)


# 8. Committed rows coalesce the sidebar refresh without losing highlight.
def test_sidebar_coalescing():
    window=new_window();sid=make_session(window,5)
    window.refresh_sessions();settle(30)
    row=window.history_rows[sid][1]
    assert '5 段' in row.detail.text()
    window.on_event('committed',dict(text='A coalesced committed sentence.',sentence_id=5,
        audio_start_ms=5000,audio_end_ms=6000))
    assert window._sessions_refresh_timer.isActive()
    assert window.view_session==sid
    settle(350)
    assert not window._sessions_refresh_timer.isActive()
    assert '6 段' in row.detail.text()
    assert window.history_rows[sid][0].isSelected()
    window.close();settle(100)


# 9. rows_by_ids must use the globally unique id key, not scan a session.
def test_rows_by_ids_plan():
    db_path=Path(temporary.name)/'rows-by-ids.sqlite3'
    history=History(db_path)
    sid=history.create_session('Visible rows',{'name':'fixture','model':'fixture'})
    visible=[history.add(sid,f'Visible {i}.',i*1000,i*1000+900,status='done') for i in range(100)]
    def measure():
        samples=[]
        for _ in range(7):
            started=time.perf_counter();rows=history.rows_by_ids(sid,visible)
            samples.append((time.perf_counter()-started)*1000)
            assert len(rows)==100
        return round(statistics.median(samples),3)
    timings={'100':measure()}
    other=history.create_session('Foreign rows',{'name':'fixture','model':'fixture'})
    for i in range(900):
        history.add(other,f'Foreign {i}.',i*1000,i*1000+900,status='done')
    timings['1000']=measure()
    for i in range(900,9900):
        history.add(other,f'Foreign {i}.',i*1000,i*1000+900,status='done')
    timings['10000']=measure()
    foreign=[row['id'] for row in history.rows(other,100)]
    marks=','.join('?'*len(visible))
    plan=[row[3] for row in history.db.execute(
        f'EXPLAIN QUERY PLAN SELECT * FROM segments WHERE id IN ({marks})',visible)]
    plan_text=' | '.join(plan).upper()
    assert 'SEGMENT_SESSION' not in plan_text,(plan,plan_text)
    assert 'ID=?' in plan_text,(plan,plan_text)
    assert 'SEARCH SEGMENTS' in plan_text,(plan,plan_text)
    assert not any('SCAN SEGMENTS' in item.upper() for item in plan),(plan,plan_text)
    rows=history.rows_by_ids(sid,visible)
    assert len(rows)==100 and {row['session_id'] for row in rows}=={sid}
    assert [row['seq'] for row in rows]==sorted(row['seq'] for row in rows)
    assert history.rows_by_ids(sid,foreign)==[]
    assert history.rows_by_ids(sid,[])==[]
    dedup=history.rows_by_ids(sid,visible[:5]+visible[:5]+['missing-id'])
    assert [row['id'] for row in dedup]==visible[:5]
    history.close()
    return {'query_plan':plan,'timings_ms':timings,
        'cross_session_empty':True,'dedup_and_missing_safe':True}


# 10. Clearing a session while the thumb is held must not resurrect old rows.
def test_clear_while_dragging():
    window=new_window();sid=make_session(window,5)
    bar=window.browser.verticalScrollBar()
    bar.sliderPressed.emit();wheel(window.browser,120)
    window.capturing=False
    original=QMessageBox.question
    QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.Yes
    try:window.delete_session(sid)
    finally:QMessageBox.question=original
    window.render_now();settle(80)
    assert window.browser.visible_rows()==[]
    assert window.browser._pending is None
    assert window.browser._rows==[] and window.browser._blocks==[]
    assert window.browser._anchor is None
    assert window.browser.following
    assert '准备好开始了' in window.browser.toPlainText()
    bar.sliderReleased.emit();settle(200)
    assert window.browser.visible_rows()==[]
    assert window.browser._pending is None
    assert '准备好开始了' in window.browser.toPlainText()
    sid2=window.history.create_session('Fresh session',window.profile())
    window.current_session=window.view_session=sid2;window.capturing=True
    for index in range(3):
        window.history.add(sid2,f'Fresh session row {index}.',index*1000,index*1000+900,status='done')
    window.render_now();settle(80)
    rows=window.browser.visible_rows()
    assert len(rows)==3 and window.browser.following
    text=window.browser.toPlainText()
    assert 'Fresh session row 2.' in text and 'Sentence 0.' not in text
    assert bar.value()==bar.maximum()
    window.close();settle(100)


# 11. A bounded view never removes persistent history from exports.
def test_export_retained():
    window=new_window();sid=make_session(window,1000,translations=False)
    assert len(window.browser.visible_rows())==100
    path=Path(temporary.name)/'history-export.txt'
    count=export_session(window.history,sid,path)
    assert count==1000
    text=path.read_text('utf-8')
    assert 'Sentence 0.' in text and 'Sentence 999.' in text
    window.close();settle(100)
    return count


# 9. Informational baseline/new timings; structural counters are the assertions.
def test_performance():
    report={}
    for count in (100,1000,3000):
        data=rows_for(count)
        base=QTextBrowser();base.resize(900,600);base.show();settle(10)
        render_captions(base,data,DEFAULT_COLORS,SETTINGS);settle(10)
        view=CaptionView();view.resize(900,600);view.show();settle(10)
        view.set_captions(data,DEFAULT_COLORS,SETTINGS,reset=True);settle(10)
        initial=view.full_rebuild_count
        baseline_times=[];incremental_times=[]
        for step in range(5):
            changed=data[1:]+rows_for(count+1+step,count+step)
            started=time.perf_counter();render_captions(base,changed,DEFAULT_COLORS,SETTINGS)
            baseline_times.append((time.perf_counter()-started)*1000)
            started=time.perf_counter();view.set_captions(changed,DEFAULT_COLORS,SETTINGS)
            incremental_times.append((time.perf_counter()-started)*1000)
            settle(1)
            assert view.full_rebuild_count==initial
            data=changed
        report[count]=dict(baseline_median_ms=round(statistics.median(baseline_times),2),
            incremental_median_ms=round(statistics.median(incremental_times),2),
            full_rebuilds=view.full_rebuild_count,blocks=view.document().blockCount())
        view.close();base.close();settle(20)
    return report


# 10. No idle document churn or timer-driven work.
def test_no_idle_work():
    view=CaptionView();view.resize(900,600);view.show();settle(20)
    view.set_captions(rows_for(100),DEFAULT_COLORS,SETTINGS,reset=True);settle(20)
    revision=view.document().revision();rebuilds=view.full_rebuild_count;updates=view.incremental_update_count
    settle(250)
    assert view.document().revision()==revision
    view.set_captions(rows_for(100),DEFAULT_COLORS,SETTINGS);settle(20)
    assert view.document().revision()==revision
    assert view.full_rebuild_count==rebuilds and view.incremental_update_count==updates
    assert not [timer for timer in view.findChildren(QTimer) if timer.isActive()]
    view.close();settle(20)


def main():
    summary={}
    summary['legacy_transient_gap_px']=test_legacy_ordering()
    summary['long_follow']=test_long_follow()
    test_user_intent();summary['wheel_key_thumb_pause_resume']=True
    summary['programmatic_value_and_range_do_not_resume']=True
    test_bottom_semantics();summary['large_padding_bottom_semantics']=True
    summary['reading_anchor']=test_reading_anchor()
    summary['draft_identity']=test_draft_identity()
    summary['random_updates']=test_random_updates()
    test_formatting();summary['custom_formatting_matches_full_render']=True
    test_sidebar_coalescing();summary['sidebar_refresh_coalesced']=True
    summary['rows_by_ids']=test_rows_by_ids_plan()
    test_clear_while_dragging();summary['clear_while_dragging']=True
    summary['export_rows_retained']=test_export_retained()
    summary['performance']=test_performance()
    test_no_idle_work();summary['no_idle_document_churn']=True
    summary['notes']='Offscreen timings are edit-call diagnostics, not CPU or power guarantees.'
    (output/'caption-scroll-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    temporary.cleanup()


if __name__=='__main__':main()
