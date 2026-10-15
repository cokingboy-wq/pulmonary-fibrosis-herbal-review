"""Audit the actual unittest command inside a capability-free Linux net namespace."""
import argparse
import errno
import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASELINE='ca5e29388f4416665426f033f55dbb653067fdec'

def utc():return datetime.now(timezone.utc).isoformat()
def write(out,name,value):
    with (out/name).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)
def digest(b):return hashlib.sha256(b).hexdigest()

def route_table_safe(routes):
    # iproute2 JSON is the authoritative table; /proc/net/route can be empty
    # (not even a header) for an isolated unprivileged process on hosted kernels.
    return isinstance(routes,list) and not any(r.get('type') not in ('unreachable','blackhole','prohibit') for r in routes)

def isolation(host_namespace):
    current=os.readlink('/proc/self/ns/net')
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    interfaces=socket.if_nameindex()
    route=Path('/proc/net/route').read_text()
    route6=Path('/proc/net/ipv6_route').read_text()
    ipv4_routes=json.loads(subprocess.check_output(['ip','-j','-4','route','show','table','all'],text=True))
    ipv6_routes=json.loads(subprocess.check_output(['ip','-j','-6','route','show','table','all'],text=True))
    evidence={'method':'unshare --net; setpriv non-root, no capabilities and no_new_privs',
        'host_namespace':host_namespace,'test_namespace':current,'interfaces':interfaces,
        'uid':os.getuid(),'CapEff':status['CapEff'].strip(),
        'CapBnd':status['CapBnd'].strip(),'NoNewPrivs':status['NoNewPrivs'].strip(),
        'ipv4_routes':route,'ipv4_route_details':ipv4_routes,'ipv6_routes':route6,'ipv6_route_details':ipv6_routes,'probe_target':'192.0.2.1:443 (documentation address; no NCBI access)'}
    with socket.socket() as probe:
        probe.settimeout(1)
        code=probe.connect_ex(('192.0.2.1',443))
    evidence['probe_errno']=code
    evidence['effective']=(current!=host_namespace and os.getuid()!=0
        and int(evidence['CapEff'],16)==0 and int(evidence['CapBnd'],16)==0
        and evidence['NoNewPrivs']=='1' and {n for i,n in interfaces}=={'lo'}
        and route_table_safe(ipv4_routes) and route_table_safe(ipv6_routes)
        and code in (errno.ENETUNREACH,errno.EHOSTUNREACH))
    return evidence

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--host-netns',required=True)
    a=p.parse_args();out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();errors=[];net={};hashes={};summary={}
    metadata={'repository':os.getenv('GITHUB_REPOSITORY'),'run_id':os.getenv('GITHUB_RUN_ID'),
        'run_attempt':os.getenv('GITHUB_RUN_ATTEMPT'),'commit_sha':os.getenv('GITHUB_SHA'),
        'baseline_commit':BASELINE,'timestamp_utc':utc(),'stage':'SR-PUBMED-002-001-G',
        'formal_retrieval_started':False,'formal_run_id':None}
    env={'runner_os':platform.platform(),'python_version':sys.version,'runner_name':os.getenv('RUNNER_NAME'),
        'runner_image_os':os.getenv('ImageOS'),'runner_image_version':os.getenv('ImageVersion'),
        'timestamp_utc':utc()}
    write(out,'workflow-run-metadata.json',metadata)
    try:
        b=Path('02_Search_Strategies/PubMed_SR-PUBMED-001_query.txt').read_bytes()
        q=b.decode('utf-8').split('\n\n',1)[1].rstrip('\r\n')
        hashes={'source_sha256':digest(b),'body_sha256':digest(q.encode()),'body_characters':len(q)}
        hashes['match']=(hashes['source_sha256']=='13aeeda4c5138fa93e2fdeb1a4c028d5e5aaa422aad410c2e440c091f241fa51'
            and hashes['body_sha256']=='9db3953a99b295813d21f30e3af8af278b13d45c154be8bb794a878cb27f6ddf' and len(q)==1698)
        if not hashes['match']:raise ValueError('Frozen hashes or length mismatch')
        net=isolation(a.host_netns)
        print('ISOLATION_DIAGNOSTICS '+json.dumps(net),flush=True)
        if not net['effective']:raise ValueError('Network namespace isolation verification failed')
        before=time.monotonic()
        with (out/'offline-test-results.txt').open('x',encoding='utf-8') as log:
            proc=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],stdout=log,stderr=subprocess.STDOUT,timeout=900)
        content=(out/'offline-test-results.txt').read_text()
        print(content,flush=True)
        ran=re.search(r'Ran (\d+) tests? in ([0-9.]+)s',content)
        outcome=re.findall(r'^(?:OK|FAILED)(?: \(([^\n]*)\))?$',content,re.M)
        counts={k:int(v) for k,v in re.findall(r'(failures|errors|skipped)=(\d+)',outcome[-1] if outcome else '')}
        summary={'command':'python -m unittest discover -s tests -v','exit_code':proc.returncode,
            'tests_run':int(ran[1]) if ran else None,'failures':counts.get('failures',0),
            'errors':counts.get('errors',0),'skipped':counts.get('skipped',0),
            'unittest_seconds':float(ran[2]) if ran else None,'wall_seconds':time.monotonic()-before,
            'previous_reported_count':81,'count_difference':int(ran[1])-81 if ran else None,
            'status':'PASS' if proc.returncode==0 and ran and outcome else 'FAIL'}
        if summary['status']!='PASS':errors.append('Actual unittest run did not pass')
    except Exception as exc:
        errors.append(str(exc))
        print('OFFLINE_ACCEPTANCE_ERROR: '+str(exc),file=sys.stderr,flush=True)
        traceback.print_exc()
        with (out/'infrastructure-errors.txt').open('x',encoding='utf-8') as f:f.write(traceback.format_exc())
    finally:
        if not (out/'offline-test-results.txt').exists():
            (out/'offline-test-results.txt').write_text('NOT_EXECUTED: pre-test failure; see audit errors\n')
        env['network_isolation']=net
        write(out,'environment.json',env);write(out,'frozen-hashes.json',hashes)
        write(out,'test-summary.json',summary or {'status':'NOT_EXECUTED','tests_run':None})
        report={'cloud_test_status':'PASS' if not errors and summary.get('status')=='PASS' else 'FAIL',
            'final_status':'PARTIAL' if not errors and summary.get('status')=='PASS' else 'FAIL',
            'local_artifact_acceptance':'NOT_EXECUTED','errors':errors,'metadata':metadata,
            'network_isolation_effective':net.get('effective',False),'frozen_hashes_match':hashes.get('match',False),
            'tests':summary,'utc_completed':utc(),'elapsed_seconds':time.monotonic()-started,
            'note':'Cloud-only PASS is not final acceptance; independent Windows ZIP/digest verification required.'}
        write(out,'final-acceptance-report.json',report)
        rows=[{'path':f.name,'bytes':f.stat().st_size,'sha256':digest(f.read_bytes())} for f in sorted(out.iterdir()) if f.is_file()]
        write(out,'sha256_manifest.json',{'files':rows,'manifest_self_hash':'Anchor via independently captured Artifact ZIP digest'})
    return 0 if report['cloud_test_status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
