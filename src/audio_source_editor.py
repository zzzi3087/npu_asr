"""On-demand asynchronous device discovery, cached until manually refreshed."""
import os
from PySide6.QtCore import QThread,Signal,Qt
from PySide6.QtWidgets import QApplication,QWidget,QFormLayout,QComboBox,QPushButton,QLabel
from audio_sources import normalize_audio_source,microphone_devices,desktop_devices


class DeviceInventory(QThread):
    def __init__(self):
        super().__init__(QApplication.instance());self.result=None;self.errors={}
    def run(self):
        initialized=False
        if os.name=='nt':
            import ctypes
            initialized=ctypes.windll.ole32.CoInitializeEx(None,0) in (0,1)
        try:
            self.result={};self.errors={}
            for kind,read in [('microphone',microphone_devices),('desktop',desktop_devices)]:
                try:self.result[kind]=read()
                except Exception:self.result[kind]=[];self.errors[kind]='无法列出设备，请检查连接后刷新。'
        finally:
            if initialized:ctypes.windll.ole32.CoUninitialize()


class AudioSourceEditor(QWidget):
    def __init__(self,value=None,parent=None):
        super().__init__(parent);self.config=normalize_audio_source(value)
        form=QFormLayout(self);form.setContentsMargins(0,0,0,0);form.setVerticalSpacing(12)
        self.kind=QComboBox();self.kind.addItem('麦克风','microphone');self.kind.addItem('桌面音频（播放设备回环）','desktop')
        self.device=QComboBox();self.refresh=QPushButton('刷新音频设备')
        form.addRow('音频来源',self.kind);form.addRow('设备',self.device);form.addRow(self.refresh)
        self.note=QLabel('');self.note.setWordWrap(True);self.note.setObjectName('muted');form.addRow(self.note)
        app=QApplication.instance()
        if not hasattr(app,'_audio_inventory'):app._audio_inventory=DeviceInventory()
        self.inventory=app._audio_inventory;self.inventory.finished.connect(self.populate)
        self.kind.currentIndexChanged.connect(self.kind_changed);self.device.currentIndexChanged.connect(self.device_changed)
        self.refresh.clicked.connect(self.reload)
        self.set_values(value)
        if self.inventory.result is None and not self.inventory.isRunning():self.reload()

    def set_values(self,value):
        self.config=normalize_audio_source(value)
        self.kind.blockSignals(True);self.kind.setCurrentIndex(self.kind.findData(self.config['kind']));self.kind.blockSignals(False)
        self.populate()

    def kind_changed(self,*_):
        self.config={'kind':self.kind.currentData(),'device':''};self.populate()

    def device_changed(self,*_):
        if self.device.currentData() is not None:self.config['device']=self.device.currentData()

    def populate(self):
        kind=self.config['kind'];name=self.config['device']
        self.device.blockSignals(True);self.device.clear()
        self.device.addItem('系统默认播放设备' if kind=='desktop' else '系统默认麦克风','')
        for d in (self.inventory.result or {}).get(kind,[]):self.device.addItem(d['name'],d['name'])
        if name and self.device.findData(name)<0:self.device.addItem(name+'（当前未列出，请刷新确认）',name)
        self.device.setCurrentIndex(max(0,self.device.findData(name)));self.device.blockSignals(False)
        self.refresh.setEnabled(not self.inventory.isRunning())
        description=('采集所选播放设备上所有应用的声音，不同时采集麦克风。播放声音须输出到此设备。' if kind=='desktop' else '采集所选麦克风，不同时采集桌面音频。')
        self.note.setText(description+' 保存后下次收音生效。'+self.inventory.errors.get(kind,''))

    def reload(self):
        if not self.inventory.isRunning():self.refresh.setEnabled(False);self.inventory.start()

    def values(self):return normalize_audio_source(self.config,strict=True)
