"""Check IR pairs and normalize local paths before handing them to OpenVINO."""
import time
import stat
from pathlib import Path


def read_ir(core,filename):
    path=Path(filename).expanduser().resolve()
    missing=[]
    for item in (path,path.with_suffix('.bin')):
        try:
            info=item.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size==0:missing.append(item.name+'（不是有效文件或内容为空）')
        except OSError as error:missing.append(item.name+'（'+str(error)+'）')
    if missing:
        raise RuntimeError('模型文件缺失、为空或不可读取：'+', '.join(missing)+'；模型目录：'+str(path.parent))
    try:
        # The Path overload handles Windows filesystem paths directly.
        return core.read_model(path)
    except RuntimeError as error:
        message=str(error).lower()
        if not any(text in message for text in ('could not open the file','could not open file','cannot open file')):
            raise
        # Retry only a file-open error, once, with a normalized alternate spelling.
        # Graph/driver/compiler errors propagate immediately. Never change devices.
        time.sleep(.15)
        return core.read_model(path.as_posix())
