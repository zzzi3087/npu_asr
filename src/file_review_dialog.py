"""Review suspicious local-file translations against their original audio."""
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt,QProcess
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTreeWidget,
    QTreeWidgetItem,QPlainTextEdit,QFileDialog)
import wave
import numpy as np
import sounddevice as sd
from storage import cue_time
from media_processing import media_tools

REASONS={'quantity':'数量可能不一致','unit':'单位可能不一致','negation':'否定含义可能不一致','bound':'上下限含义可能不一致'}

def flag_reason(flag):
    reason=REASONS.get(flag.get('kind'),str(flag.get('kind','待核')))
    source=str(flag.get('source',''));target=str(flag.get('target',''))
    if source and target and len(source)+len(target)<=80:reason+=f'（原文 {source} → 译文 {target}）'
    return reason

class FileReviewDialog(QDialog):
    def __init__(self,window,sid,path=''):
        super().__init__(window);self.window=window;self.sid=sid;self.path=path;self.row=None;self.snapshot=None
        self.setWindowTitle('核对疑似译文');self.resize(860,650)
        layout=QVBoxLayout(self)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['原音时间','待核原因','原文']);self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0,190);self.tree.setColumnWidth(1,180);layout.addWidget(self.tree,1)
        self.source=QLabel();self.source.setWordWrap(True);self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(self.source)
        layout.addWidget(QLabel('译文（可直接修改）'))
        self.edit=QPlainTextEdit();self.edit.setAccessibleName('待核字幕译文');layout.addWidget(self.edit,1)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        reload_button=QPushButton('放弃草稿并刷新译文');reload_button.clicked.connect(self.reload_translation);layout.addWidget(reload_button)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        self.play=QPushButton('播放这段原音');self.play.clicked.connect(self.play_cue);buttons.addWidget(self.play)
        stop=QPushButton('停止播放');stop.clicked.connect(self.stop_audio);buttons.addWidget(stop)
        locate=QPushButton('选择原始文件');locate.clicked.connect(self.locate);buttons.addWidget(locate)
        buttons.addStretch()
        self.retry=QPushButton('仅重新翻译此句');self.retry.clicked.connect(self.retranslate);buttons.addWidget(self.retry)
        self.save=QPushButton('保存并标记已核对');self.save.clicked.connect(self.save_edit);buttons.addWidget(self.save)
        self.temp=tempfile.TemporaryDirectory(prefix='caption-review-');self.stream=None;self.stopping=False
        self.process=QProcess(self);self.process.finished.connect(self.decoded)
        self.process.errorOccurred.connect(lambda *_:self.status.setText('原音解码失败，请检查文件和 FFmpeg。'))
        self.tree.itemSelectionChanged.connect(self.select);self.refresh()

    def refresh(self):
        # Preserve unsaved edits while background queue progress arrives.
        selected=self.row['id'] if self.row else None
        dirty=bool(self.row and self.edit.toPlainText()!=self.row['zh'])
        rows=self.window.history.review_cues(self.sid);self.rows={row['id']:row for row in rows}
        stale=bool(dirty and (selected not in self.rows or self.rows[selected]['zh']!=self.row['zh'] or self.rows[selected]['en']!=self.row['en']))
        if dirty and selected not in self.rows:
            # Keep the edited cue selected until the user resolves their draft.
            rows.append(self.row);self.rows[selected]=self.row
        self.tree.blockSignals(True);self.tree.clear()
        for row in rows:
            item=QTreeWidgetItem([cue_time(row['start_ms'])+' – '+cue_time(row['end_ms']),
                '、'.join(dict.fromkeys(flag_reason(flag) for flag in row['flags'])),row['en']])
            item.setData(0,Qt.ItemDataRole.UserRole,row['id']);self.tree.addTopLevelItem(item)
            if row['id']==selected:self.tree.setCurrentItem(item)
        if not self.tree.currentItem() and self.tree.topLevelItemCount():self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.blockSignals(False)
        self.summary.setText(f'待核对 {len(rows)} 句 · 数量、单位和否定提示可能有误报，请听原音后判断。')
        if not dirty:self.select()
        self.update_buttons()
        if stale:self.status.setText('这句已在后台更新；你的草稿已保留。请先复制草稿，再点击“放弃草稿并刷新译文”核对最新结果。')

    def update_buttons(self):
        run=self.window.history.batch_run(self.sid)
        available=bool(self.row and run and run['status'] in ('completed','failed'))
        self.save.setEnabled(available);self.retry.setEnabled(available);self.edit.setReadOnly(not available)
        self.play.setEnabled(bool(self.row))
        if run and run['status'] not in ('completed','failed'):self.status.setText('整份文件翻译结束后可保存修改或单句重译。')

    def select(self):
        item=self.tree.currentItem();ident=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        self.row=self.rows.get(ident);self.snapshot=self.window.history.batch_run(self.sid)
        self.source.setText(self.row['en'] if self.row else '没有待核对的字幕。')
        self.edit.setPlainText(self.row['zh'] if self.row else '');self.update_buttons()

    def reload_translation(self):
        self.row=None;self.refresh();self.status.setText('已载入最新待核译文。')

    def save_edit(self):
        if not self.row or not self.snapshot:return
        ok=self.window.history.apply_review_translation(self.sid,self.row['id'],self.edit.toPlainText(),[],
            expected_source=self.row['en'],expected_translation=self.row['zh'],expected_run_id=self.snapshot['run_id'])
        if not ok:self.status.setText('字幕已在后台更新；请复制草稿，再点击“放弃草稿并刷新译文”核对。');return
        self.window.cancel_review_cue(self.sid,self.row['id'])
        self.row=None;self.window.review_cue_saved(self.sid)
        self.status.setText('已保存译文并移除这句的待核标记。')

    def retranslate(self):
        if self.row and self.window.request_file_cue_translation(self.sid,self.row['id']):
            self.status.setText('正在重新翻译这一句；仍可手动修改并保存。')

    def locate(self):
        path,_=QFileDialog.getOpenFileName(self,'选择此字幕对应的原始音频或视频',self.path)
        if path:self.path=path

    def stop_audio(self):
        if self.stream is not None:
            self.stream.abort();self.stream.close();self.stream=None
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            self.stopping=True;self.process.kill();self.process.waitForFinished(1000);self.stopping=False

    def play_cue(self):
        if not self.row:return
        if not Path(self.path).is_file():self.status.setText('找不到原始文件，请先选择此字幕对应的原始音频或视频。');return
        self.stop_audio()
        try:ffmpeg,_=media_tools()
        except RuntimeError as error:self.status.setText(str(error));return
        self.clip=str(Path(self.temp.name)/'cue.wav')
        start=self.row['start_ms']/1000;duration=(self.row['end_ms']-self.row['start_ms'])/1000
        self.status.setText('正在准备这段原音…')
        self.process.start(ffmpeg,['-hide_banner','-nostdin','-v','error','-y','-ss',f'{start:.3f}',
            '-i',self.path,'-t',f'{duration:.3f}','-vn','-ac','1','-ar','48000',self.clip])

    def decoded(self,code,*_):
        if self.stopping:return
        if code==0 and Path(self.clip).is_file():
            try:
                with wave.open(self.clip,'rb') as audio:
                    samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype=np.int16)
                    rate=audio.getframerate()
                position=0
                def output(data,frames,time,status):
                    nonlocal position
                    count=min(frames,len(samples)-position);data.fill(0)
                    data[:count,0]=samples[position:position+count];position+=count
                    if position>=len(samples):raise sd.CallbackStop()
                self.stream=sd.OutputStream(samplerate=rate,channels=1,dtype='int16',callback=output)
                self.stream.start()
                self.status.setText('正在播放所选字幕的原始时间范围。')
            except Exception as error:self.status.setText('无法播放原音：'+str(error))
        elif code:self.status.setText('无法播放原音：'+bytes(self.process.readAllStandardError()).decode('utf-8','replace')[-350:])

    def closeEvent(self,event):
        self.stop_audio();super().closeEvent(event)

