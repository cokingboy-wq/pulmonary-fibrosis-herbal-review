"""Offline artifact acceptance. Does not import or trust the execution program."""
import argparse
import hashlib
import json
import re
import stat
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

def digest(data): return hashlib.sha256(data).hexdigest()
def read_json(path): return json.loads(path.read_bytes())

def verify_directory(base):
    base = Path(base)
    try:
        manifest = read_json(base/'sha256_manifest.json')
        paths = set()
        for row in manifest['files']:
            name=row['path']
            if not isinstance(name,str) or name in paths or PurePosixPath(name).name != name or ':' in name or '\\' in name or name in ('.','..','sha256_manifest.json'):
                raise ValueError('Unsafe or duplicate manifest path')
            paths.add(name)
            path=base/name
            if path.is_symlink() or not path.is_file():raise ValueError('Missing or unsafe manifest file')
            first=path.read_bytes(); second=path.read_bytes()
            if first!=second or len(second)!=row['bytes'] or digest(second)!=row['sha256']:
                raise ValueError('Size/hash/reopen mismatch: '+name)
        actual={p.name for p in base.iterdir() if p.is_file() and p.name!='sha256_manifest.json'}
        if actual!=paths:raise ValueError('Unmanifested or missing files')
        required={'run_metadata_initial.json','run_metadata.json','final_report.json','frozen_source.txt','requests.jsonl'}
        if not required<=paths:raise ValueError('Required evidence absent')
        source=(base/'frozen_source.txt').read_bytes()
        if digest(source)!='13aeeda4c5138fa93e2fdeb1a4c028d5e5aaa422aad410c2e440c091f241fa51':raise ValueError('Frozen source mismatch')
        term=source.decode().split('\n\n',1)[1].rstrip('\r\n')
        if digest(term.encode())!='9db3953a99b295813d21f30e3af8af278b13d45c154be8bb794a878cb27f6ddf':raise ValueError('Query body mismatch')
        report=read_json(base/'final_report.json'); meta=read_json(base/'run_metadata.json')
        if type(meta.get('simulation')) is not bool:raise ValueError('simulation must be explicitly boolean')
        if report['run_id']!=meta['run_id']:raise ValueError('Run identities disagree')
        events=[json.loads(line) for line in (base/'requests.jsonl').read_text().splitlines()]
        starts={(e['batch'],e['attempt']) for e in events if e.get('event')=='ATTEMPT_STARTED'}
        finishes=[e for e in events if e.get('event')=='ATTEMPT_FINISHED']
        keys=[(e['batch'],e['attempt']) for e in finishes]
        if len(keys)!=len(set(keys)) or set(keys)!=starts:raise ValueError('Unclosed/duplicate request attempt')
        raw_files=set()
        successes={}
        for e in finishes:
            if not e.get('timestamp_utc') or not e.get('parameters') or not e.get('endpoint','').startswith('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'):
                raise ValueError('Incomplete request provenance')
            if e['parameters'].get('db')!='pubmed':raise ValueError('Unexpected database')
            if 'file' in e:
                if e['file'] not in paths:raise ValueError('Request file outside manifest')
                raw_files.add(e['file']); b=(base/e['file']).read_bytes()
                if len(b)!=e['bytes'] or digest(b)!=e['sha256']:raise ValueError('Request/raw hash mismatch')
            if e.get('http_status')==200 and not e.get('error'):
                if not e.get('transport_complete'):raise ValueError('Transport not verified')
                headers={k.lower():v for k,v in e['headers'].items()}
                if headers.get('content-encoding','identity').lower()!='identity':raise ValueError('Encoding unsupported')
                if 'content-length' in headers and int(headers['content-length'])!=e['bytes']:raise ValueError('Length mismatch')
                if e['batch'] in successes:raise ValueError('Ambiguous successful attempt')
                successes[e['batch']]=e
        if {p.name for p in base.glob('*.raw')}!=raw_files:raise ValueError('Unexplained raw response')
        complete=True; ids=[]; fetched=[]; count=None; batches=[]
        try:
            initial=successes['esearch_initial']
            if initial['parameters'].get('term')!=term or initial['parameters'].get('db')!='pubmed' or int(initial['parameters'].get('retmax',-1))!=0 or initial['parameters'].get('usehistory')!='y':raise ValueError('Initial query/parameters changed')
            root=ET.fromstring((base/initial['file']).read_bytes())
            if root.tag!='eSearchResult' or root.find('.//ERROR') is not None or root.find('.//ErrorList') is not None:raise ValueError('Search error XML')
            for field in ('Count','RetStart','RetMax','IdList','QueryTranslation','WebEnv','QueryKey'):
                if root.find(field) is None:raise ValueError('Search schema missing')
            count=int(root.findtext('Count'));web=root.findtext('WebEnv');key=root.findtext('QueryKey')
            if count<0 or count>9999 or not web or not key.isdigit() or not root.findtext('QueryTranslation') or int(root.findtext('RetStart'))!=0 or int(root.findtext('RetMax'))!=0:raise ValueError('Initial search invalid')
            if report.get('count')!=count or report.get('query_translation')!=root.findtext('QueryTranslation'):raise ValueError('Report/search mismatch')
            pages=sorted((e for n,e in successes.items() if n.startswith('esearch_') and n!='esearch_initial'),key=lambda e:int(e['parameters']['retstart']))
            position=0;history_pages=[]
            for e in pages:
                p=e['parameters']; r=ET.fromstring((base/e['file']).read_bytes()); n=int(p['retmax'])
                if p.get('retmode')!='xml':raise ValueError('Wrong page format')
                if p.get('usehistory')!='y':raise ValueError('History page request missing usehistory=y')
                if p.get('WebEnv')!=web or p.get('term')!='#'+key or int(p['retstart'])!=position:raise ValueError('History/gap/order mismatch')
                if r.tag!='eSearchResult' or int(r.findtext('Count'))!=count or int(r.findtext('RetStart'))!=position or int(r.findtext('RetMax'))!=n:raise ValueError('Page boundary mismatch')
                if r.findtext('WebEnv') is not None and r.findtext('WebEnv')!=web:raise ValueError('Returned WebEnv mismatch')
                output_key=r.findtext('QueryKey')
                if output_key is not None and not re.fullmatch(r'[1-9][0-9]*',output_key):raise ValueError('Invalid output History key')
                if r.find('.//WarningList') is not None or r.find('.//ERROR') is not None or r.find('.//ErrorList') is not None:raise ValueError('History page server error/warning')
                page=[x.text for x in r.findall('IdList/Id')]
                if len(page)!=n or n<=0 or position+n>count:raise ValueError('Page cardinality mismatch')
                history_pages.append({'source_webenv':web,'source_query_key':key,'response_webenv':r.findtext('WebEnv'),
                    'response_query_key':output_key,'retstart':position,'retmax':n,'count':count,'pmids':page})
                ids.extend(page);position+=n
            if len(ids)!=count or len(set(ids))!=count or any(not x or not x.isdigit() for x in ids):raise ValueError('PMID completeness mismatch')
            if read_json(base/'pmids_ordered.json')!=ids:raise ValueError('Saved PMID order mismatch')
            if report.get('history_pages')!=history_pages:raise ValueError('Report History source/output binding mismatch')
            position=0
            for e in sorted((e for n,e in successes.items() if n.startswith('efetch_')),key=lambda e:int(e['batch'].split('_')[1])):
                if e['parameters'].get('retmode')!='text' or e['parameters'].get('rettype')!='medline':raise ValueError('Wrong fetch format')
                if int(e['batch'].split('_')[1])!=position:raise ValueError('Missing fetch batch')
                requested=e['parameters']['id'].split(',')
                if ids[position:position+len(requested)]!=requested:raise ValueError('Fetch request mismatch')
                body=(base/e['file']).read_bytes();text=body.decode('utf-8')
                if not text.endswith('\n'):raise ValueError('Truncated MEDLINE terminal')
                received=[]
                for record in re.split(r'\r?\n[ \t]*\r?\n',text.strip()):
                    fields={};prior=False
                    for line in record.splitlines():
                        match=re.fullmatch(r'([A-Z][A-Z0-9]{1,3})[ \t]*-[ \t]+(.+)',line)
                        if match:fields.setdefault(match[1],[]).append(match[2].strip());prior=True
                        elif not prior or not line.startswith('      '):raise ValueError('Malformed record')
                    if len(fields.get('PMID',[]))!=1 or not fields.get('TI') or not fields.get('DP'):raise ValueError('Record incomplete')
                    received.extend(fields['PMID'])
                if len(received)!=len(requested) or set(received)!=set(requested):raise ValueError('Batch IDs differ')
                batches.append({'start':position,'requested':requested,'received':received})
                fetched.extend(received);position+=len(requested)
            if position!=count or len(set(fetched))!=count:raise ValueError('Fetch incomplete')
            if report.get('batches')!=batches:raise ValueError('Report batch mismatch')
            allowed={'esearch_initial'}|{e['batch'] for e in pages}|{'efetch_'+str(b['start']) for b in batches}
            if set(successes)!=allowed:raise ValueError('Unexplained successful request')
            expected={'requested_count':len(ids),'fetched_count':len(fetched),'missing':[], 'unexpected':[], 'duplicate_fetched_count':0}
            if report.get('reconciliation')!=expected:raise ValueError('Report reconciliation mismatch')
        except (KeyError,ValueError,TypeError,ET.ParseError,FileNotFoundError) as exc:
            complete=False;reason=str(exc)
        status=report.get('status')
        if status=='API_RETRIEVAL_COMPLETE':
            if not complete or report.get('errors') or report.get('fetched_count')!=count:raise ValueError('False completion: '+(reason if not complete else 'report mismatch'))
            if meta['simulation'] and not meta['run_id'].startswith('SIMULATION-'):raise ValueError('Simulation impersonates formal run')
            return {'status':'SIMULATION_ONLY' if meta['simulation'] else 'INTERNAL_API_CONSISTENT',
                    'internal_consistency':'PASS','external_authenticity':'NOT_VERIFIED',
                    'web_evidence':'PENDING_WEB_EVIDENCE','simulation':meta['simulation']}
        if complete:raise ValueError('Failure report contradicts complete raw evidence')
        if status=='INCOMPLETE' and report.get('errors'):return {'status':'VERIFIED_INCOMPLETE','reason':reason,'internal_consistency':'PASS','external_authenticity':'NOT_VERIFIED'}
        if status=='FAILED' and report.get('errors'):return {'status':'VERIFIED_FAILURE','reason':reason,'internal_consistency':'PASS','external_authenticity':'NOT_VERIFIED'}
        raise ValueError('Unrecognized or unjustified report status')
    except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile) as exc:
        return {'status':'INVALID_EVIDENCE','errors':[str(exc)]}

