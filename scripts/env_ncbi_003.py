import hashlib,json,os,platform,sys,time,urllib.request,urllib.parse,xml.etree.ElementTree as ET
from datetime import datetime,timezone
OUT=os.getcwd(); attempts=[]
def fetch(url,path):
    for i in range(3):
        t=time.time()
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ENV-NCBI-003"})
            with urllib.request.urlopen(req,timeout=20) as r:
                b=r.read(); status=r.status; headers=dict(r.headers)
            open(path,"wb").write(b); attempts.append({"url":url,"attempt":i+1,"status":status,"elapsed":time.time()-t})
            return status,b,headers
        except Exception as e:
            attempts.append({"url":url,"attempt":i+1,"error":str(e),"elapsed":time.time()-t}); time.sleep(2**i)
    return None,b"",{}
now=datetime.now(timezone.utc).isoformat(); base="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
s,b,h=fetch(base+"einfo.fcgi?db=pubmed","env_ncbi_einfo_response.xml")
xml_ok=False
try: ET.fromstring(b); xml_ok=True
except Exception: pass
u=base+"efetch.fcgi?"+urllib.parse.urlencode({"db":"pubmed","id":"31452104","rettype":"medline","retmode":"text"})
s2,b2,h2=fetch(u,"env_ncbi_test_medline.txt")
text=b2.decode("utf-8","replace"); fields={k:bool(__import__("re").search(r"(?m)^"+k+r"\s+-",text)) for k in ["PMID","TI","AU","JT","DP","MH"]}
files=[]
for n in ["env_ncbi_einfo_response.xml","env_ncbi_test_medline.txt"]:
 if os.path.exists(n):
  x=open(n,"rb").read(); files.append({"path":n,"bytes":len(x),"sha256":hashlib.sha256(x).hexdigest()})
json.dump({"timestamp_utc":now,"runner_os":platform.platform(),"python":sys.version,"run_id":os.getenv("GITHUB_RUN_ID"),"commit_sha":os.getenv("GITHUB_SHA"),"requests":attempts},open("connectivity_test.json","w"),indent=2)
json.dump({"einfo_url":base+"einfo.fcgi?db=pubmed","efetch_url":u,"attempts":attempts},open("request_manifest.json","w"),indent=2)
json.dump({"files":files},open("sha256_manifest.json","w"),indent=2)
json.dump({"runner":"github-hosted ubuntu-latest","einfo_status":s,"einfo_xml_valid":xml_ok,"efetch_status":s2,"medline_fields":fields,"files":files,"final_status":"PASS" if s==200 and xml_ok and s2==200 and fields.get("PMID") else "FAIL"},open("ENV-NCBI-003-report.json","w"),indent=2)
