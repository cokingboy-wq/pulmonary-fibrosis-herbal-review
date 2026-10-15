"""Explicitly gated PubMed retrieval; importing this module never uses the network."""
import argparse
import hashlib
import json
import os
import platform
import re
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
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)

class Client:
    def __init__(self, out, timeout=30, retries=2, delay=0.4, opener=None, sleep=time.sleep):
        self.out, self.timeout, self.retries = Path(out), timeout, retries
        self.delay, self.opener, self.sleep = delay, opener or urllib.request.urlopen, sleep
        self.attempts = []

    def request(self, endpoint, params, name):
        encoded = urllib.parse.urlencode(params).encode('ascii')
        for attempt in range(self.retries + 1):
            self.sleep(self.delay if attempt == 0 else max(self.delay, 2 ** attempt))
            entry = {'timestamp_utc': now(), 'endpoint': BASE + endpoint,
                     'method': 'POST', 'parameters': params, 'attempt': attempt + 1,
                     'batch': name}
            started = time.monotonic()
            retry = False
            try:
                req = urllib.request.Request(BASE + endpoint, data=encoded,
                    headers={'User-Agent': 'PulmonaryFibrosisSR/2',
                             'Content-Type': 'application/x-www-form-urlencoded'})
                try:
                    response = self.opener(req, timeout=self.timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response as r:
                    entry['http_status'] = r.code
                    entry['headers'] = dict(r.headers)
                    body = r.read()
                path = self.out / (name + '.attempt' + str(attempt + 1) + '.raw')
                with path.open('xb') as f:
                    f.write(body)
                if path.read_bytes() != body:
                    raise ValueError('Persistence mismatch')
                entry.update(file=path.name, bytes=len(body), sha256=sha(body))
                status = entry['http_status']
                retry = status == 429 or 500 <= status < 600
                if status != 200:
                    raise ValueError('HTTP ' + str(status))
                return body
            except (OSError, ValueError) as exc:
                entry['error'] = str(exc)
                if 'http_status' not in entry:
                    retry = isinstance(exc, OSError)
                if not retry or attempt == self.retries:
                    raise
            finally:
                entry['elapsed_seconds'] = time.monotonic() - started
                self.attempts.append(entry)
                # Append-only evidence survives later failures.
                with (self.out / 'requests.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps(entry) + '\n')

def parse_search(body):
    root = ET.fromstring(body)
    if root.find('ERROR') is not None or root.find('ErrorList') is not None:
        raise ValueError('ESearch server error')
    return root, int(root.findtext('Count'))

def execute(query_file, parent, batch_size=200, client_factory=Client, **options):
    query = load_query(query_file)  # Reject before creating any run identity.
    run_id = 'SR-PUBMED-002-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
    out = Path(parent) / run_id
    out.mkdir(parents=True, exist_ok=False)
    metadata = {'run_id': run_id, 'execution_started': True, 'ncbi_accessed': False,
        'query_sha256': EXPECTED, 'timestamp_utc': now(), 'python': platform.python_version(),
        'runner': platform.platform(), 'github_run_id': os.getenv('GITHUB_RUN_ID'),
        'github_run_attempt': os.getenv('GITHUB_RUN_ATTEMPT'), 'commit_sha': os.getenv('GITHUB_SHA')}
    write_json(out / 'run_metadata.json', metadata)
    (out / 'frozen_source.txt').write_bytes(Path(query_file).read_bytes())
    client = client_factory(out, **options)
    report = {'status': 'FAIL', 'web_search_details': 'PENDING_EVIDENCE',
              'web_search_history': 'PENDING_EVIDENCE', 'errors': []}
    try:
        metadata['ncbi_accessed'] = True
        raw = client.request('esearch.fcgi', {'db': 'pubmed', 'term': query,
            'retmax': 0, 'usehistory': 'y', 'retmode': 'xml'}, 'esearch_initial')
        root, count = parse_search(raw)
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
                'term': '#' + key, 'WebEnv': webenv, 'retstart': start,
                'retmax': min(batch_size, count-start), 'retmode': 'xml'}, 'esearch_' + str(start)))
            page = [x.text for x in root.findall('IdList/Id')]
            if n != count or len(page) != min(batch_size, count-start):
                raise ValueError('ESearch page incomplete or Count changed')
            ids.extend(page)
        write_json(out / 'pmids_ordered.json', ids)
        if len(ids) != count or len(set(ids)) != count or any(not x or not x.isdigit() for x in ids):
            raise ValueError('PMID count, uniqueness, or syntax mismatch')
        fetched = []
        for start in range(0, count, batch_size):
            batch = ids[start:start+batch_size]
            body = client.request('efetch.fcgi', {'db': 'pubmed', 'id': ','.join(batch),
                'rettype': 'medline', 'retmode': 'text'}, 'efetch_' + str(start))
            actual = re.findall(rb'(?m)^PMID[ \t]*-[ \t]+([0-9]+)[ \t]*\r?$', body)
            actual = [x.decode('ascii') for x in actual]
            fetched.extend(actual)
            if len(actual) != len(batch) or set(actual) != set(batch):
                raise ValueError('EFetch batch missing, duplicate, or unexpected PMID')
        if len(fetched) != count or set(fetched) != set(ids) or len(set(fetched)) != count:
            raise ValueError('Global EFetch completeness mismatch')
        report.update(status='API_RETRIEVAL_COMPLETE', fetched_count=len(fetched))
    except Exception as exc:
        report['errors'].append(str(exc))
    finally:
        report.update(run_id=run_id, ncbi_accessed=metadata['ncbi_accessed'], completed_utc=now())
        write_json(out / 'final_report.json', report)
        manifest = [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}
                    for p in sorted(out.iterdir()) if p.is_file()]
        write_json(out / 'sha256_manifest.json', {'base': '.', 'files': manifest})
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