def authenticate_origin(base, provenance, zip_hash):
    """Compare an independently supplied GitHub API capture; never derive expected ZIP hash from ZIP."""
    if not provenance:raise ValueError('Independent GitHub provenance absent')
    p=provenance
    required=('repository','run_id','run_attempt','commit_sha','artifact_id','artifact_name','zip_sha256','source_url','artifact_run_id','artifact_commit_sha')
    if any(not isinstance(p.get(k),str) or not p[k] for k in required):raise ValueError('Incomplete external provenance')
    repo='cokingboy-wq/pulmonary-fibrosis-herbal-review'
    if p['repository']!=repo or p['source_url']!='https://api.github.com/repos/'+repo+'/actions/artifacts/'+p['artifact_id']:
        raise ValueError('Artifact source/repository mismatch')
    if not all(p[k].isdigit() for k in ('run_id','run_attempt','artifact_id')) or not re.fullmatch('[0-9a-f]{40}',p['commit_sha']):raise ValueError('Invalid source identifiers')
    if not re.fullmatch('[0-9a-f]{64}',p['zip_sha256']) or p['zip_sha256']!=zip_hash:raise ValueError('Missing or mismatched external ZIP digest')
    if p['artifact_name']!='SR-PUBMED-002-'+p['run_id']+'-'+p['run_attempt']:raise ValueError('Artifact name identity mismatch')
    if p['artifact_run_id']!=p['run_id'] or p['artifact_commit_sha']!=p['commit_sha']:raise ValueError('Artifact workflow association mismatch')
    initial=read_json(base/'run_metadata_initial.json');meta=read_json(base/'run_metadata.json');report=read_json(base/'final_report.json')
    for document in [initial,meta,report]+[read_json(x) for x in base.glob('metadata_event_*.json')]:
        if document.get('simulation') is not False:raise ValueError('Not explicitly a real execution')
        if document.get('run_id')!=meta['run_id']:raise ValueError('Formal run identities disagree')
        for local,external in [('github_repository','repository'),('github_run_id','run_id'),('github_run_attempt','run_attempt'),('commit_sha','commit_sha')]:
            if document.get(local)!=p[external]:raise ValueError('Identity/provenance mismatch: '+local)
    if not re.fullmatch(r'SR-PUBMED-002-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}',meta['run_id']):raise ValueError('Invalid formal execution identity')
    return {'status':'MATCHED_INDEPENDENT_GITHUB_CAPTURE','repository':repo,'run_id':p['run_id'],
            'run_attempt':p['run_attempt'],'commit_sha':p['commit_sha'],'artifact_id':p['artifact_id'],
            'artifact_name':p['artifact_name'],'github_zip_sha256':p['zip_sha256'],'local_zip_sha256':zip_hash,
            'trust_boundary':'Caller must obtain provenance through authenticated GitHub independently; offline comparison is not a cryptographic attestation.'}

