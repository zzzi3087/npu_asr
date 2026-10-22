"""Explicit, revision-pinned model download; no credentials required."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import requests
from app_paths import APP_DIR,MODEL_ROOT

REVISION = 'e115adf711921df9378e383d01190901f0057704'
REQUIRED_FILES=tuple('parakeet_'+part+ext for part in ('melspectogram','encoder','decoder','joint') for ext in ('.xml','.bin'))+('parakeet_vocab.json',)
DEFAULT_MODEL_DIR = MODEL_ROOT / REVISION


def model_directory(app_dir=None,cache_dir=None):
    local=(Path(app_dir) if app_dir is not None else APP_DIR)/'models'/REVISION
    try:complete=(local/'manifest.json').is_file() and all((local/name).is_file() and (local/name).stat().st_size>0 for name in REQUIRED_FILES)
    except OSError:complete=False
    return local if complete else (Path(cache_dir) if cache_dir is not None else DEFAULT_MODEL_DIR)


MODEL_DIR=model_directory()
REPO = 'FluidInference/parakeet-tdt-0.6b-v2-ov'

def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    items = requests.get(f'https://huggingface.co/api/models/{REPO}/tree/{REVISION}', timeout=30).json()
    def fetch(item):
        name, size = item['path'], item.get('size', 0)
        if name.startswith('.'):
            return
        dst = MODEL_DIR / name
        if dst.exists() and dst.stat().st_size == size:
            return
        print(f'Downloading {name}: {size / 1e6:.1f} MB', flush=True)
        with requests.get(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}', stream=True, timeout=(30, 120)) as res:
            res.raise_for_status()
            with dst.with_suffix(dst.suffix + '.part').open('wb') as f:
                for chunk in res.iter_content(4 * 1024 * 1024):
                    f.write(chunk)
        if dst.with_suffix(dst.suffix + '.part').stat().st_size != size:
            raise RuntimeError(f'Incomplete download: {name}')
        dst.with_suffix(dst.suffix + '.part').replace(dst)
        print(f'Finished {name}', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, items))
    manifest = {p.name: hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
                for p in MODEL_DIR.iterdir() if p.is_file() and p.name != 'manifest.json'}
    (MODEL_DIR / 'manifest.json').write_text(json.dumps({'repository': REPO, 'revision': REVISION, 'sha256': manifest}, indent=2))
    print(str(MODEL_DIR), flush=True)

if __name__ == '__main__':
    main()
