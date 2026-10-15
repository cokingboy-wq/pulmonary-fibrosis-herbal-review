import importlib.util
import json
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch
from test_sr_pubmed_002_execute import m,QUERY,search

spec=importlib.util.spec_from_file_location('independent',QUERY.parents[1]/'scripts/verify_sr_pubmed_002_artifact.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

class Response:
    code=200
    def __init__(self,body,headers=None):self.body=body;self.headers=headers or {'Content-Length':str(len(body))}
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,size=-1):
        b=self.body[:size] if size>=0 else self.body;self.body=self.body[len(b):];return b

class ArtifactTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.parent=Path(self.temp.name)
    def build(self,zero=False,fail=False):
        values=([search(count=0)] if zero else [search(),search('1,2'),search('3'),b'PMID- 1\nTI  - One\nDP  - 2020\n\nPMID- 2\nTI  - Two\nDP  - 2021\n',b'PMID- 3\nTI  - Three\nDP  - 2022\n'])
        iterator=iter(values)
        def opener(*a,**kw):
            if fail:raise TimeoutError('mock timeout')
            return Response(next(iterator))
        return m.execute(QUERY,self.parent,2,simulation=True,opener=opener,sleep=lambda x:None,retries=0)[0]
    def zip(self,out):
        path=self.parent/'evidence.zip'
        with zipfile.ZipFile(path,'x') as z:
            for p in out.iterdir():z.write(p,p.name)
        return path
    def rehash(self,out):
        rows=[{'path':p.name,'bytes':p.stat().st_size,'sha256':m.sha(p.read_bytes())} for p in out.iterdir() if p.name!='sha256_manifest.json']
        (out/'sha256_manifest.json').write_text(json.dumps({'files':rows}))
    def test_verified_full(self):
        out=self.build();self.assertEqual(v.verify_zip(self.zip(out),self.parent/'download')['status'],'VERIFIED_API_RETRIEVAL')
    def test_valid_zero(self):self.assertEqual(v.verify_directory(self.build(zero=True))['status'],'VERIFIED_API_RETRIEVAL')
    def test_verified_failure(self):self.assertEqual(v.verify_directory(self.build(fail=True))['status'],'VERIFIED_FAILURE')
    def test_missing_raw(self):
        out=self.build();(out/'efetch_0.attempt1.raw').unlink();self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_tampered_raw(self):
        out=self.build();(out/'efetch_0.attempt1.raw').write_bytes(b'bad');self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_forged_report_rehashed(self):
        out=self.build();p=out/'final_report.json';r=json.loads(p.read_bytes());r['count']=10;p.write_text(json.dumps(r));self.rehash(out)
        self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_missing_batch_rehashed(self):
        out=self.build();(out/'efetch_2.attempt1.raw').unlink();self.rehash(out);self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_saved_order_changed(self):
        out=self.build();(out/'pmids_ordered.json').write_text('[3,2,1]');self.rehash(out);self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_path_traversal(self):
        p=self.parent/'bad.zip'
        with zipfile.ZipFile(p,'x') as z:z.writestr('../escape','bad')
        self.assertEqual(v.verify_zip(p,self.parent/'target')['status'],'INVALID_EVIDENCE');self.assertFalse((self.parent/'target').exists())
    def test_existing_destination(self):
        out=self.build();p=self.zip(out);self.assertEqual(v.verify_zip(p,self.parent)['status'],'INVALID_EVIDENCE')
    def test_length_mismatch(self):
        c=m.Client(self.parent,opener=lambda *a,**k:Response(b'abc',{'Content-Length':'4'}),sleep=lambda x:None,retries=0)
        with self.assertRaises(ValueError):c.request('efetch.fcgi',{'db':'pubmed'},'length')
        self.assertEqual((self.parent/'length.attempt1.raw').read_bytes(),b'abc')
    def test_incremental_network_failure(self):
        class Broken(Response):
            def read(self,size=-1):
                if self.body:return super().read(size)
                raise TimeoutError('after prefix')
        c=m.Client(self.parent,opener=lambda *a,**k:Broken(b'prefix'),sleep=lambda x:None,retries=0)
        with self.assertRaises(TimeoutError):c.request('efetch.fcgi',{'db':'pubmed'},'partial')
        self.assertEqual((self.parent/'partial.attempt1.raw').read_bytes(),b'prefix')
    def test_atomic_no_overwrite(self):
        p=self.parent/'audit.json';m.write_json(p,{'old':1})
        with self.assertRaises(FileExistsError):m.write_json(p,{'new':2})
        self.assertEqual(json.loads(p.read_text()),{'old':1})
    def test_page_without_optional_history(self):
        body=search('1,2').replace(b'<WebEnv>w</WebEnv>',b'').replace(b'<QueryKey>1</QueryKey>',b'')
        r,n=m.parse_search(body);self.assertEqual(m.check_page(r,n,3,0,2,'w','1'),['1','2'])
    def test_invalid_zero(self):
        with self.assertRaises(ValueError):m.parse_search(b'<eSearchResult><Count>0</Count></eSearchResult>')
    def test_initial_error(self):
        with self.assertRaises(ValueError):m.parse_search(search(count=0).replace(b'</eSearchResult>',b'<ERROR>bad</ERROR></eSearchResult>'))
    def test_filesystem_observer_failure(self):
        c=m.Client(self.parent,observer=lambda s:(_ for _ in ()).throw(OSError('simulated disk error')),sleep=lambda x:None,retries=0)
        with self.assertRaises(OSError):c.request('efetch.fcgi',{'db':'pubmed'},'disk')
        events=[json.loads(x) for x in (self.parent/'requests.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['event'],'ATTEMPT_FINISHED');self.assertIn('error',events[-1])
    def test_verified_incomplete(self):
        values=iter([search(),search('1')])
        out,r=m.execute(QUERY,self.parent,2,simulation=True,opener=lambda *a,**k:Response(next(values)),sleep=lambda x:None,retries=0)
        self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(v.verify_directory(out)['status'],'VERIFIED_INCOMPLETE')
    def test_final_manifest_failure_preserved(self):
        original=m.write_json
        def writer(p,value):
            if Path(p).name=='sha256_manifest.json':raise OSError('simulated manifest disk failure')
            return original(p,value)
        with patch.object(m,'write_json',side_effect=writer):out=self.build(zero=True)
        self.assertTrue(list(out.glob('finalization_failure_*.json')))
        self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_reserved_windows_path(self):
        p=self.parent/'reserved.zip'
        with zipfile.ZipFile(p,'x') as z:z.writestr('CON.txt','bad')
        self.assertEqual(v.verify_zip(p,self.parent/'target')['status'],'INVALID_EVIDENCE')
    def test_zip_symlink(self):
        import stat
        p=self.parent/'link.zip';e=zipfile.ZipInfo('link');e.create_system=3;e.external_attr=(stat.S_IFLNK|0o777)<<16
        with zipfile.ZipFile(p,'x') as z:z.writestr(e,'../outside')
        self.assertEqual(v.verify_zip(p,self.parent/'target')['status'],'INVALID_EVIDENCE')
    def test_no_content_length_valid(self):
        c=m.Client(self.parent,opener=lambda *a,**k:Response(b'abc',{'X-Test':'offline'}),sleep=lambda x:None)
        self.assertEqual(c.request('efetch.fcgi',{'db':'pubmed'},'chunked'),b'abc')
    def test_encoding_rejected(self):
        c=m.Client(self.parent,opener=lambda *a,**k:Response(b'abc',{'Content-Encoding':'gzip'}),sleep=lambda x:None,retries=0)
        with self.assertRaises(ValueError):c.request('efetch.fcgi',{'db':'pubmed'},'encoding')

if __name__=='__main__':unittest.main()