def verify_zip(zip_path,destination, provenance=None):
    try:
        source=Path(zip_path); zip_hash=digest(source.read_bytes());dest=Path(destination)
        if dest.exists():raise ValueError('Destination exists; refusing overwrite')
        with zipfile.ZipFile(source) as z:
            seen=set();total=0
            for e in z.infolist():
                p=PurePosixPath(e.filename);mode=e.external_attr>>16
                if p.is_absolute() or '..' in p.parts or '\\' in e.filename or ':' in e.filename or e.filename in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)):
                    raise ValueError('Unsafe archive entry')
                reserved={'CON','PRN','AUX','NUL'}|{'COM'+str(i) for i in range(1,10)}|{'LPT'+str(i) for i in range(1,10)}
                if not p.parts or any(part.endswith(('.', ' ')) or part.split('.')[0].upper() in reserved or re.search(r'[<>"|?*\x00-\x1f]',part) for part in p.parts):
                    raise ValueError('Unsafe Windows archive path')
                seen.add(e.filename);total+=e.file_size
                if total>1024**3 or e.flag_bits&1:raise ValueError('Archive exceeds limit or encrypted')
            dest.mkdir(parents=True,exist_ok=False)
            for e in z.infolist():
                path=dest.joinpath(*PurePosixPath(e.filename).parts)
                if e.is_dir():path.mkdir(parents=True,exist_ok=True)
                else:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(e) as src,path.open('xb') as dst:
                        while True:
                            b=src.read(65536)
                            if not b:break
                            dst.write(b)
            manifests=list(dest.rglob('sha256_manifest.json'))
            if len(manifests)!=1:raise ValueError('Expected exactly one run evidence manifest')
            result=verify_directory(manifests[0].parent);result['zip_sha256']=zip_hash
            if result['status']=='INTERNAL_API_CONSISTENT':
                try:
                    result['origin']=authenticate_origin(manifests[0].parent,provenance,zip_hash)
                    result.update(status='VERIFIED_API_RETRIEVAL',external_authenticity='MATCHED_INDEPENDENT_GITHUB_CAPTURE')
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    result.update(status='INVALID_EVIDENCE',external_authenticity='FAILED',errors=[str(exc)])
            return result
    except (OSError,ValueError,zipfile.BadZipFile) as exc:return {'status':'INVALID_EVIDENCE','errors':[str(exc)]}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--destination',required=True);p.add_argument('--provenance')
    a=p.parse_args()
    provenance=None
    if a.provenance:
        if Path(a.provenance).resolve().is_relative_to(Path(a.destination).resolve()):p.error('Provenance must be outside extraction directory')
        provenance=read_json(Path(a.provenance))
    r=verify_zip(a.zip,a.destination,provenance);print(json.dumps(r,indent=2))
    raise SystemExit(0 if r['status']=='VERIFIED_API_RETRIEVAL' else 1)
