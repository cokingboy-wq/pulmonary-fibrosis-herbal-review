import importlib.util, pathlib
p=pathlib.Path(__file__).with_name('env_ncbi_003.py'); spec=importlib.util.spec_from_file_location('m',p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
xml=b'<eInfoResult><DbInfo><DbName>pubmed</DbName></DbInfo></eInfoResult>'
base='PMID- 31452104\nTI  - Example title\nAU  - Author\nJT  - Journal\nDP  - 2020\nMH  - Fibrosis\n'
def v(status=200,x=xml,med=base): return m.validate(status,x,200,med.encode())
assert m.parse_medline(base)['PMID']==['31452104']; assert m.parse_medline(base.replace('PMID-','PMID  -'))['PMID']==['31452104']
assert not v(med=base.replace('PMID- 31452104\n',''))[2]; assert not v(med=base.replace('31452104','999'))[2]
for field in ['TI','AU','JT','DP','MH']: assert not all(v(med='\\n'.join(line for line in base.splitlines(True) if not line.startswith(field+' ')))[1].values())
assert not v(status=500)[0]; assert not v(x=b'bad')[0]; assert v()[2]
real=pathlib.Path('D:/Pulmonary_Fibrosis_SR/04_Search_Audit/ENV-NCBI-003/run_38066988929/env_ncbi_test_medline.txt').read_bytes(); assert m.validate(200,xml,200,real)[2]
print('9 regression assertions passed')

