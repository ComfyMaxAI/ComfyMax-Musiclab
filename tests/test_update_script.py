import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
UPDATE=ROOT/'Update.bat'


FAKE_GIT=r'''@echo off
echo %*>>"%MUSICLAB_GIT_LOG%"
if "%~1"=="--version" (
  echo git version test
  exit /b 0
)
if "%~1"=="rev-parse" if "%~2"=="--show-toplevel" (
  if /I "%MUSICLAB_SCENARIO%"=="norepo" exit /b 1
  echo %MUSICLAB_TEST_ROOT%
  exit /b 0
)
if "%~1"=="symbolic-ref" (
  echo main
  exit /b 0
)
if "%~1"=="rev-parse" if "%~2"=="--abbrev-ref" (
  if /I "%MUSICLAB_SCENARIO%"=="noupstream" exit /b 1
  echo origin/main
  exit /b 0
)
if "%~1"=="status" (
  if /I "%MUSICLAB_SCENARIO%"=="dirty" echo  M tracked.py
  exit /b 0
)
if "%~1"=="fetch" (
  if /I "%MUSICLAB_SCENARIO%"=="fetchfail" exit /b 1
  exit /b 0
)
if "%~1"=="rev-list" (
  if /I "%MUSICLAB_SCENARIO%"=="behind" (
    echo 0 1
    exit /b 0
  )
  if /I "%MUSICLAB_SCENARIO%"=="pullfail" (
    echo 0 1
    exit /b 0
  )
  if /I "%MUSICLAB_SCENARIO%"=="divergent" (
    echo 1 1
    exit /b 0
  )
  echo 0 0
  exit /b 0
)
if "%~1"=="pull" (
  if /I "%MUSICLAB_SCENARIO%"=="pullfail" exit /b 1
  exit /b 0
)
exit /b 1
'''


class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); base=Path(self.temp.name)
        self.root=base/'MusicLab'; self.root.mkdir()
        self.bin=base/'bin'; self.bin.mkdir(); self.outside=base/'outside'; self.outside.mkdir()
        (self.root/'Update.bat').write_bytes(UPDATE.read_bytes())
        (self.bin/'git.cmd').write_text(FAKE_GIT,encoding='utf-8')
        (self.root/'Install.bat').write_text(
            '@echo off\necho install>>"%MUSICLAB_INSTALL_LOG%"\n'
            'if "%MUSICLAB_INSTALL_FAIL%"=="1" exit /b 1\nexit /b 0\n',encoding='utf-8')
        self.git_log=base/'git.log'; self.install_log=base/'install.log'
        self.env=os.environ.copy()
        self.env.update(COMFYMAX_NO_PAUSE='1',MUSICLAB_TEST_ROOT=str(self.root),
                        MUSICLAB_GIT_LOG=str(self.git_log),MUSICLAB_INSTALL_LOG=str(self.install_log))
        self.env['PATH']=str(self.bin)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')

    def tearDown(self): self.temp.cleanup()

    def run_update(self,scenario='uptodate',env=None):
        values=dict(self.env if env is None else env); values['MUSICLAB_SCENARIO']=scenario
        return subprocess.run([str(self.root/'Update.bat')],cwd=self.outside,env=values,
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)

    def commands(self): return self.git_log.read_text(encoding='utf-8') if self.git_log.exists() else ''

    def test_script_is_relocatable_and_contains_no_destructive_or_install_logic(self):
        script=UPDATE.read_text(encoding='utf-8'); lower=script.lower()
        self.assertIn('%~dp0',script); self.assertNotIn('D:\\ComfyMax-Musiclab',script)
        self.assertIn('git status --porcelain --untracked-files=no',script)
        self.assertIn('git pull --ff-only',script); self.assertIn('call "%ROOT%Install.bat"',script)
        for forbidden in ('git reset','git clean','git checkout','models\\','invoke-webrequest','pip install','audiocpp_server.exe'):
            self.assertNotIn(forbidden,lower)

    def test_git_missing_stops_cleanly(self):
        env=dict(self.env); env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        result=self.run_update(env=env)
        self.assertNotEqual(result.returncode,0); self.assertIn('[ERROR] Git was not found.',result.stdout)
        self.assertFalse(self.install_log.exists())

    def test_non_repository_stops_without_install(self):
        result=self.run_update('norepo')
        self.assertNotEqual(result.returncode,0); self.assertIn('only available for a Git installation',result.stdout)
        self.assertFalse(self.install_log.exists())

    def test_tracked_local_change_stops_before_fetch(self):
        result=self.run_update('dirty')
        self.assertNotEqual(result.returncode,0); self.assertIn('Local project changes were detected',result.stdout)
        self.assertNotIn('fetch',self.commands()); self.assertFalse(self.install_log.exists())

    def test_up_to_date_still_runs_installer(self):
        result=self.run_update('uptodate')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('already up to date',result.stdout); self.assertIn('fetch',self.commands())
        self.assertNotIn('pull --ff-only',self.commands()); self.assertTrue(self.install_log.exists())

    def test_behind_branch_fast_forwards_then_runs_installer(self):
        result=self.run_update('behind')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('MusicLab updated',result.stdout); self.assertIn('pull --ff-only',self.commands())
        self.assertTrue(self.install_log.exists())

    def test_divergent_branch_stops_without_pull_or_installer(self):
        result=self.run_update('divergent')
        self.assertNotEqual(result.returncode,0); self.assertIn('has diverged',result.stdout)
        self.assertNotIn('pull --ff-only',self.commands()); self.assertFalse(self.install_log.exists())

    def test_missing_upstream_stops_before_fetch(self):
        result=self.run_update('noupstream')
        self.assertNotEqual(result.returncode,0); self.assertIn('no upstream branch',result.stdout)
        self.assertNotIn('fetch',self.commands()); self.assertFalse(self.install_log.exists())

    def test_fetch_failure_stops_without_pull_or_installer(self):
        result=self.run_update('fetchfail')
        self.assertNotEqual(result.returncode,0); self.assertIn('Git fetch failed',result.stdout)
        self.assertNotIn('pull --ff-only',self.commands()); self.assertFalse(self.install_log.exists())

    def test_pull_failure_is_reported_without_destructive_recovery(self):
        result=self.run_update('pullfail')
        self.assertNotEqual(result.returncode,0); self.assertIn('fast-forward update failed',result.stdout)
        self.assertIn('pull --ff-only',self.commands()); self.assertFalse(self.install_log.exists())

    def test_installer_failure_is_reported(self):
        env=dict(self.env); env['MUSICLAB_INSTALL_FAIL']='1'
        result=self.run_update('uptodate',env)
        self.assertNotEqual(result.returncode,0); self.assertIn('Install.bat failed',result.stdout)
        self.assertTrue(self.install_log.exists())


if __name__=='__main__': unittest.main()
