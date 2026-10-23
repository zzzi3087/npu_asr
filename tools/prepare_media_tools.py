"""Fetch the pinned LGPL shared FFmpeg build for packaging (never Git source)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import requests

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=ROOT/'build/media-tools')
    parser.add_argument('--archive',type=Path);args=parser.parse_args()
    info=json.loads((ROOT/'resources/media-tools.json').read_text('utf-8'))
    target=args.directory.resolve();target.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='livecaptions-ffmpeg-build-') as temp:
        archive=args.archive or Path(temp)/'ffmpeg.zip'
        if not args.archive:
            with requests.get(info['url'],stream=True,timeout=(30,120)) as response:
                response.raise_for_status()
                with archive.open('wb') as out:
                    for block in response.iter_content(1024*1024):out.write(block)
        with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if archive.stat().st_size!=info['size'] or digest!=info['sha256']:raise RuntimeError('FFmpeg archive integrity mismatch')
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                parts=Path(name).parts
                if '..' in parts or Path(name).is_absolute():raise RuntimeError('Unsafe archive member')
                if len(parts)<2:continue
                relative=Path(*parts[1:])
                if relative.name=='ffplay.exe':continue
                if relative.parts[0]!='bin' and relative.name!='LICENSE.txt':continue
                if name.endswith('/'):continue
                destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src,destination.open('wb') as dst:shutil.copyfileobj(src,dst)
        for name in ('ffmpeg.exe','ffprobe.exe'):
            if not (target/'bin'/name).is_file():raise RuntimeError('Missing media executable')
        shutil.copyfile(ROOT/'resources/media-tools.json',target/'BUILD.json')
    print(json.dumps({'media_tools_ready':True,'directory':str(target),'pinned_archive_verified':True}))

if __name__=='__main__':main()
