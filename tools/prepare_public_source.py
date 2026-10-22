"""Copy an explicit source allowlist; fail closed on secrets/private paths."""
import argparse,json,re,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
NAMES={'.gitignore','README.md','LICENSE','THIRD_PARTY_NOTICES.md','requirements-lock.txt','model-catalog.json',
       'eddy-LICENSE.txt','Nerdstorm-LICENSE.txt','Silero-LICENSE.txt'}
DIRECTORIES={'tools','packaging','assets','third-party-licenses'}
EXTENSIONS={'.py','.txt','.json','.md','.spec','.iss','.ico'}
FORBIDDEN={'.onnx','.bin','.npy','.npz','.model','.safetensors','.pt','.pth','.sqlite','.db','.log','.wav','.mp3','.pem','.key'}
def check(path):
    data=path.read_bytes()
    # No local account name, home directory, or credential-shaped literals.
    patterns=[rb'(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/\s"\']+',rb'(?i)/(?:home|Users)/[A-Za-z0-9_.-]+',
        rb'(?<![A-Za-z0-9])(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})']
    for pattern in patterns:
        if re.search(pattern,data):raise RuntimeError('Sensitive content detected: '+str(path.name))
    return True
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',type=Path,required=True);args=parser.parse_args()
    destination=args.destination.resolve()
    if destination==ROOT or ROOT in destination.parents:raise ValueError('Use a separate release-source directory')
    files=[p for p in ROOT.iterdir() if p.is_file() and (p.suffix=='.py' or p.name in NAMES)]
    files+=[p for folder in DIRECTORIES for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and (p.suffix.lower() in EXTENSIONS or folder=='third-party-licenses')]
    for source in files:
        if source.suffix.lower() in FORBIDDEN:raise RuntimeError('Forbidden payload: '+source.name)
        check(source)
    destination.mkdir(parents=True,exist_ok=True)
    for source in files:
        target=destination/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    expected={str(p.relative_to(ROOT)).replace('\\','/') for p in files}
    actual={str(p.relative_to(destination)).replace('\\','/') for p in destination.rglob('*') if p.is_file() and '.git' not in p.relative_to(destination).parts}
    if actual!=expected:raise RuntimeError('Release source contains unapproved extra/missing files')
    print(json.dumps({'approved_source_files':len(files),'bytes':sum(p.stat().st_size for p in files),'privacy_scan_passed':True}))
if __name__=='__main__':main()
