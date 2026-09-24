"""Download-only optional setup. Existing valid components are never overwritten."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from . import sheetsage_runtime as runtime
from .sheetsage_worker import sha256


def fetch(url,destination,checksum):
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.is_file() and sha256(destination)==checksum:
        print('Verified cached download: '+destination.name,flush=True); return destination
    # Invalid downloads are not promoted to installed files; retries start fresh.
    partial=destination.with_name(destination.name+'.part')
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'ComfyMax-Audio-Chunker-SheetSage-Setup/1'})
        print('Downloading '+destination.name,flush=True)
        with urllib.request.urlopen(request,timeout=60) as response,partial.open('wb') as output:
            total=0; announced=0
            while block:=response.read(1024*1024):
                output.write(block); total+=len(block)
                if total-announced>=128*1024*1024:
                    print(f'  {total/1024**2:.0f} MiB received',flush=True); announced=total
        if sha256(partial)!=checksum: raise ValueError('Checksum mismatch for '+destination.name+'; installation aborted')
        os.replace(partial,destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


def extract(archive,stage):
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target=(stage/entry.filename).resolve()
            if not target.is_relative_to(stage.resolve()) or ':' in entry.filename or '\\' in entry.filename:
                raise ValueError('Unsafe archive path')
            if (entry.external_attr>>16)&0o170000==0o120000: raise ValueError('Archive symlink rejected')
            if not entry.is_dir():
                target.parent.mkdir(parents=True,exist_ok=True)
                with bundle.open(entry) as source,target.open('wb') as out: shutil.copyfileobj(source,out)


def setup(root=runtime.APP_ROOT,cache=None):
    root=Path(root).resolve(); cache=Path(cache).resolve() if cache else root/'.cache'/'sheetsage-downloads'
    cache.mkdir(parents=True,exist_ok=True)
    lock=root/'.cache'/'sheetsage-install.lock'; lock.parent.mkdir(parents=True,exist_ok=True)
    try: handle=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise ValueError('Another setup is active. If it was forcibly interrupted, close it and remove .cache/sheetsage-install.lock, then retry.')
    os.close(handle)
    try:
        install,model=runtime.locations(root)
        try: runtime.runtime_files(install,full=True); ready=True
        except (OSError,ValueError,KeyError,TypeError): ready=False
        if ready:
            print('Runtime already verified; keeping existing installation.',flush=True)
        else:
            archives=[fetch(runtime.RELEASE_URL+name,cache/name,checksum) for name,checksum in runtime.ARCHIVES]
            with tempfile.TemporaryDirectory(prefix='sheetsage-stage-',dir=lock.parent) as folder:
                stage=Path(folder)/'runtime'; stage.mkdir()
                for archive in archives: extract(archive,stage)
                if sha256(stage/'audiocpp_cli.exe')!=runtime.EXE_SHA: raise ValueError('Executable checksum mismatch')
                files={p.relative_to(stage).as_posix():dict(bytes=p.stat().st_size,sha256=sha256(p)) for p in sorted(stage.rglob('*')) if p.is_file()}
                (stage/'installation.json').write_text(json.dumps(dict(version=runtime.VERSION,archives=runtime.ARCHIVES,files=files),indent=2),encoding='utf8')
                runtime.runtime_files(stage,full=True)
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
                result=subprocess.run([str(stage/'audiocpp_cli.exe'),'--version'],cwd=stage,capture_output=True,text=True,timeout=30,check=True,creationflags=flags)
                if 'audio.cpp '+runtime.VERSION not in result.stdout: raise ValueError('Unexpected audio.cpp version')
                install.parent.mkdir(parents=True,exist_ok=True)
                backup=None
                if install.exists():
                    backup=lock.parent/('sheetsage-runtime-backup-'+uuid.uuid4().hex)
                    install.rename(backup)
                    print('Previous incomplete runtime retained at '+str(backup),flush=True)
                try: stage.rename(install)
                except BaseException:
                    if backup is not None: backup.rename(install)
                    raise
                print('Runtime installed and verified.',flush=True)
        if model.is_file() and sha256(model)==runtime.MODEL_SHA:
            print('Model already verified; keeping existing file.',flush=True)
        else:
            downloaded=fetch(runtime.MODEL_URL,cache/'sheetsage2-orig.gguf',runtime.MODEL_SHA)
            model.parent.mkdir(parents=True,exist_ok=True)
            staging=model.with_suffix('.gguf.part')
            try:
                shutil.copyfile(downloaded,staging)
                if sha256(staging)!=runtime.MODEL_SHA: raise ValueError('Installed model checksum mismatch')
                os.replace(staging,model)
            finally: staging.unlink(missing_ok=True)
        print('SheetSage2: Ready\nExecutable: '+str(install/'audiocpp_cli.exe')+'\nModel: '+str(model),flush=True)
        print('Optional CUDA backend; requires a compatible NVIDIA driver. No project data changed.',flush=True)
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='Install pinned audio.cpp 0.8.1 and SheetSage2 (CC BY-NC 4.0; non-commercial use only).')
    parser.add_argument('--download-cache',type=Path,help='Optional verified download cache (never used for inference)')
    args=parser.parse_args()
    print('SheetSage2 model: CC BY-NC 4.0, non-commercial use only. See engines/sheetsage/README.md.\nDownloads: about 3.55 GB; allow 10 GB free space. Ctrl+C cancels safely.',flush=True)
    try:
        setup(cache=args.download_cache)
    except KeyboardInterrupt:
        print('Installation cancelled. Run Install_SheetSage.bat again to retry.'); return 130
    except Exception as exc:
        print('SheetSage installation failed: '+str(exc)+'\nRun Install_SheetSage.bat again after resolving the issue.',file=sys.stderr); return 1
    return 0


if __name__=='__main__': sys.exit(main())
