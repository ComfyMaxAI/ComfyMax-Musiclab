import unittest
import hashlib
import subprocess
import tempfile
import zipfile
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
RUNTIME_HELPER=ROOT/'install-audiocpp-runtime.ps1'
RUNTIME_FILES=(
    'audiocpp_server.exe','cublas64_13.dll','cublasLt64_13.dll','cufft64_12.dll',
    'ggml-base.dll','ggml-cpu-haswell.dll','ggml-cuda.dll','ggml.dll',
    'MSVCP140_CODECVT_IDS.dll','MSVCP140.dll','VCRUNTIME140_1.dll','VCRUNTIME140.dll')


def make_runtime_archive(path,missing=None,include_server=False):
    missing=set(missing or ())
    with zipfile.ZipFile(path,'w') as archive:
        for name in RUNTIME_FILES:
            if name not in missing: archive.writestr(name,('runtime-'+name).encode())
        if include_server: archive.writestr('server.json',b'replaced')
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def run_runtime_helper(runtime,archive,expected_hash,work):
    return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(RUNTIME_HELPER),
        '-RuntimeDir',str(runtime),'-ReleaseUrl','https://invalid.example/runtime.zip',
        '-ExpectedSha256',expected_hash,'-SourceArchive',str(archive),'-WorkingDirectory',str(work)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)


