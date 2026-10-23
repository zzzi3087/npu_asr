"""Create the app icon and collect redistribution notices, without local metadata."""
import importlib.metadata as metadata
import json,os,re,shutil,struct,sys
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]

def main():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage,QPainter,QColor,QPen
    from PySide6.QtCore import Qt,QRectF,QBuffer,QIODevice
    qt=QApplication.instance() or QApplication([])
    images=[]
    for size in (16,32,48,64,128,256):
        img=QImage(size,size,QImage.Format.Format_ARGB32);img.fill(Qt.GlobalColor.transparent)
        p=QPainter(img);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(size/256,size/256)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#087f6c'));p.drawRoundedRect(QRectF(8,8,240,240),48,48)
        p.setBrush(QColor('#ffffff'));p.drawRoundedRect(QRectF(40,53,176,130),22,22)
        p.setBrush(QColor('#087f6c'))
        for x,y,width in ((61,84,134),(61,115,98),(61,146,118)):p.drawRoundedRect(QRectF(x,y,width,12),6,6)
        p.setPen(QPen(QColor('#ffffff'),10,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));p.drawLine(92,211,164,211);p.end()
        buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);img.save(buffer,'PNG');images.append((size,bytes(buffer.data())))
    icon=bytearray(struct.pack('<HHH',0,1,len(images)));offset=6+16*len(images)
    for size,data in images:
        icon+=struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(data),offset);offset+=len(data)
    for _,data in images:icon+=data
    (ROOT/'resources/assets').mkdir(parents=True,exist_ok=True);(ROOT/'resources/assets/app.ico').write_bytes(icon)
    target=ROOT/'licenses/third-party';target.mkdir(exist_ok=True)
    records=[]
    names=[]
    for line in (ROOT/'requirements/runtime.txt').read_text().splitlines():
        if '==' in line:names.append(line.split('==')[0])
    names+=['Python','PyInstaller','pyinstaller-hooks-contrib']
    for name in names:
        if name=='Python':
            for filename in ('LICENSE.txt','LICENSE_PYTHON.txt','LICENSE'):
                source=Path(sys.base_prefix)/filename
                if source.exists():shutil.copyfile(source,target/'Python-LICENSE.txt');break
            # Conda-built Python may use external codec/crypto libraries.
            # Append their public notices without copying environment metadata.
            native_notices=[]
            for package in ('bzip2','xz','libffi','openssl','zlib','libsqlite'):
                for meta in (Path(sys.base_prefix)/'conda-meta').glob(package+'-*.json'):
                    info=json.loads(meta.read_text('utf-8'))
                    folder=Path(sys.base_prefix)/'pkgs'/f"{info['name']}-{info['version']}-{info['build']}"/'info/licenses'
                    for license_file in folder.rglob('*'):
                        if license_file.is_file():native_notices.append('\n\n'+info['name']+' '+info['version']+' — '+license_file.name+'\n'+license_file.read_text('utf-8',errors='replace'))
            if native_notices:
                with (target/'Python-LICENSE.txt').open('a',encoding='utf-8') as stream:stream.write(''.join(native_notices))
            continue
        dist=metadata.distribution(name);folder=target/re.sub(r'[^A-Za-z0-9_.-]','_',name);folder.mkdir(exist_ok=True)
        license_text=dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See included license files and upstream project'
        records.append({'name':dist.metadata['Name'],'version':dist.version,'license':license_text,
            'project_urls':dist.metadata.get_all('Project-URL') or []})
        for item in dist.files or []:
            parts=Path(str(item)).parts
            if not any(part.endswith(('.dist-info','.egg-info')) for part in parts):continue
            if not any('license' in part.lower() or 'copying' in part.lower() or 'notice' in part.lower() for part in parts[1:]):continue
            source=Path(dist.locate_file(item))
            if source.is_file():
                destination=folder/Path(*parts[1:]);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    (target/'DEPENDENCIES.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf-8')
    for source in (ROOT/'licenses').glob('*LICENSE.txt'):shutil.copyfile(source,target/source.name)
    for name,url in {'LGPL-3.0.txt':'https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/LGPL-3.0-only.txt','GPL-3.0.txt':'https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/GPL-3.0-only.txt'}.items():
        path=target/name
        if not path.exists():
            response=requests.get(url,timeout=45);response.raise_for_status();path.write_bytes(response.content)
    (target/'Qt-source.txt').write_text('PySide6 Essentials 6.12.0 community edition: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only.\n'
        'Dynamically linked Qt libraries may be replaced with compatible builds.\n'
        'Upstream source: https://download.qt.io/archive/qt/6.12/\n'
        'Qt for Python source: https://code.qt.io/cgit/pyside/pyside-setup.git/\n'
        'Package license metadata: https://pypi.org/project/PySide6-Essentials/6.12.0/\n','utf-8')
    print(json.dumps({'icon_created':True,'dependency_notices':len(records),'license_files':sum(p.is_file() for p in target.rglob('*'))}))

if __name__=='__main__':main()
