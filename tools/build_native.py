"""Build the small audio DLL with installed MSVC; no downloads or user data."""
import argparse,json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def build(output):
    if os.name!='nt':raise RuntimeError('The optional native build uses Windows MSVC')
    finder=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not finder.is_file():raise RuntimeError('Install Visual Studio C++ Build Tools before packaging')
    location=subprocess.check_output([str(finder),'-latest','-products','*','-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
    script=Path(location)/'Common7/Tools/VsDevCmd.bat'
    if not location or not script.is_file() or any(c in str(script) for c in '"%\r\n'):
        raise RuntimeError('A usable x64 MSVC installation was not found')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    # A fixed local batch file avoids cmd.exe argument-list quoting ambiguity.
    # Capture its environment in memory; never log environment variables.
    env_script=output/'native_env.cmd'
    env_script.write_text(f'@call "{script}" -no_logo -arch=x64 -host_arch=x64 >nul\n@if errorlevel 1 exit /b 1\n@set\n','utf-8')
    text=subprocess.check_output(['cmd.exe','/d','/c',env_script.name],cwd=output,text=True,encoding='utf-8',errors='replace')
    environment=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('='))
    compiler=shutil.which('cl.exe',path=next((v for k,v in environment.items() if k.upper()=='PATH'),''))
    if not compiler:raise RuntimeError('MSVC compiler was not found in its build environment')
    # Relative source name keeps workstation paths out of compiler metadata.
    shutil.copyfile(ROOT/'native/audio_native.cpp',output/'audio_native.cpp')
    subprocess.run([compiler,'/nologo','/std:c++17','/O2','/MT','/LD','/EHsc','/fp:precise','/Brepro',
        'audio_native.cpp','/Fe:audio_native.dll','/Fo:audio_native.obj','/link','/INCREMENTAL:NO','/Brepro'],
        cwd=output,env=environment,check=True)
    return output/'audio_native.dll'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'build/native');args=parser.parse_args()
    artifact=build(args.output);print(json.dumps({'native_audio_built':True,'bytes':artifact.stat().st_size}))
