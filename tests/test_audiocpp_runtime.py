import os
import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from comfymax_audio_chunker.editor.audiocpp_runtime import (AudioCppRuntime,ROOT,
    YUE2_MAIN,YUE2_MODEL_ID,YUE2_MODEL_ROOT,YUE2_REGISTERED_PATH,YUE2_SIDECARS,YUE2_VAE,
    validate_yue2_package)


class Process:
    def __init__(self): self.returncode=None; self.terminated=False; self.killed=False
    def poll(self): return self.returncode
    def terminate(self): self.terminated=True; self.returncode=0
    def wait(self,timeout=None): return self.returncode
    def kill(self): self.killed=True; self.returncode=-9


class AudioCppRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_internal_runtime_path_and_executable(self):
        runtime=AudioCppRuntime()
        self.assertEqual(runtime.runtime_dir,ROOT/'engines'/'audiocpp')
        self.assertTrue(runtime.executable.is_file()); self.assertTrue(runtime.config.is_file())

    def test_yue2_package_and_registration(self):
        self.assertEqual(validate_yue2_package(),[])
        self.assertTrue((YUE2_MODEL_ROOT/YUE2_MAIN).is_file())
        self.assertTrue((YUE2_MODEL_ROOT/YUE2_VAE).is_file())
        self.assertTrue(all((YUE2_MODEL_ROOT/'sidecars'/name).is_file() for name in YUE2_SIDECARS))
        config=__import__('json').loads((ROOT/'engines'/'audiocpp'/'server.json').read_text())
        text=(ROOT/'engines'/'audiocpp'/'server.json').read_text()
        self.assertNotIn('runtime-placeholder',text)
        self.assertTrue(config['lazy_load'])
        model=config['models'][0]
        self.assertEqual((model['id'],model['family']),(YUE2_MODEL_ID,'yue2'))
        self.assertEqual(model['path'],YUE2_REGISTERED_PATH)
        self.assertEqual(model['session_options'],{'model_gguf':YUE2_MAIN,'vae_gguf':YUE2_VAE})

    def test_missing_runtime_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=AudioCppRuntime(runtime_dir=folder)
            with patch.object(runtime,'health',return_value=None): runtime._start_worker()
            self.assertEqual(runtime.status,'Not available'); self.assertFalse(runtime.owned)

    def test_external_server_is_reused_and_never_stopped(self):
        runtime=AudioCppRuntime(); external=Process(); runtime.process=external
        with patch.object(runtime,'health',return_value={'status':'ok','backend':'cuda'}): runtime._start_worker()
        self.assertEqual(runtime.status,'Ready'); self.assertFalse(runtime.owned)
        runtime.stop(); self.assertFalse(external.terminated); self.assertFalse(external.killed)

    def test_owned_server_starts_once_and_stops(self):
        runtime=AudioCppRuntime(); process=Process()
        with patch.object(runtime,'health',side_effect=[None,{'status':'ok','backend':'cuda'}]), \
             patch('comfymax_audio_chunker.editor.audiocpp_runtime.subprocess.Popen',return_value=process) as spawn:
            runtime._start_worker()
        self.assertEqual(runtime.status,'Ready'); self.assertTrue(runtime.owned); spawn.assert_called_once()
        command=spawn.call_args.args[0]
        self.assertIn('--no-ui',command); self.assertIn('--ui-management',command)
        runtime.ensure_started(); QTest.qWait(20); spawn.assert_called_once()
        runtime.stop(); self.assertTrue(process.terminated); self.assertFalse(runtime.owned)

    def test_registered_model_count_is_accepted_by_health(self):
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"status":"ok","backend":"cuda","models":1}'
        with patch('comfymax_audio_chunker.editor.audiocpp_runtime.urlopen',return_value=response):
            self.assertEqual(AudioCppRuntime().health()['models'],1)

    def test_generation_request_loads_only_when_needed_and_saves_audio(self):
        wav=b'RIFFtest-wave'
        generation_copy='X:1\nK:D\n"D"D E F G|'
        with tempfile.TemporaryDirectory() as folder:
            runtime=AudioCppRuntime(); runtime.ensure_ready=Mock(); runtime.load_yue2=Mock()
            with patch.object(runtime,'models',return_value=[{'id':'yue2-3b-q8','loaded':False}]), \
                 patch.object(runtime,'_json_request',return_value={'audio':base64.b64encode(wav).decode()}) as send:
                result=runtime.generate_yue2('words','indie',generation_copy,7,'melody',folder)
            runtime.ensure_ready.assert_called_once(); runtime.load_yue2.assert_called_once()
            endpoint,payload=send.call_args.args
            self.assertEqual(endpoint,'/v1/tasks/run'); self.assertEqual(payload['model'],'yue2-3b-q8')
            self.assertEqual(payload['request'],{'lyrics':'words','seed':7,
                'options':{'style':'indie','cot':'melody','abc':generation_copy}})
            self.assertEqual(result['path'].read_bytes(),wav)

    def test_generation_does_not_reload_resident_model(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=AudioCppRuntime(); runtime.ensure_ready=Mock(); runtime.load_yue2=Mock()
            with patch.object(runtime,'models',return_value=[{'id':'yue2-3b-q8','loaded':True}]), \
                 patch.object(runtime,'_json_request',return_value={'audio':base64.b64encode(b'wav').decode()}):
                runtime.generate_yue2('words','style','',1,'off',folder)
            runtime.load_yue2.assert_not_called()

    def test_off_omits_external_abc(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=AudioCppRuntime(); runtime.ensure_ready=Mock(); runtime.load_yue2=Mock()
            with patch.object(runtime,'models',return_value=[{'id':'yue2-3b-q8','loaded':True}]), \
                 patch.object(runtime,'_json_request',return_value={'audio':base64.b64encode(b'wav').decode()}) as send:
                runtime.generate_yue2('words','style','',2,'off',folder)
            self.assertEqual(send.call_args.args[1]['request']['options'],{'style':'style','cot':'off'})

    def test_full_merges_supported_sampling_options(self):
        sampling={'guidance_scale':1.5,'num_inference_steps':16,
                  'semantic_top_k':100,'semantic_temperature':1.0,'abc_top_k':30,'abc_temperature':.7}
        with tempfile.TemporaryDirectory() as folder:
            runtime=AudioCppRuntime(); runtime.ensure_ready=Mock(); runtime.load_yue2=Mock()
            with patch.object(runtime,'models',return_value=[{'id':'yue2-3b-q8','loaded':True}]), \
                 patch.object(runtime,'_json_request',return_value={'audio':base64.b64encode(b'wav').decode()}) as send:
                runtime.generate_yue2('words','style','X:1',2,'full',folder,sampling)
            options=send.call_args.args[1]['request']['options']
            self.assertEqual(options,{'style':'style','cot':'full','abc':'X:1',**sampling})
            self.assertIsInstance(options['guidance_scale'],float)
            self.assertIsInstance(options['num_inference_steps'],int)

    def test_generation_status_text(self):
        from test_generation_panel import Host
        host=Host(); self.addCleanup(host.generation_pane.deleteLater); self.addCleanup(host.lyrics_editor.deleteLater)
        for status in ('Starting...','Ready','Not available','Error'):
            host.set_audiocpp_status(status); self.assertEqual(host.audiocpp_status.text(),'audio.cpp: '+status)


class AudioCppRuntimeSmokeTest(unittest.TestCase):
    def test_bundled_runtime_yue2_load_unload_stop(self):
        probe=AudioCppRuntime()
        if probe.health(): self.skipTest('Port 18080 already has an external audio.cpp server')
        process=None
        probe._start_worker()
        try:
            self.assertEqual(probe.status,'Ready')
            self.assertTrue(probe.owned)
            health=probe.health(); self.assertEqual(health['status'],'ok'); self.assertEqual(health['backend'],'cuda')
            self.assertEqual(health['models'],1)
            before=probe.models(); self.assertEqual(len(before),1)
            self.assertEqual(before[0]['id'],YUE2_MODEL_ID); self.assertEqual(before[0]['family'],'yue2')
            self.assertFalse(before[0]['loaded'])
            probe.load_yue2()
            loaded=probe.models(); self.assertTrue(loaded[0]['loaded'])
            probe.unload_yue2()
            unloaded=probe.models(); self.assertFalse(unloaded[0]['loaded'])
            process=probe.process
        finally:
            probe.stop()
        self.assertIsNotNone(process); self.assertIsNotNone(process.poll())
        self.assertIsNone(probe.health())


if __name__=='__main__': unittest.main()
