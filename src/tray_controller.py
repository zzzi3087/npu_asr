"""Static native tray icon, updated only when application state changes."""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QSystemTrayIcon,QMenu
from app_paths import VERSION


class TrayController(QObject):
    def __init__(self,window,factory=None):
        super().__init__(window)
        self.window=window;self.factory=factory or QSystemTrayIcon;self.icon=None;self._key=None
        self.menu=QMenu(window)
        self.show_action=self.menu.addAction('显示主窗口',window.restore_from_tray)
        self.hide_action=self.menu.addAction('隐藏到托盘',window.hide_to_tray)
        self.menu.addSeparator()
        self.capture_action=self.menu.addAction('开始收音',self.toggle_capture)
        self.desktop_action=self.menu.addAction('桌面字幕');self.desktop_action.setCheckable(True)
        self.desktop_action.triggered.connect(lambda enabled:window.change_desktop(enabled=enabled))
        self.settings_action=self.menu.addAction('设置',self.open_settings)
        self.files_action=self.menu.addAction('音频文件与队列',window.open_files)
        self.menu.addSeparator()
        self.quit_action=self.menu.addAction('退出',window.request_quit)
        self.menu.aboutToShow.connect(self.refresh)
        if self.factory.isSystemTrayAvailable():
            self.icon=self.factory(window.windowIcon(),window)
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self.activated)
            self.refresh();self.icon.show()

    @property
    def available(self):
        return self.icon is not None and self.factory.isSystemTrayAvailable()

    def refresh(self):
        w=self.window
        key=(w.start.text(),w.start.isEnabled(),w.capturing,w.downloading,w.closing,w.file_queue.active,w.desktop.options['enabled'])
        if key==self._key:return
        self._key=key
        self.capture_action.setText(key[0]);self.capture_action.setEnabled(bool(key[1] and w.worker and not w.closing))
        self.desktop_action.setChecked(key[-1])
        for action in (self.show_action,self.hide_action,self.settings_action,self.files_action,self.quit_action):action.setEnabled(not w.closing)
        status='正在退出' if w.closing else ('正在收音' if w.capturing else ('文件转录中' if w.file_queue.active else ('模型下载中' if w.downloading else '已停止收音')))
        if self.icon:self.icon.setToolTip(f'实时字幕 {VERSION} · {status}')

    def toggle_capture(self):
        self.window.toggle();self.refresh()

    def open_settings(self):
        self.window.restore_from_tray();self.window.open_settings()

    def activated(self,reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick):
            self.window.restore_from_tray()

    def shutdown(self):
        if self.icon:self.icon.hide()
        self.menu.close()
