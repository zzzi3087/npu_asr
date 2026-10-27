from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all,collect_dynamic_libs,collect_data_files,copy_metadata
root=Path(SPECPATH).parent
source=root/'src'
native_dll=root/'build'/'native'/'audio_native.dll'
if not native_dll.is_file():raise RuntimeError('Run python tools/build_native.py before packaging')
ov_data,ov_bins,ov_imports=collect_all('openvino')
hidden=ov_imports+['keyring.backends.Windows','win32ctypes.pywin32.win32cred','pyaudiowpatch',
    'onnxruntime.capi.onnxruntime_pybind11_state','onnxruntime.capi._pybind_state','tokenizers','sentencepiece']
data=ov_data+collect_data_files('onnxruntime')+collect_data_files('_sounddevice_data')+collect_data_files('_soundfile_data')
for package in ('openvino','keyring','tokenizers'):data+=copy_metadata(package)
data+=[(str(root/'resources'/'model-catalog.json'),'.'),(str(root/'resources'/'assets'/'app.ico'),'assets'),
    (str(root/'licenses'/'third-party'),'third-party-licenses'),(str(root/'licenses'/'THIRD_PARTY_NOTICES.md'),'.')]
media=Path(os.environ.get('LIVE_CAPTIONS_MEDIA_DIR',root/'build'/'media-tools'))
if not (media/'bin'/'ffmpeg.exe').is_file() or not (media/'bin'/'ffprobe.exe').is_file():
    raise RuntimeError('Run python tools/prepare_media_tools.py before packaging')
data+=[(str(media),'media-tools')]
weights={'.onnx','.bin','.npy','.npz','.model','.safetensors','.pt','.pth'}
data=[(source,target) for source,target in data if Path(source).suffix.lower() not in weights]
a=Analysis([str(source/'frozen_entry.py')],pathex=[str(source)],binaries=ov_bins+collect_dynamic_libs('onnxruntime')+[(str(native_dll),'native')],
    datas=data,hiddenimports=hidden,hookspath=[],runtime_hooks=[str(root/'packaging'/'runtime_hook.py')],
    excludes=['torch','tensorflow','transformers','scipy','pandas','matplotlib','IPython','pytest','PySide6.QtQml','PySide6.QtQuick'],
    noarchive=False,optimize=0)
pyz=PYZ(a.pure)
# Qt 6 uses Windows 11's native ICU ABI. Never bundle a same-named ICU from
# unrelated tools on the build machine's PATH (e.g. Poppler/Conda).
a.binaries=[entry for entry in a.binaries if not Path(entry[0]).name.lower().startswith(('icuuc','icudt','icuin'))]
# FFmpeg runs in its own directory and loads its adjacent shared libraries.
# Analysis also copies those dependencies to the bundle root; retain one copy.
media_dlls={path.name.lower() for path in (media/'bin').glob('*.dll')}
a.binaries=[entry for entry in a.binaries if not (len(Path(entry[0]).parts)==1 and Path(entry[0]).name.lower() in media_dlls)]
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='LiveCaptions',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,disable_windowed_traceback=False,
    icon=str(root/'resources'/'assets'/'app.ico'),version=str(root/'packaging'/'version-info.txt'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='LiveCaptions')
