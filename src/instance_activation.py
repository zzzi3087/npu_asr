"""User-scoped local activation, so reopening the EXE restores a hidden window."""
import hashlib
from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from app_paths import DATA


def activation_name():
    return 'LiveCaptions-'+hashlib.sha256(str(DATA.resolve()).casefold().encode('utf-8')).hexdigest()[:24]


def activate_existing(name=None):
    socket=QLocalSocket();socket.connectToServer(name or activation_name())
    if not socket.waitForConnected(1000):return False
    socket.write(b'SHOW');socket.flush()
    if socket.bytesToWrite():socket.waitForBytesWritten(500)
    acknowledged=socket.waitForReadyRead(1200) and bytes(socket.readAll())==b'OK'
    socket.disconnectFromServer();return bool(acknowledged)


class InstanceActivation(QObject):
    def __init__(self,window,name=None):
        super().__init__(window);self.window=window;self.name=name or activation_name();self.clients=set()
        self.server=QLocalServer(self)
        self.server.setMaxPendingConnections(4)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        # The caller already owns the application lock before removing a stale endpoint.
        QLocalServer.removeServer(self.name)
        self.server.newConnection.connect(self.accept)
        self.listening=self.server.listen(self.name)

    def accept(self):
        while self.server.hasPendingConnections():
            socket=self.server.nextPendingConnection();socket.setReadBufferSize(16);self.clients.add(socket)
            socket.readyRead.connect(lambda s=socket:self.receive(s))
            socket.disconnected.connect(lambda s=socket:self.disconnected(s))
            if socket.bytesAvailable():self.receive(socket)

    def receive(self,socket):
        if bytes(socket.peek(4))==b'SHOW':
            socket.readAll();self.window.restore_from_tray();socket.write(b'OK');socket.flush();socket.disconnectFromServer()
        elif socket.bytesAvailable()>=4:socket.disconnectFromServer()

    def disconnected(self,socket):
        self.clients.discard(socket);socket.deleteLater()

    def close(self):
        for socket in tuple(self.clients):socket.abort()
        self.server.close()
