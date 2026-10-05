import base64
import json
import os
from pathlib import Path
import struct
import tempfile
import subprocess
import time
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QFileDialog
from comfymax_audio_chunker.editor.audiocpp_runtime import AudioCppRuntime,YUE2_MODEL_ID
from comfymax_audio_chunker.editor.generation_panel import GenerationTask
from comfymax_audio_chunker.editor.settings_panel import load_settings,save_settings
from comfymax_audio_chunker.editor.yue2_lora import adapter_kind,scan_loras,session_lora_options,active_lora_options
from test_generation_panel import Host


def write_adapter(path,kind='ar',shape_a=(1,1),shape_b=(1,1),extra=None):
    prefix='layers.0.'+('nar_' if kind=='nar' else '')+'self_attn.q_proj'
    tensors={prefix+'.lora_A':shape_a,prefix+'.lora_B':shape_b,**(extra or {})}
    header={}; data=b''
    for name,shape in tensors.items():
        count=__import__('math').prod(shape)*4
        header[name]={'dtype':'F32','shape':list(shape),'data_offsets':[len(data),len(data)+count]}
        data+=b'\0'*count
    encoded=json.dumps(header).encode(); encoded+=b' '*((-len(encoded))%8)
    path.write_bytes(struct.pack('<Q',len(encoded))+encoded+data)
    return str(path.resolve())


class LoraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def test_missing_directory_created_and_empty(self):
        folder=self.root/'new'/'loras'
        self.assertEqual(scan_loras(folder),({'ar':[],'nar':[]},[])); self.assertTrue(folder.is_dir())

    def test_tensor_names_classify_without_filename_guesses(self):
        ar=Path(write_adapter(self.root/'nar-looking.safetensors'))
        nar=Path(write_adapter(self.root/'ar-looking.safetensors','nar',extra={'vae2llm.bias':(1,)}))
        (self.root/'foreign.safetensors').write_bytes(b'invalid')
        (self.root/'ignored.txt').write_text('ignored')
        adapters,errors=scan_loras(self.root)
        self.assertEqual(adapters,{'ar':[ar],'nar':[nar]}); self.assertEqual(len(errors),1)

    def test_invalid_fused_mixed_and_missing_pairs_rejected(self):
        cases=[{'foreign.lora_A.weight':(1,1)}, {'layers.0.nar_self_attn.q_proj.lora_A':(1,1)},
               {'layers.0.mlp.gate_proj.lora_A':(1,1)}]
        for index,extra in enumerate(cases):
            path=self.root/f'{index}.safetensors'; write_adapter(path,extra=extra)
            with self.assertRaises(ValueError): adapter_kind(path)

    def test_ar_nar_combined_and_scale_mapping(self):
        for kinds in [('ar',),('nar',),('ar','nar')]:
            with self.subTest(kinds=kinds):
                config={}; expected={}
                for kind in kinds:
                    path=write_adapter(self.root/(kind+'.safetensors'),kind)
                    config[kind+'_lora']=path; config[kind+'_lora_scale']=.75
                    expected['yue2.'+kind+'_lora']=path; expected['yue2.'+kind+'_lora_scale']=.75
                self.assertEqual(session_lora_options(config),expected)

    def test_none_has_no_options_and_strength_zero_is_supported(self):
        self.assertEqual(session_lora_options({'ar_lora':None,'nar_lora':None}),{})
        ar=write_adapter(self.root/'ar.safetensors')
        self.assertEqual(session_lora_options({'ar_lora':ar,'ar_lora_scale':0})['yue2.ar_lora_scale'],0)
        for strength in (float('nan'),float('inf'),-1,3):
            with self.assertRaises(ValueError): session_lora_options({'ar_lora':ar,'ar_lora_scale':strength})

    def test_wrong_kind_or_deleted_file_is_clear_error(self):
        ar=write_adapter(self.root/'ar.safetensors')
        with self.assertRaisesRegex(ValueError,'not NAR'): session_lora_options({'nar_lora':ar})
        Path(ar).unlink()
        with self.assertRaisesRegex(ValueError,'no longer exists'): session_lora_options({'ar_lora':ar})

    def test_runtime_loads_session_options_not_generation_options_and_saves_metadata(self):
        ar=write_adapter(self.root/'ar.safetensors'); nar=write_adapter(self.root/'nar.safetensors','nar')
        config={'ar_lora':ar,'ar_lora_scale':.6,'nar_lora':nar,'nar_lora_scale':1.4}
        r=AudioCppRuntime(); r.ensure_ready=Mock()
        with patch.object(r,'models',return_value=[{'id':YUE2_MODEL_ID,'loaded':False}]), \
             patch.object(r,'_json_request',side_effect=[{}, {'audio':base64.b64encode(b'wav').decode()}]) as send:
            result=r.generate_yue2('lyrics','style','',42,output_dir=self.root,lora_config=config)
        load,run=send.call_args_list
        self.assertEqual(load.args[0],'/v1/models/load')
        for key,value in session_lora_options(config).items(): self.assertEqual(load.args[1]['session_options'][key],value)
        self.assertEqual(run.args[1]['request'],{'lyrics':'lyrics','seed':42,'options':{'style':'style','cot':'off'}})
        self.assertEqual(json.loads(result['metadata_path'].read_text())['loras'],config)

    def test_same_stack_no_reload_scale_change_and_none_reload(self):
        ar=write_adapter(self.root/'ar.safetensors'); config={'ar_lora':ar,'ar_lora_scale':.8}
        r=AudioCppRuntime(); r.ensure_ready=Mock(); r.load_yue2=Mock()
        state={'id':YUE2_MODEL_ID,'loaded':True,'session_options':{'yue2.ar_lora':ar,'yue2.ar_lora_scale':'0.8'}}
        with patch.object(r,'models',return_value=[state]),patch.object(r,'_json_request',return_value={'audio':base64.b64encode(b'wav').decode()}):
            r.generate_yue2('lyrics','style','',42,output_dir=self.root,lora_config=config)
            r.load_yue2.assert_not_called()
            r.generate_yue2('lyrics','style','',42,output_dir=self.root,lora_config={**config,'ar_lora_scale':1.2})
            r.load_yue2.assert_called_once_with({'yue2.ar_lora':ar,'yue2.ar_lora_scale':1.2})
            r.load_yue2.reset_mock()
            r.generate_yue2('lyrics','style','',42,output_dir=self.root)
            r.load_yue2.assert_called_once_with()

    def test_unloaded_server_with_same_stack_reloads(self):
        ar=write_adapter(self.root/'ar.safetensors'); config={'ar_lora':ar}
        r=AudioCppRuntime(); r.ensure_ready=Mock(); r.load_yue2=Mock()
        state={'id':YUE2_MODEL_ID,'loaded':False,'session_options':session_lora_options(config)}
        with patch.object(r,'models',return_value=[state]),patch.object(r,'_json_request',return_value={'audio':base64.b64encode(b'wav').decode()}):
            r.generate_yue2('lyrics','style','',42,output_dir=self.root,lora_config=config)
        r.load_yue2.assert_called_once_with(session_lora_options(config))

    def test_invalid_header_prevents_runtime_load_and_failure_emits_without_crash(self):
        invalid=self.root/'invalid.safetensors'; invalid.write_bytes(b'bad')
        r=AudioCppRuntime(); r.ensure_ready=Mock(); failed=Mock()
        task=GenerationTask(lambda:r.generate_yue2('lyrics','style','',42,lora_config={'ar_lora':str(invalid)}))
        task.failed.connect(failed); task.run()
        self.assertIn('Missing SafeTensors header',failed.call_args.args[0]); r.ensure_ready.assert_not_called()

    def test_real_audio_cpp_error_propagates_and_next_none_clears_failed_config(self):
        ar=write_adapter(self.root/'ar.safetensors')
        r=AudioCppRuntime(); r.ensure_ready=Mock()
        states=[{'id':YUE2_MODEL_ID,'loaded':True},
                {'id':YUE2_MODEL_ID,'loaded':False,'session_options':{'yue2.ar_lora':ar}}]
        with patch.object(r,'models',side_effect=[[state] for state in states]), \
             patch.object(r,'_json_request',side_effect=[RuntimeError('audio.cpp request failed: HTTP 500: LoRA shape mismatch'),{}, {'audio':base64.b64encode(b'wav').decode()}]) as send:
            with self.assertRaisesRegex(RuntimeError,'LoRA shape mismatch'):
                r.generate_yue2('lyrics','style','',42,output_dir=self.root,lora_config={'ar_lora':ar})
            r.generate_yue2('lyrics','style','',42,output_dir=self.root)
        self.assertFalse(any('lora' in key for key in send.call_args_list[1].args[1]['session_options']))

    def test_alias_and_relative_session_options_compare_correctly(self):
        from comfymax_audio_chunker.editor.yue2_lora import LORA_ROOT
        value=active_lora_options({'path':'../../models/yue2','session_options':{'ar_lora':'loras/a.safetensors','ar_lora_scale':'1'}})
        self.assertEqual(value,{'yue2.ar_lora':str((LORA_ROOT/'a.safetensors').resolve()),'yue2.ar_lora_scale':1.0})


class LoraPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.config=self.root/'settings.json'
        self.hosts=[]
    def host(self):
        root=self.root/'loras'
        class LoraHost(Host): generation_lora_root=root
        h=LoraHost(self.config); self.hosts.append(h); return h
    def tearDown(self):
        for h in self.hosts:
            h.generation_gpu_timer.stop(); h.generation_pane.deleteLater(); h.lyrics_editor.deleteLater()
        self.app.processEvents(); self.temp.cleanup()

    def test_empty_dropdowns_none_default_strength_and_compact_range(self):
        h=self.host()
        for combo,scale in h.generation_loras.values():
            self.assertEqual(combo.count(),1); self.assertEqual(combo.currentText(),'None')
            self.assertEqual((scale.value(),scale.minimum(),scale.maximum()),(1.0,0.0,2.0))

    def test_refresh_open_view_selection_persistence_and_deleted_fallback(self):
        h=self.host(); ar=write_adapter(h.generation_lora_root/'nar-name.safetensors')
        nar=write_adapter(h.generation_lora_root/'ar-name.safetensors','nar')
        h.refresh_generation_inputs()
        for kind,path in [('ar',ar),('nar',nar)]:
            combo,scale=h.generation_loras[kind]; self.assertEqual(combo.count(),2)
            combo.setCurrentIndex(1); scale.setValue(.7)
            self.assertEqual(h.generation_request()['loras'][kind+'_lora'],path)
        other=self.host()
        for kind in ('ar','nar'):
            combo,scale=other.generation_loras[kind]; self.assertEqual(combo.currentIndex(),1); self.assertEqual(scale.value(),.7)
        Path(ar).unlink(); other.refresh_generation_loras()
        self.assertIsNone(other.generation_loras['ar'][0].currentData())
        self.assertEqual(load_settings(self.config)['yue2_ar_lora'],'')
        self.assertIn('using None',other.generation_lora_status.text())

    def test_missing_saved_adapter_startup_falls_back(self):
        save_settings({'yue2_ar_lora':str(self.root/'missing.safetensors')},self.config)
        h=self.host(); self.assertEqual(h.generation_loras['ar'][0].currentText(),'None')

    def test_ui_mapping_failure_recovery_and_metadata_export(self):
        h=self.host(); ar=write_adapter(h.generation_lora_root/'ar.safetensors')
        h.refresh_generation_loras(); h.generation_loras['ar'][0].setCurrentIndex(1)
        h.generation_loras['ar'][1].setValue(1.25); h.generation_use_abc.setChecked(False)
        h.audiocpp_runtime.generate_yue2.side_effect=RuntimeError('Unsupported yue2.ar_lora tensor: wrong adapter')
        h.generate_music()
        for _ in range(100):
            self.app.processEvents(); QTest.qWait(5)
            if h.generation_job is None: break
        self.assertTrue(h.generation_generate.isEnabled())
        self.assertIn('Unsupported yue2.ar_lora tensor',h.generation_output_status.text())
        self.assertEqual(h.audiocpp_runtime.generate_yue2.call_args.kwargs['lora_config']['ar_lora_scale'],1.25)
        h.generation_output_path=self.root/'preview.wav'; h.generation_output_path.write_bytes(b'wav')
        payload={'loras':h.generation_lora_config()}
        h.generation_output_path.with_suffix('.json').write_text(json.dumps(payload))
        target=self.root/'saved.wav'
        with patch.object(QFileDialog,'getSaveFileName',return_value=(str(target),'')): h.save_generation_audio()
        self.assertEqual(json.loads(target.with_suffix('.json').read_text()),payload)

    def test_open_folder_uses_fixed_directory_and_invalid_file_visible_error(self):
        h=self.host(); (h.generation_lora_root/'invalid.safetensors').write_bytes(b'bad')
        h.refresh_generation_loras(); self.assertIn('invalid.safetensors',h.generation_lora_status.text())
        with patch('comfymax_audio_chunker.editor.generation_panel.QDesktopServices.openUrl',return_value=True) as open_url:
            h.open_generation_lora_folder()
        self.assertEqual(open_url.call_args.args[0].toLocalFile(),str(h.generation_lora_root.resolve()).replace('\\','/'))


class LoraRuntimeSmokeTests(unittest.TestCase):
    def test_bundled_runtime_loads_ar_nar_both_and_recovers_from_rejection(self):
        r=AudioCppRuntime(health_url='http://127.0.0.1:18081/health')
        if not r.executable.is_file(): self.skipTest('Bundled runtime unavailable')
        if r.health(): self.skipTest('Test port 18081 is already in use')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            ar=write_adapter(root/'ar.safetensors','ar',(1,2048),(2048,1))
            nar=write_adapter(root/'nar.safetensors','nar',(1,2048),(2048,1))
            bad=write_adapter(root/'wrong-shape.safetensors')
            try:
                r.process=subprocess.Popen([str(r.executable),'--no-ui','--ui-management',
                    '--config',str(r.config),'--port','18081'],cwd=r.runtime_dir,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                r.owned=True
                for _ in range(100):
                    if r.health(): break
                    time.sleep(.1)
                self.assertTrue(r.health())
                for kinds in [('ar',),('nar',),('ar','nar')]:
                    config={kind+'_lora':ar if kind=='ar' else nar for kind in kinds}
                    config.update({kind+'_lora_scale':.75 for kind in kinds})
                    options=session_lora_options(config); r.load_yue2(options)
                    state=r.models()[0]; self.assertTrue(state['loaded'])
                    self.assertEqual(active_lora_options(state),options)
                with self.assertRaisesRegex(RuntimeError,'LoRA shape mismatch'):
                    r.load_yue2(session_lora_options({'ar_lora':bad}))
                self.assertFalse(r.models()[0]['loaded'])
                r.load_yue2(); state=r.models()[0]
                self.assertTrue(state['loaded']); self.assertEqual(active_lora_options(state),{})
                r.unload_yue2(); self.assertFalse(r.models()[0]['loaded'])
            finally: r.stop()


if __name__=='__main__': unittest.main()
