import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
A=load('audit_namespace','scripts/run_pubmed_offline_acceptance.py')
L=load('launcher','scripts/launch_pubmed_offline_acceptance.py')

class DiagnosticsTests(unittest.TestCase):
    def isolation(self,route_text='',routes=None,uid=1001):
        process='CapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nNoNewPrivs:\t1\n'
        def read(p,*a,**k):
            return process if p.as_posix()=='/proc/self/status' else route_text if p.as_posix()=='/proc/net/route' else ''
        sock=MagicMock();sock.__enter__.return_value.connect_ex.return_value=A.errno.ENETUNREACH
        with patch.object(A.os,'readlink',return_value='net:[isolated]'),patch.object(A.os,'getuid',return_value=uid,create=True),patch.object(A.socket,'if_nameindex',return_value=[(1,'lo')]),patch.object(A.socket,'socket',return_value=sock),patch.object(A.Path,'read_text',read),patch.object(A.subprocess,'check_output',return_value=json.dumps(routes or [])):
            return A.isolation('net:[host]')
    def test_empty_proc_route_regression(self):self.assertTrue(self.isolation()['effective'])
    def test_header_only_proc_route(self):self.assertTrue(self.isolation('Iface Destination Gateway')['effective'])
    def test_real_route_not_allowed(self):self.assertFalse(self.isolation(routes=[{'dst':'default','dev':'eth0'}])['effective'])
    def test_root_not_allowed(self):self.assertFalse(self.isolation(uid=0)['effective'])
    def fail_stage(self,index,expected):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run';calls=[]
            def run(cmd,**kwargs):
                i=len(calls);calls.append(cmd)
                return type('Result',(),{'returncode':1 if i==index else 0,'stdout':'injected diagnostic failure' if i==index else 'ok'})()
            with patch.object(L.shutil,'which',return_value='/usr/bin/tool'),patch.object(L.subprocess,'run',side_effect=run),patch.object(L.os,'getuid',return_value=1001,create=True),patch.object(L.os,'getgid',return_value=1001,create=True),patch('sys.argv',['launcher','--output-dir',str(out)]),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(L.main(),1)
            report=json.loads((Path(d)/'run-launcher/launcher-error.json').read_text())
            self.assertIn(expected,report['error']);self.assertEqual(report['completed_stages'][-1]['exit_code'],1)
            self.assertFalse(out.exists())
    def test_sudo_failure_recorded(self):self.fail_stage(0,'sudo_start')
    def test_unshare_failure_recorded(self):self.fail_stage(1,'namespace_creation')
    def test_setpriv_failure_recorded(self):self.fail_stage(2,'setpriv_downgrade')
    def test_workspace_failure_recorded(self):self.fail_stage(3,'workspace_permissions')

if __name__=='__main__':unittest.main()