class InstallScriptTests(unittest.TestCase):
    def test_installer_is_relocatable_and_never_installs_models(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        self.assertIn('cd /d "%~dp0"',script)
        self.assertNotIn('D:\\ComfyMax-Musiclab',script)
        self.assertNotIn('Install_SheetSage.bat',script)
        self.assertIn('comfymax_audio_chunker.music.sheetsage_setup --runtime-only',script)
        self.assertIn('Models are not installed automatically.',script)
        for folder in ('models\\yue2','models\\sheetsage2','.cache\\whisper'):
            self.assertIn(folder,script)

    def test_installer_validates_bundled_runtimes_and_imports(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        runtime=RUNTIME_HELPER.read_text(encoding='utf-8')
        for name in ('audiocpp_server.exe','server.json','ggml.dll','ggml-base.dll','ggml-cuda.dll',
                     'ggml-cpu-haswell.dll','cublas64_13.dll','cublasLt64_13.dll','cufft64_12.dll',
                     'MSVCP140.dll','MSVCP140_CODECVT_IDS.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
            self.assertIn(name,runtime)
        for package in ('comfymax_audio_chunker','PySide6','faster_whisper','mido','sounddevice','soundfile','librosa','demucs'):
            self.assertIn(package,script)
        self.assertIn('MusicLab-managed Beat-Transformer runtime', script)
        self.assertIn('validate_managed_runtime()', script)

    def test_beat_transformer_assets_are_bundled_and_not_external(self):
        model = ROOT/'engines'/'beat-transformer'/'model'
        self.assertTrue((model/'code'/'DilatedTransformer.py').is_file())
        self.assertTrue((model/'code'/'DilatedTransformerLayer.py').is_file())
        self.assertTrue((model/'checkpoint'/'fold_4_trf_param.pt').is_file())
        self.assertTrue((ROOT/'engines'/'beat-transformer'/'LICENSE').is_file())
        backend=(ROOT/'src'/'comfymax_audio_chunker'/'music'/'beat_backend.py').read_text(encoding='utf-8')
        worker=(ROOT/'src'/'comfymax_audio_chunker'/'music'/'beat_worker.py').read_text(encoding='utf-8')
        for legacy_name in ('Chord'+'MiniApp', 'ComfyMax-'+'Audio-Chunker'):
            self.assertNotIn(legacy_name, backend+worker)
        self.assertNotIn('import madmom', worker)

    def test_sheetsage_runtime_install_follows_venv_and_never_requires_prebundled_manifest(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        self.assertLess(script.index('[4/9] Installing Python environment'),
                        script.index('[6/9] Installing or validating SheetSage runtime'))
        self.assertIn('[INFO] Installing SheetSage runtime...',script)
        self.assertIn('[OK] SheetSage runtime',script)
        self.assertIn('validate_runtime',script)
        self.assertIn('--runtime-only',script)
        self.assertNotIn('Missing bundled SheetSage runtime file: installation.json',script)
        self.assertNotIn('models\\sheetsage2\\sheetsage2-orig.gguf',script)

    def test_audio_cpp_release_runtime_has_official_url_and_hash(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        self.assertIn('https://github.com/ComfyMaxAI/ComfyMax-Musiclab/releases/download/runtime-audiocpp-0.8.1/audiocpp-runtime-windows-cuda.zip',script)
        self.assertIn('set "AUDIOCPP_RUNTIME_SHA256=5B2F0CDC4036B20D15C440D22B4B292FCBC09AD27C8D3E8366211CFDA6319B88"',script)
        self.assertIn('install-audiocpp-runtime.ps1',script)

    def test_complete_audio_cpp_runtime_skips_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); runtime=root/'runtime'; runtime.mkdir(); (runtime/'server.json').write_text('{}')
            for name in RUNTIME_FILES: (runtime/name).write_bytes(b'existing')
            work=root/'work'; missing_archive=root/'does-not-exist.zip'
            result=run_runtime_helper(runtime,missing_archive,'0'*64,work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('[OK] audio.cpp runtime',result.stdout)
            self.assertNotIn('Downloading',result.stdout); self.assertFalse(work.exists())

    def test_valid_audio_cpp_package_extracts_cleans_up_and_preserves_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); runtime=root/'runtime'; runtime.mkdir(); server=runtime/'server.json'
            server.write_text('repository-config',encoding='utf-8')
            archive=root/'runtime.zip'; digest=make_runtime_archive(archive,include_server=True); work=root/'work'
            result=run_runtime_helper(runtime,archive,digest,work)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('[INFO] Downloading audio.cpp Windows CUDA runtime...',result.stdout)
            self.assertTrue(all((runtime/name).is_file() for name in RUNTIME_FILES))
            self.assertEqual(server.read_text(encoding='utf-8'),'repository-config')
            self.assertFalse(work.exists())

    def test_wrong_audio_cpp_hash_stops_and_removes_temporary_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); runtime=root/'runtime'; runtime.mkdir(); (runtime/'server.json').write_text('{}')
            archive=root/'runtime.zip'; make_runtime_archive(archive); work=root/'work'
            result=run_runtime_helper(runtime,archive,'0'*64,work)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('SHA-256 mismatch',result.stdout)
            self.assertFalse(any((runtime/name).exists() for name in RUNTIME_FILES)); self.assertFalse(work.exists())

    def test_incomplete_audio_cpp_package_reports_exact_file_and_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); runtime=root/'runtime'; runtime.mkdir(); (runtime/'server.json').write_text('{}')
            archive=root/'runtime.zip'; digest=make_runtime_archive(archive,missing={'ggml-cuda.dll'}); work=root/'work'
            result=run_runtime_helper(runtime,archive,digest,work)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('[MISSING] Extracted audio.cpp runtime file: ggml-cuda.dll',result.stdout)
            self.assertIn('[ERROR] audio.cpp runtime installation failed',result.stdout)
            self.assertFalse(work.exists())

    def test_audio_cpp_installer_never_downloads_models(self):
        helper=RUNTIME_HELPER.read_text(encoding='utf-8').lower()
        for model_name in ('yue2','whisper','sheetsage','safetensors','.gguf'):
            self.assertNotIn(model_name,helper)

    def test_setup_uses_python_311_dependency_metadata_and_no_test_or_model_install(self):
        setup=(ROOT/'setup.ps1').read_text(encoding='utf-8')
        project=(ROOT/'pyproject.toml').read_text(encoding='utf-8')
        self.assertIn('3.11.15',setup)
        self.assertIn("'-e','.[editor]'",setup)
        self.assertNotIn("'-m','unittest'",setup)
        self.assertIn('requires-python = ">=3.11,<3.12"',project)
        self.assertIn('faster-whisper==1.2.1',project)
        self.assertIn('PySide6==6.8.3',project)

    def test_start_script_uses_only_project_environment_and_local_ffmpeg(self):
        script=(ROOT/'Launch Editor.cmd').read_text(encoding='utf-8')
        self.assertIn('cd /d "%~dp0"',script)
        self.assertIn('.venv\\Scripts\\pythonw.exe',script)
        self.assertIn('engines\\ffmpeg\\bin',script)
        self.assertNotIn('D:\\ComfyMax-Musiclab',script)


if __name__=='__main__': unittest.main()
