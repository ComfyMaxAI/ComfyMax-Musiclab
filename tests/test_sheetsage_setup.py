import io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from comfymax_audio_chunker.music import sheetsage as sheet,sheetsage_runtime as runtime,sheetsage_setup as setup
from comfymax_audio_chunker.music.sheetsage_worker import sha256

class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/'Portable App'; self.root.mkdir()
        self.cache=Path(self.temp.name)/'downloads'; self.cache.mkdir()
        self.exe=b'tested executable'; self.model=b'GGUF test model'
        files=['audiocpp_cli.exe','ggml.dll','ggml-base.dll','ggml-cuda.dll','cudart64_13.dll','cublas64_13.dll','cublasLt64_13.dll','cufft64_12.dll','model_specs/sheetsage2.json','LICENSE']
        archive=self.cache/'runtime.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name in files: z.writestr(name,self.exe if name=='audiocpp_cli.exe' else b'dependency')
        model=self.cache/'sheetsage2-orig.gguf'; model.write_bytes(self.model)
        import hashlib
        self.patches=[patch.object(runtime,'ARCHIVES',(('runtime.zip',sha256(archive)),)),patch.object(runtime,'EXE_SHA',hashlib.sha256(self.exe).hexdigest()),patch.object(runtime,'MODEL_SHA',sha256(model)),patch.object(runtime,'MODEL_SIZE',len(self.model)),patch.object(setup.subprocess,'run',return_value=SimpleNamespace(stdout='audio.cpp 0.8.1'))]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def install(self):
        with patch.object(setup.urllib.request,'urlopen',side_effect=AssertionError('Must use verified cache')): setup.setup(self.root,self.cache)
    def test_complete_repeat_partial_and_reinstall(self):
        install,model=runtime.locations(self.root)
        with self.assertRaises(OSError):runtime.managed_config(self.root)
        self.install(); runtime.runtime_files(install,full=True)
        stamp=(install/'audiocpp_cli.exe').stat().st_mtime_ns; mstamp=model.stat().st_mtime_ns
        self.install(); self.assertEqual((install/'audiocpp_cli.exe').stat().st_mtime_ns,stamp);self.assertEqual(model.stat().st_mtime_ns,mstamp)
        model.unlink() # runtime only
        with self.assertRaises(ValueError):runtime.managed_config(self.root)
        self.install()
        mstamp=model.stat().st_mtime_ns
        install.rename(install.with_name('removed-runtime')) # model only / uninstall simulation
        with self.assertRaises(OSError):runtime.managed_config(self.root)
        self.install(); self.assertEqual(model.stat().st_mtime_ns,mstamp)
        model.write_bytes(b'bad'); self.install();self.assertEqual(model.read_bytes(),self.model)
        (install/'ggml.dll').write_bytes(b'bad');self.install();runtime.runtime_files(install,full=True)
    def test_bad_checksum_unavailable_and_interrupt_keep_destination(self):
        target=self.cache/'new';target.write_bytes(b'previous')
        for response in (io.BytesIO(b'bad download'),OSError('network unavailable'),KeyboardInterrupt()):
            kwargs={'side_effect':response} if isinstance(response,BaseException) else {'return_value':response}
            with patch.object(setup.urllib.request,'urlopen',**kwargs),self.assertRaises((ValueError,OSError,KeyboardInterrupt)):
                setup.fetch('https://example.invalid/file',target,'0'*64)
            self.assertEqual(target.read_bytes(),b'previous');self.assertFalse(target.with_name('new.part').exists())
    def test_no_silent_external_fallback_and_override(self):
        config=self.root/'missing.json'
        with patch.object(sheet,'APP_ROOT',self.root),patch.object(sheet,'CONFIG',config),patch.dict(os.environ,{},clear=True):
            self.assertFalse(sheet.availability()[0]);self.assertIn('Install_SheetSage.bat',sheet.availability()[1])
            self.install();c=sheet.configuration();self.assertTrue(c['managed']);self.assertTrue(Path(c['executable']).is_relative_to(self.root))
            override=self.root/'override.json';override.write_text(json.dumps(dict(executable='engines/sheetsage/runtime/audiocpp_cli.exe',model='models/sheetsage2/sheetsage2-orig.gguf',backend='cpu')))
            with patch.dict(os.environ,{'COMFYMAX_SHEETSAGE_CONFIG':str(override)}):self.assertEqual(sheet.configuration()['backend'],'cpu')
            with patch.dict(os.environ,{'COMFYMAX_SHEETSAGE_CONFIG':str(config)}):self.assertFalse(sheet.availability()[0])
    def test_malformed_manifest_and_checksum_before_use(self):
        self.install();install,model=runtime.locations(self.root)
        p=install/'ggml.dll';p.write_bytes(b'x'*p.stat().st_size)
        with self.assertRaises(ValueError):runtime.runtime_files(install,full=True)
        (install/'installation.json').write_text('[]')
        with self.assertRaises(ValueError):runtime.runtime_files(install)
    def test_safe_archive_paths_and_failed_setup_unlock(self):
        archive=self.cache/'unsafe.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape',b'bad')
        with self.assertRaises(ValueError):setup.extract(archive,self.root)
        with patch.object(setup,'fetch',side_effect=KeyboardInterrupt()),self.assertRaises(KeyboardInterrupt):setup.setup(self.root,self.cache)
        self.assertFalse((self.root/'.cache/sheetsage-install.lock').exists())

if __name__=='__main__':unittest.main()
