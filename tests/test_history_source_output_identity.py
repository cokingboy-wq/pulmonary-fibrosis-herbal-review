"""History source keys stay immutable even when posted result keys change."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_pubmed_artifact_acceptance as helpers
from test_sr_pubmed_002_execute import m,QUERY,search

class SourceIdentityTests(unittest.TestCase):
    setUp=helpers.ArtifactTests.setUp
    build=helpers.ArtifactTests.build
    rehash=helpers.ArtifactTests.rehash
    def page(self,body=None,count=3,start=0,size=2):
        root,n=m.parse_search(body or search('1,2'))
        return m.check_page(root,n,count,start,size,'w','1')
    def test_original_key(self):self.assertEqual(self.page(),['1','2'])
    def test_new_output_key(self):self.assertEqual(self.page(search('1,2').replace(b'<QueryKey>1',b'<QueryKey>2')),['1','2'])
    def test_output_key_absent(self):self.assertEqual(self.page(search('1,2').replace(b'<QueryKey>1</QueryKey>',b'')),['1','2'])
    def test_conflicting_environment(self):
        with self.assertRaises(ValueError):self.page(search('1,2').replace(b'<WebEnv>w',b'<WebEnv>wrong'))
    def test_count_change(self):
        with self.assertRaises(ValueError):self.page(search('1,2',4))
    def test_bad_boundary(self):
        with self.assertRaises(ValueError):self.page(search('1,2').replace(b'<RetStart>0',b'<RetStart>1'))
    def test_invalid_key(self):
        with self.assertRaises(ValueError):self.page(search('1,2').replace(b'<QueryKey>1',b'<QueryKey>0'))
    def test_missing_page_id(self):
        with self.assertRaises(ValueError):self.page(search('1'))
    def test_server_warning(self):
        body=search('1,2').replace(b'</eSearchResult>',b'<WarningList><OutputMessage>History expired</OutputMessage></WarningList></eSearchResult>')
        with self.assertRaises(ValueError):self.page(body)
    def test_later_request_never_uses_new_output_key(self):
        calls=[];responses=iter([search(),search('1,2').replace(b'<QueryKey>1',b'<QueryKey>2'),search('3').replace(b'<QueryKey>1',b'<QueryKey>3'),b'PMID- 1\nTI  - One\nDP  - 2020\n\nPMID- 2\nTI  - Two\nDP  - 2020\n',b'PMID- 3\nTI  - Three\nDP  - 2020\n'])
        class Fake:
            def __init__(self,out,**kw):pass
            def request(self,ep,p,name):calls.append((ep,p,name));return next(responses)
        out,r=m.execute(QUERY,self.parent,2,simulation=True,client_factory=Fake)
        self.assertEqual(r['status'],'API_RETRIEVAL_COMPLETE')
        pages=[p for ep,p,n in calls if n in ('esearch_0','esearch_2')]
        self.assertEqual([p['term'] for p in pages],['#1','#1'])
        self.assertTrue(all(p['WebEnv']=='w' and p['usehistory']=='y' for p in pages))
        self.assertEqual([p['response_query_key'] for p in r['history_pages']],['2','3'])
    def test_repeated_ids_incomplete(self):
        responses=iter([search(),search('1,2'),search('3').replace(b'<Id>3</Id>',b'<Id>1</Id>')])
        class Fake:
            def __init__(self,out,**kw):pass
            def request(self,*a):return next(responses)
        _,r=m.execute(QUERY,self.parent,2,simulation=True,client_factory=Fake)
        self.assertEqual(r['status'],'INCOMPLETE')
    def new_key_artifact(self):
        original=helpers.search
        def changed(ids='',count=3):
            body=original(ids,count)
            return body.replace(b'<QueryKey>1',b'<QueryKey>2') if ids else body
        with patch.object(helpers,'search',side_effect=changed):return self.build()
    def test_independent_acceptance_new_output_key(self):
        out=self.new_key_artifact();self.assertEqual(helpers.v.verify_directory(out)['status'],'SIMULATION_ONLY')
    def test_independent_rejects_wrong_request_source(self):
        out=self.new_key_artifact();p=out/'requests.jsonl';events=[json.loads(x) for x in p.read_text().splitlines()]
        for e in events:
            if e['batch']=='esearch_2':e['parameters']['term']='#2'
        p.write_text('\n'.join(json.dumps(e) for e in events)+'\n');self.rehash(out)
        self.assertEqual(helpers.v.verify_directory(out)['status'],'INVALID_EVIDENCE')

if __name__=='__main__':unittest.main()
