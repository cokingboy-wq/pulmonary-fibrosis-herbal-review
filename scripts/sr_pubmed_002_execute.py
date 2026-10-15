"""Explicitly gated PubMed retrieval; importing this module never uses the network."""
import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

EXPECTED = '13aeeda4c5138fa93e2fdeb1a4c028d5e5aaa422aad410c2e440c091f241fa51'
BASE = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
CONFIRM = 'EXECUTE_FROZEN_PUBMED_QUERY'

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def load_query(path):
    data = Path(path).read_bytes()
    if sha(data) != EXPECTED:
        raise ValueError('Frozen source SHA256 mismatch')
    # The frozen source is an audited wrapper followed by one unchanged query.
    text = data.decode('utf-8')
    header, sep, query = text.partition('\n\n')
    if not sep or 'Frozen source: PubMed_v1_2_Frozen_B31' not in header:
        raise ValueError('Unexpected frozen source structure')
    query = query.rstrip('\r\n')
    if len(query) != 1698:
        raise ValueError('Unexpected query length')
    return query

def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    # Atomic publication without replacing an existing evidence file.
    os.link(temp, path)
    temp.unlink()

def log_event(path, value):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(value) + '\n')
        f.flush()
        os.fsync(f.fileno())

class Client:
    def __init__(self, out, timeout=30, retries=2, delay=0.4, opener=None, sleep=time.sleep, observer=None):
        self.out, self.timeout, self.retries = Path(out), timeout, retries
        self.delay, self.opener, self.sleep = delay, opener or urllib.request.urlopen, sleep
        self.attempts = []
        self.observer = observer or (lambda state: None)

    def request(self, endpoint, params, name):
        encoded = urllib.parse.urlencode(params).encode('ascii')
        for attempt in range(self.retries + 1):
            path = None
            self.sleep(self.delay if attempt == 0 else max(self.delay, 2 ** attempt))
            entry = {'timestamp_utc': now(), 'endpoint': BASE + endpoint,
                     'method': 'POST', 'parameters': params, 'attempt': attempt + 1,
                     'batch': name}
            started = time.monotonic()
            retry = False
            log_event(self.out / 'requests.jsonl', dict(entry, event='ATTEMPT_STARTED'))
            try:
                self.observer('REQUEST_ATTEMPTED')
                req = urllib.request.Request(BASE + endpoint, data=encoded,
                    headers={'User-Agent': 'PulmonaryFibrosisSR/2',
                             'Content-Type': 'application/x-www-form-urlencoded', 'Accept-Encoding': 'identity'})
                try:
                    response = self.opener(req, timeout=self.timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response as r:
                    entry['http_status'] = r.code
                    entry['headers'] = dict(r.headers)
                    self.observer('HTTP_RESPONSE_RECEIVED')
                    path = self.out / (name + '.attempt' + str(attempt + 1) + '.raw')
                    with path.open('xb') as f:
                        try:
                            while True:
                                chunk = r.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                                f.flush()
                        except http.client.IncompleteRead as exc:
                            f.write(exc.partial)
                            raise
                        finally:
                            f.flush()
                            os.fsync(f.fileno())
                    body = path.read_bytes()
                    entry.update(file=path.name, bytes=path.stat().st_size, sha256=sha(path.read_bytes()))
                    lengths = [v for k,v in r.headers.items() if k.lower() == 'content-length']
                    enc = next((v for k,v in r.headers.items() if k.lower() == 'content-encoding'), 'identity')
                    if enc.lower() != 'identity':
                        raise ValueError('Unsupported content encoding; raw evidence retained')
                    if lengths and (len(lengths) != 1 or int(lengths[0]) != len(body)):
                        raise ValueError('Content-Length mismatch')
                    entry['transport_complete'] = True
                if path.read_bytes() != body:
                    raise ValueError('Persistence mismatch')
                entry.update(file=path.name, bytes=len(body), sha256=sha(body))
                status = entry['http_status']
                retry = status == 429 or 500 <= status < 600
                if status != 200:
                    raise ValueError('HTTP ' + str(status))
                return body
            except (OSError, ValueError, http.client.HTTPException, KeyboardInterrupt) as exc:
                entry['error'] = str(exc)
                if path is not None and path.exists():
                    entry.update(file=path.name, bytes=path.stat().st_size, sha256=sha(path.read_bytes()))
                if isinstance(exc, http.client.HTTPException):
                    retry = True
                if 'http_status' not in entry:
                    retry = isinstance(exc, OSError)
                if not retry or attempt == self.retries:
                    raise
            finally:
                entry['elapsed_seconds'] = time.monotonic() - started
                self.attempts.append(entry)
                # Append-only evidence survives later failures.
                log_event(self.out / 'requests.jsonl', dict(entry, event='ATTEMPT_FINISHED'))

def parse_search(body):
    root = ET.fromstring(body)
    if root.find('.//ERROR') is not None or root.find('.//ErrorList') is not None:
        raise ValueError('ESearch server error')
    warnings = [x.text or '' for x in root.findall('.//WarningList/OutputMessage')]
    if any('wrong subquery' in message.lower() for message in warnings):
        raise ValueError('ESearch History error: ' + '; '.join(warnings))
    if root.tag != 'eSearchResult' or int(root.findtext('Count')) < 0:
        raise ValueError('Invalid ESearch result')
    for tag in ('RetStart', 'RetMax', 'IdList', 'QueryTranslation'):
        if root.find(tag) is None:
            raise ValueError('Missing ESearch field: ' + tag)
    if not (root.findtext('QueryTranslation') or '').strip():
        raise ValueError('Empty QueryTranslation')
    if int(root.findtext('RetStart')) < 0 or int(root.findtext('RetMax')) < 0:
        raise ValueError('Negative pagination boundary')
    return root, int(root.findtext('Count'))

def check_page(root, count, expected_count, start, size, webenv=None, key=None):
    if count != expected_count or int(root.findtext('RetStart')) != start or int(root.findtext('RetMax')) != size:
        raise ValueError('ESearch Count or returned page boundaries mismatch')
    actual_env = root.findtext('WebEnv')
    if actual_env is not None and actual_env != webenv:
        raise ValueError('History context changed: WebEnv')
    # usehistory=y posts a result set. Its output key need not be the source key.
    # Only the INITIAL source key is used in requests; the output key is audited.
    output_key = root.findtext('QueryKey')
    if output_key is not None and not re.fullmatch(r'[1-9][0-9]*', output_key):
        raise ValueError('Invalid output History QueryKey')
    if root.find('.//WarningList') is not None:
        raise ValueError('History page contains server warnings')
    page = [x.text for x in root.findall('IdList/Id')]
    if len(page) != size or any(not x or not x.isdigit() for x in page):
        raise ValueError('Invalid PMID page')
    return page

def parse_medline(body):
    """Reject ID-only payloads, embedded HTML, malformed fields and truncated records."""
    text = body.decode('utf-8', 'strict')
    if not text.endswith('\n'):
        raise ValueError('MEDLINE missing terminal newline; possible truncation')
    records = re.split(r'\r?\n[ \t]*\r?\n', text.strip())
    result = []
    for record in records:
        fields = {}
        previous = None
        for line in record.splitlines():
            m = re.fullmatch(r'([A-Z][A-Z0-9]{1,3})[ \t]*-[ \t]+(.+)', line)
            if m:
                fields.setdefault(m[1], []).append(m[2].strip())
                previous = m[1]
            elif not line.startswith('      ') or previous is None:
                raise ValueError('Malformed MEDLINE line')
        ids = fields.get('PMID', [])
        if len(ids) != 1 or not ids[0].isdigit() or not fields.get('TI') or not fields.get('DP'):
            raise ValueError('Missing MEDLINE PMID/TI/DP or multiple PMID fields')
        result.extend(ids)
    return result

def execute(query_file, parent, batch_size=200, client_factory=Client, simulation=False, **options):
    query = load_query(query_file)  # Reject before creating any run identity.
    run_id = ('SIMULATION-' if simulation else 'SR-PUBMED-002-') + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
    out = Path(parent) / run_id
    out.mkdir(parents=True, exist_ok=False)
    metadata = {'run_id': run_id, 'simulation': simulation, 'execution_started': True, 'ncbi_accessed': False,
        'request_state': 'NOT_ATTEMPTED',
        'query_sha256': EXPECTED, 'timestamp_utc': now(), 'python': platform.python_version(),
        'runner': platform.platform(), 'github_run_id': os.getenv('GITHUB_RUN_ID'),
        'github_run_attempt': os.getenv('GITHUB_RUN_ATTEMPT'), 'commit_sha': os.getenv('GITHUB_SHA')}
    metadata['github_repository'] = os.getenv('GITHUB_REPOSITORY')
    write_json(out / 'run_metadata_initial.json', metadata)
    (out / 'frozen_source.txt').write_bytes(Path(query_file).read_bytes())
    states = ['NOT_ATTEMPTED','REQUEST_ATTEMPTED','HTTP_RESPONSE_RECEIVED','VALID_SEARCH_RESULT']
    def observe(state):
        if states.index(state) > states.index(metadata['request_state']):
            metadata['request_state'] = state
        metadata['ncbi_accessed'] = metadata['request_state'] in states[2:] and not simulation
        # Immutable snapshots; final run_metadata.json is created only once.
        write_json(out / ('metadata_event_' + str(len(list(out.glob('metadata_event_*')))) + '.json'), dict(metadata, event_timestamp_utc=now()))
    client = client_factory(out, observer=observe, **options)
    report = {'status': 'FAILED', 'web_evidence_status': 'PENDING_WEB_EVIDENCE',
              'web_search_details': 'PENDING_WEB_EVIDENCE',
              'web_search_history': 'PENDING_WEB_EVIDENCE', 'batches': [], 'history_pages': [], 'errors': []}
    ids, fetched = [], []
    try:
        raw = client.request('esearch.fcgi', {'db': 'pubmed', 'term': query,
            'retmax': 0, 'usehistory': 'y', 'retmode': 'xml'}, 'esearch_initial')
        root, count = parse_search(raw)
        if int(root.findtext('RetStart')) != 0 or int(root.findtext('RetMax')) != 0 or root.findall('IdList/Id'):
            raise ValueError('Invalid initial retmax=0 response')
        if not root.findtext('WebEnv') or not (root.findtext('QueryKey') or '').isdigit():
            raise ValueError('Invalid initial History identity, including zero results')
        observe('VALID_SEARCH_RESULT')
        report['status'] = 'INCOMPLETE'
        report.update(count=count, query_translation=root.findtext('QueryTranslation'),
                      actual_search_timestamp_utc=now())
        # PubMed ESearch cannot return more than 9999 IDs: fail closed above that limit.
        if count > 9999:
            raise ValueError('Count exceeds validated PubMed 9999-ID retrieval limit')
        webenv, key = root.findtext('WebEnv'), root.findtext('QueryKey')
        if count and (not webenv or not key):
            raise ValueError('Missing History Server identity')
        ids = []
        for start in range(0, count, batch_size):
            root, n = parse_search(client.request('esearch.fcgi', {'db': 'pubmed',
                'term': '#' + key, 'WebEnv': webenv, 'usehistory': 'y', 'retstart': start,
                'retmax': min(batch_size, count-start), 'retmode': 'xml'}, 'esearch_' + str(start)))
            page = check_page(root,n,count,start,min(batch_size,count-start),webenv,key)
            report['history_pages'].append({'source_webenv':webenv,'source_query_key':key,
                'response_webenv':root.findtext('WebEnv'),'response_query_key':root.findtext('QueryKey'),
                'retstart':start,'retmax':len(page),'count':n,'pmids':page})
            if set(ids).intersection(page):
                raise ValueError('Overlapping pages')
            ids.extend(page)
        write_json(out / 'pmids_ordered.json', ids)
        if len(ids) != count or len(set(ids)) != count or any(not x or not x.isdigit() for x in ids):
            raise ValueError('PMID count, uniqueness, or syntax mismatch')
        fetched = []
        for start in range(0, count, batch_size):
            batch = ids[start:start+batch_size]
            body = client.request('efetch.fcgi', {'db': 'pubmed', 'id': ','.join(batch),
                'rettype': 'medline', 'retmode': 'text'}, 'efetch_' + str(start))
            actual = parse_medline(body)
            report['batches'].append({'start': start, 'requested': batch, 'received': actual})
            fetched.extend(actual)
            if len(actual) != len(batch) or set(actual) != set(batch):
                raise ValueError('EFetch batch missing, duplicate, or unexpected PMID')
        if len(fetched) != count or set(fetched) != set(ids) or len(set(fetched)) != count:
            raise ValueError('Global EFetch completeness mismatch')
        report.update(status='API_RETRIEVAL_COMPLETE', fetched_count=len(fetched))
        report['content_integrity'] = 'TRANSPORT_AND_FORMAT_CHECKED; SEMANTIC_TRUNCATION_NOT_EXCLUDED'
    except (Exception, KeyboardInterrupt) as exc:
        report['errors'].append(str(exc))
    finally:
        try:
            write_json(out / 'run_metadata.json', dict(metadata, completed_utc=now()))
        except OSError as exc:
            report['status'] = 'FAILED'
            report['errors'].append('Metadata finalization: ' + str(exc))
        if not (out / 'pmids_ordered.json').exists():
            try:
                write_json(out / 'pmids_partial.json', ids)
            except OSError as exc:
                report['status'] = 'FAILED'
                report['errors'].append('Partial PMID persistence: ' + str(exc))
        report['reconciliation'] = {'requested_count': len(ids), 'fetched_count': len(fetched),
            'missing': sorted(set(ids)-set(fetched)), 'unexpected': sorted(set(fetched)-set(ids)),
            'duplicate_fetched_count': len(fetched)-len(set(fetched))}
        report.update(run_id=run_id, ncbi_accessed=metadata['ncbi_accessed'], completed_utc=now())
        report.update({k: metadata[k] for k in ('simulation','github_run_id','github_run_attempt','commit_sha','github_repository')})
        try:
            write_json(out / 'final_report.json', report)
            manifest = [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}
                        for p in sorted(out.iterdir()) if p.is_file()]
            write_json(out / 'sha256_manifest.json', {'base': '.', 'files': manifest})
        except OSError as exc:
            report['status'] = 'FAILED'
            report['errors'].append('Evidence finalization: ' + str(exc))
            try:
                write_json(out / ('finalization_failure_' + uuid.uuid4().hex + '.json'), report)
            except OSError:
                print('Evidence storage unavailable: ' + str(exc), file=sys.stderr)
    return out, report

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--query-file', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--confirm', required=True)
    p.add_argument('--batch-size', type=int, default=200)
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--delay', type=float, default=0.4)
    a = p.parse_args()
    if a.confirm != CONFIRM or not 1 <= a.batch_size <= 500 or a.delay < 0.34 or not 0 < a.timeout <= 120 or not 0 <= a.retries <= 4:
        p.error('Invalid confirmation or bounded request settings')
    _, report = execute(a.query_file, a.output_dir, a.batch_size,
                        timeout=a.timeout, retries=a.retries, delay=a.delay)
    print(json.dumps(report))
    raise SystemExit(0 if report['status'] == 'API_RETRIEVAL_COMPLETE' else 1)

if __name__ == '__main__':
    main()
