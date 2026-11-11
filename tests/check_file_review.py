import os,sys,tempfile,json,wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QObject,Signal
from storage import History
from file_review_dialog import FileReviewDialog
qt=QApplication.instance() or QApplication([])
class Queue(QObject):
    changed=Signal()
class Window(QWidget):
    def cancel_review_cue(self,*args):self.cancelled=args
    def review_cue_saved(self,*args):pass
    def request_file_cue_translation(self,*args):self.requested=args;return True
with tempfile.TemporaryDirectory() as folder:
    h=History(Path(folder)/'test.db');sid=h.create_session('file',{'name':'test','model':'fake'},kind='file')
    ident=h.add(sid,'There are 3 kg.',1200,2400)
    h.update(ident,'done','有 5 千克。')
    from storage import text_fingerprint,batch_fingerprint
    batch={'batch_id':'b','items':[{'id':ident,'hash':text_fingerprint('There are 3 kg.')}],'source_hash':batch_fingerprint(['There are 3 kg.'])}
    run=h.create_batch_run(sid,h.translation_revision(sid),{'name':'test','model':'fake'},'Chinese','auto',[batch])
    claim=h.claim_batch(sid,run,'b')
    assert h.apply_batch_translations(sid,run,'b',claim['claim'],h.translation_revision(sid),{ident:'有 5 千克。'},[{'id':ident,'kind':'quantity','source':'3','target':'5'}])['ok']
    window=Window();window.history=h;window.file_queue=Queue()
    dialog=FileReviewDialog(window,sid)
    assert dialog.tree.topLevelItemCount()==1 and dialog.save.isEnabled()
    # Real FFmpeg trims the exact 1.2-second cue; avoid a physical audio device.
    import file_review_dialog as review
    import numpy as np
    original=Path(folder)/'source.wav'
    with wave.open(str(original),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        audio.writeframes(np.zeros(64000,dtype=np.int16).tobytes())
    played=[]
    class Stream:
        def __init__(self,*,samplerate,channels,dtype,callback):self.callback=callback;self.rate=samplerate
        def start(self):
            samples=np.zeros((57600,1),dtype=np.int16)
            try:self.callback(samples,len(samples),None,None)
            except review.sd.CallbackStop:pass
            played.append((len(samples),self.rate))
        def abort(self):pass
        def close(self):pass
    review.sd.OutputStream=Stream
    dialog.path=str(original);dialog.play_cue()
    assert dialog.process.waitForFinished(10000)
    assert dialog.process.arguments().index('-ss')<dialog.process.arguments().index('-i')
    assert played==[(57600,48000)],played
    with wave.open(dialog.clip,'rb') as clip:assert clip.getnframes()==57600
    dialog.retranslate();assert window.requested==(sid,ident)
    dialog.status.setText('未能请求');window.request_file_cue_translation=lambda *args:False
    dialog.retranslate();assert dialog.status.text()=='未能请求'
    assert '上下限' in review.flag_reason({'kind':'bound','source':'at least 3','target':'最多 3'})
    assert '3 → 译文 5' in review.flag_reason({'kind':'quantity','source':'3','target':'5'})
    dialog.edit.setPlainText('有 3 千克。');dialog.refresh()
    assert dialog.edit.toPlainText()=='有 3 千克。'
    dialog.save_edit();assert h.segment(ident)['zh']=='有 3 千克。' and not h.review_cues(sid)
    assert window.cancelled==(sid,ident)
    assert not h.apply_review_translation(sid,ident,'stale',[],expected_source='There are 3 kg.',expected_translation='有 5 千克。',expected_run_id=run)
    dialog.close();h.close()
print('review dialog checks passed')

