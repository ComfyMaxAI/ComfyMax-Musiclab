import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def test_installer_is_relocatable_and_never_installs_models(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        self.assertIn('cd /d "%~dp0"',script)
        self.assertNotIn('D:\\ComfyMax-Musiclab',script)
        self.assertNotIn('Install_SheetSage.bat',script)
        self.assertNotIn('sheetsage_setup',script)
        self.assertIn('Models are not installed automatically.',script)
        for folder in ('models\\yue2','models\\sheetsage2','.cache\\whisper'):
            self.assertIn(folder,script)

    def test_installer_validates_bundled_runtimes_and_imports(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        for name in ('audiocpp_server.exe','server.json','ggml.dll','ggml-base.dll','ggml-cuda.dll',
                     'ggml-cpu-haswell.dll','cublas64_13.dll','cublasLt64_13.dll','cufft64_12.dll',
                     'MSVCP140.dll','MSVCP140_CODECVT_IDS.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
            self.assertIn(name,script)
        for package in ('comfymax_audio_chunker','PySide6','faster_whisper','mido','sounddevice','soundfile','librosa','demucs'):
            self.assertIn(package,script)

    def test_audio_cpp_release_runtime_is_prepared_without_fake_download_url(self):
        script=(ROOT/'Install.bat').read_text(encoding='utf-8')
        self.assertIn('set "AUDIOCPP_RUNTIME_URL="',script)
        self.assertIn('set "AUDIOCPP_RUNTIME_SHA256=5B2F0CDC4036B20D15C440D22B4B292FCBC09AD27C8D3E8366211CFDA6319B88"',script)
        self.assertIn('audiocpp-runtime-windows-cuda.zip',script)
        self.assertIn('no release URL is configured yet',script)
        self.assertIn('Missing repository audio.cpp configuration: server.json',script)
        self.assertNotIn('github.com/',script.lower())

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
