"""Offline malicious-fixture tests; identity strings are never actual run registrations."""
import contextlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import test_pubmed_artifact_acceptance as helpers
m=helpers.m
v=helpers.v

class NewTests(unittest.TestCase):
    setUp=helpers.ArtifactTests.setUp
    build=helpers.ArtifactTests.build
    zip=helpers.ArtifactTests.zip
    rehash=helpers.ArtifactTests.rehash

    def identity_fixture(self):
        out=self.build(zero=True)
        for path in [out/'run_metadata_initial.json',out/'run_metadata.json',out/'final_report.json']+list(out.glob('metadata_event_*.json')):
            doc=json.loads(path.read_bytes());doc.update(simulation=False,run_id='SR-PUBMED-002-20000101T000000Z-000000000000',
                github_run_id='123',github_run_attempt='1',commit_sha='a'*40,github_repository='cokingboy-wq/pulmonary-fibrosis-herbal-review')
            path.write_text(json.dumps(doc))
        self.rehash(out)
        return out

    def provenance(self,z):
        return {'repository':'cokingboy-wq/pulmonary-fibrosis-herbal-review','run_id':'123','run_attempt':'1',
            'commit_sha':'a'*40,'artifact_id':'456','artifact_name':'SR-PUBMED-002-123-1',
            'zip_sha256':v.digest(z.read_bytes()),'source_url':'https://api.github.com/repos/cokingboy-wq/pulmonary-fibrosis-herbal-review/actions/artifacts/456',
            'artifact_run_id':'123','artifact_commit_sha':'a'*40}

    def test_simulation_never_formal(self):
        out=self.build();z=self.zip(out)
        self.assertEqual(v.verify_zip(z,self.parent/'download',self.provenance(z))['status'],'SIMULATION_ONLY')
    def test_missing_simulation(self):
        out=self.build();p=out/'run_metadata.json';d=json.loads(p.read_bytes());del d['simulation'];p.write_text(json.dumps(d));self.rehash(out)
        self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_simulation_impersonates_run(self):
        out=self.identity_fixture();p=out/'run_metadata.json';d=json.loads(p.read_bytes());d['simulation']=True;p.write_text(json.dumps(d));self.rehash(out)
        self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def check_source_mismatch(self,key,value):
        out=self.identity_fixture();z=self.zip(out);proof=self.provenance(z);proof[key]=value
        if key in ('run_id','run_attempt'):proof['artifact_name']='SR-PUBMED-002-'+proof['run_id']+'-'+proof['run_attempt']
        r=v.verify_zip(z,self.parent/'download',proof)
        self.assertEqual(r['status'],'INVALID_EVIDENCE');self.assertEqual(r['internal_consistency'],'PASS');self.assertEqual(r['external_authenticity'],'FAILED')
    def test_github_run_mismatch(self):self.check_source_mismatch('run_id','999')
    def test_attempt_mismatch(self):self.check_source_mismatch('run_attempt','2')
    def test_commit_mismatch(self):self.check_source_mismatch('commit_sha','b'*40)
    def test_artifact_source_mismatch(self):self.check_source_mismatch('source_url','https://example.invalid/artifact')
    def test_missing_external_digest(self):self.check_source_mismatch('zip_sha256','')
    def test_digest_mismatch(self):self.check_source_mismatch('zip_sha256','0'*64)
    def test_artifact_name_mismatch(self):self.check_source_mismatch('artifact_name','another-artifact')
    def test_artifact_run_association_mismatch(self):self.check_source_mismatch('artifact_run_id','999')
    def test_internal_pass_origin_missing(self):
        out=self.identity_fixture();r=v.verify_zip(self.zip(out),self.parent/'download')
        self.assertEqual(r['status'],'INVALID_EVIDENCE');self.assertEqual(r['internal_consistency'],'PASS')
    def test_origin_comparison_positive_offline_fixture(self):
        out=self.identity_fixture();z=self.zip(out)
        r=v.verify_zip(z,self.parent/'download',self.provenance(z))
        self.assertEqual(r['status'],'VERIFIED_API_RETRIEVAL')
        self.assertEqual(r['origin']['local_zip_sha256'],r['origin']['github_zip_sha256'])
    def test_initial_identity_mismatch(self):
        out=self.identity_fixture();p=out/'run_metadata_initial.json';d=json.loads(p.read_bytes());d['commit_sha']='b'*40;p.write_text(json.dumps(d));self.rehash(out)
        z=self.zip(out);self.assertEqual(v.verify_zip(z,self.parent/'download',self.provenance(z))['status'],'INVALID_EVIDENCE')
    def test_multiple_candidates(self):
        one=self.build(zero=True);two=self.build(zero=True);z=self.parent/'multi.zip'
        with zipfile.ZipFile(z,'x') as archive:
            for i,out in enumerate([one,two]):
                for path in out.iterdir():archive.write(path,str(i)+'/'+path.name)
        self.assertEqual(v.verify_zip(z,self.parent/'download')['status'],'INVALID_EVIDENCE')

    def finalization_fault(self,names,backup=False):
        original=m.write_json;stderr=io.StringIO();reports=[]
        def writer(path,value):
            if Path(path).name in names or (backup and Path(path).name.startswith('finalization_failure_')):
                raise OSError('injected original storage failure: '+Path(path).name)
            return original(path,value)
        real_execute=m.execute
        def execute(*a,**kw):
            out,r=real_execute(*a,**kw);reports.append(r);return out,r
        with patch.object(m,'write_json',side_effect=writer),patch.object(m,'execute',side_effect=execute),contextlib.redirect_stderr(stderr):
            out=self.build(zero=True)
        self.assertEqual(reports[0]['status'],'FAILED')
        self.assertTrue(any('injected original storage failure' in error for error in reports[0]['errors']))
        self.assertNotIn('NameError',stderr.getvalue())
        if backup:self.assertIn('Evidence storage unavailable',stderr.getvalue())
        self.assertEqual(v.verify_directory(out)['status'],'INVALID_EVIDENCE')
    def test_run_metadata_write_failure(self):self.finalization_fault({'run_metadata.json'})
    def test_final_report_write_failure(self):self.finalization_fault({'final_report.json'})
    def test_manifest_write_failure(self):self.finalization_fault({'sha256_manifest.json'})
    def test_backup_report_also_fails(self):self.finalization_fault({'sha256_manifest.json'},backup=True)
    def test_all_finalization_storage_fails(self):self.finalization_fault({'run_metadata.json','final_report.json','sha256_manifest.json'},backup=True)

if __name__=='__main__':unittest.main()
