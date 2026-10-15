import argparse, hashlib, json, os

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--query-file',required=True); p.add_argument('--output-dir',required=True); p.add_argument('--run-id',required=True); p.add_argument('--dry-run',action='store_true'); a=p.parse_args()
    if not a.dry_run: raise SystemExit('Refusing network execution: --dry-run is required for preflight')
    if not os.path.isfile(a.query_file): raise SystemExit('query file missing')
    if sha256(a.query_file) != '13aeeda4c5138fa93e2fdeb1a4c028d5e5aaa422aad410c2e440c091f241fa51': raise SystemExit('Frozen SHA256 mismatch')
    os.makedirs(a.output_dir,exist_ok=True)
    result={'query_file':os.path.abspath(a.query_file),'query_sha256':sha256(a.query_file),'output_dir':os.path.abspath(a.output_dir),'run_id_parameter':a.run_id,'parameter_checks':{'dry_run':True,'network_requests':False,'ncbi_accessed':False}}
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()