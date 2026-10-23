"""Event-driven appearance detection; no polling, registry writes or animations."""
import sys
from PySide6.QtCore import QObject,Signal,Qt,QTimer
from PySide6.QtGui import QGuiApplication


def system_dark(scheme=None):
    if scheme is None:scheme=QGuiApplication.styleHints().colorScheme()
    if scheme!=Qt.ColorScheme.Unknown:return scheme==Qt.ColorScheme.Dark
    # Headless/older Qt platform plugins may not report a scheme. Read once on
    # startup or notification, never repeatedly wake the machine to poll it.
    if sys.platform=='win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return not bool(winreg.QueryValueEx(key,'AppsUseLightTheme')[0])
        except OSError:pass
    return False


class SystemTheme(QObject):
    changed=Signal(bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.dark=system_dark()
        self.pending=self.dark
        # Qt emits colorSchemeChanged before updating its default palette.
        # Dispatch after that update and coalesce duplicate notifications.
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(0)
        self.timer.timeout.connect(self.flush)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.scheme_changed)

    def scheme_changed(self,scheme):
        self.pending=system_dark(scheme)
        self.timer.start()

    def flush(self):
        if self.pending!=self.dark:
            self.dark=self.pending
            self.changed.emit(self.dark)
