"""Host-side staged infrastructure diagnostics; never runs tests outside netns."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True)
    a=p.parse_args();out=Path(a.output_dir).resolve();diag=out.with_name(out.name+'-launcher')
    diag.mkdir(parents=True,exist_ok=False)
    records=[]
    def stage(name,cmd):
        print('DIAGNOSTIC_STAGE '+name,flush=True)
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
        print(r.stdout,flush=True)
        records.append({'stage':name,'exit_code':r.returncode,'output':r.stdout})
        with (diag/(name+'.json')).open('x') as f:json.dump(records[-1],f,indent=2)
        if r.returncode:raise RuntimeError(name+' failed; exit '+str(r.returncode))
        return r.stdout
    try:
        for tool in ('sudo','unshare','setpriv','ip'):
            if not shutil.which(tool):raise RuntimeError('prerequisite_missing: '+tool)
        workspace=Path.cwd(); python=Path(sys.executable).resolve()
        with (diag/'paths.json').open('x') as f:
            json.dump({'workspace':str(workspace),'python':str(python),'audit_script':str(workspace/'scripts/run_pubmed_offline_acceptance.py')},f,indent=2)
        stage('sudo_start', ['sudo','-n','true'])
        sudo=['sudo','-n','--preserve-env=GITHUB_REPOSITORY,GITHUB_RUN_ID,GITHUB_RUN_ATTEMPT,GITHUB_SHA,RUNNER_NAME,ImageOS,ImageVersion']
        namespace=sudo+['unshare','--net','--']
        stage('namespace_creation',namespace+[str(python),'-c',"import os;print(os.readlink('/proc/self/ns/net'))"])
        lowered=namespace+['setpriv','--reuid='+str(os.getuid()),'--regid='+str(os.getgid()),'--clear-groups','--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all','--no-new-privs',str(python)]
        stage('setpriv_downgrade',lowered+['-c',"import os; assert os.getuid()!=0; print('uid',os.getuid());print(open('/proc/self/status').read().split('CapEff:')[1].splitlines()[0])"])
        probe="import os,pathlib,tempfile; p=pathlib.Path.cwd(); assert os.access(p,os.R_OK|os.W_OK|os.X_OK); assert pathlib.Path('scripts/run_pubmed_offline_acceptance.py').is_file(); f=tempfile.TemporaryFile(dir=p); f.write(b'permission probe'); f.close(); print('workspace readable/writable, script present, Python started')"
        stage('workspace_permissions',lowered+['-c',probe])
        host=os.readlink('/proc/self/ns/net')
        cmd=lowered+['scripts/run_pubmed_offline_acceptance.py','--output-dir',str(out),'--host-netns',host]
        print('DIAGNOSTIC_STAGE isolated_audit_and_unittest',flush=True)
        with (diag/'isolated-process.log').open('x',encoding='utf-8') as log:
            with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) as proc:
                for line in proc.stdout:
                    print(line,end='',flush=True);log.write(line);log.flush()
                code=proc.wait()
        category='SUCCESS'
        if code:
            category='ISOLATED_PROCESS_FAILURE'
            report_path=out/'final-acceptance-report.json'
            if report_path.exists():
                report=json.loads(report_path.read_text())
                category='ISOLATION_VALIDATION_FAILURE' if not report.get('network_isolation_effective') else 'UNITTEST_OR_AUDIT_FAILURE'
                if report.get('tests',{}).get('status')=='FAIL':category='UNITTEST_FAILURE'
        records.append({'stage':'isolated_audit_and_unittest','exit_code':code,'category':category})
        with (diag/'launcher-result.json').open('x') as f:json.dump(records,f,indent=2)
        print('LAUNCHER_RESULT '+category,flush=True)
        return code
    except Exception as exc:
        print('LAUNCHER_ERROR '+str(exc),file=sys.stderr,flush=True)
        with (diag/'launcher-error.json').open('x') as f:json.dump({'error':str(exc),'completed_stages':records},f,indent=2)
        return 1

if __name__=='__main__':raise SystemExit(main())
