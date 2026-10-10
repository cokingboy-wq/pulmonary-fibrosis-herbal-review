import argparse, hashlib, json, os, platform, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", required=True)
args = parser.parse_args()
out = os.path.abspath(args.output_dir)
os.makedirs(out, exist_ok=True)
attempts = []

def fetch(url, filename):
    for attempt in range(1, 4):
        started = time.time()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ENV-NCBI-003"})
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                status = response.status
                headers = dict(response.headers)
            with open(os.path.join(out, filename), "wb") as handle:
                handle.write(body)
            attempts.append({"url": url, "attempt": attempt, "status": status,
                             "headers": headers, "bytes": len(body),
                             "elapsed_seconds": time.time() - started})
            return status, body
        except Exception as exc:
            attempts.append({"url": url, "attempt": attempt, "error": str(exc),
                             "elapsed_seconds": time.time() - started})
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    return None, b""

def digest_files(names):
    result = []
    for name in names:
        path = os.path.join(out, name)
        if os.path.isfile(path):
            data = open(path, "rb").read()
            result.append({"path": path, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
    return result

timestamp = datetime.now(timezone.utc).isoformat()
base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
einfo_url = base + "einfo.fcgi?db=pubmed"
einfo_status, einfo_body = fetch(einfo_url, "env_ncbi_einfo_response.xml")
try:
    ET.fromstring(einfo_body)
    einfo_xml_valid = True
except (ET.ParseError, ValueError):
    einfo_xml_valid = False

efetch_url = base + "efetch.fcgi?" + urllib.parse.urlencode({
    "db": "pubmed", "id": "31452104", "rettype": "medline", "retmode": "text"})
efetch_status, medline_body = fetch(efetch_url, "env_ncbi_test_medline.txt")
medline_text = medline_body.decode("utf-8", "replace")
pmid_match = re.search(r"(?m)^PMID\s*-\s*(\d+)", medline_text)
fields = {field: bool(re.search(r"(?m)^" + field + r"\s+-", medline_text))
          for field in ("PMID", "TI", "AU", "JT", "DP", "MH")}
required_fields = all(fields.values())
pmid_matches_requested = pmid_match is not None and pmid_match.group(1) == "31452104"

files = digest_files(["env_ncbi_einfo_response.xml", "env_ncbi_test_medline.txt"])
audit = {"timestamp_utc": timestamp, "runner_os": platform.platform(),
         "python": sys.version, "run_id": os.getenv("GITHUB_RUN_ID"),
         "commit_sha": os.getenv("GITHUB_SHA"), "attempts": attempts}
json.dump(audit, open(os.path.join(out, "connectivity_test.json"), "w"), indent=2)
json.dump({"einfo_url": einfo_url, "efetch_url": efetch_url, "attempts": attempts},
          open(os.path.join(out, "request_manifest.json"), "w"), indent=2)
json.dump({"files": digest_files(["env_ncbi_einfo_response.xml",
                                  "env_ncbi_test_medline.txt"])},
          open(os.path.join(out, "sha256_manifest.json"), "w"), indent=2)
passed = (einfo_status == 200 and einfo_xml_valid and efetch_status == 200
          and pmid_matches_requested and required_fields)
report = {"runner": "github-hosted ubuntu-latest", "einfo_status": einfo_status,
          "einfo_xml_valid": einfo_xml_valid, "efetch_status": efetch_status,
          "pmid": pmid_match.group(1) if pmid_match else None,
          "pmid_matches_requested": pmid_matches_requested,
          "medline_fields": fields, "files": files,
          "final_status": "PASS" if passed else "FAIL"}
json.dump(report, open(os.path.join(out, "ENV-NCBI-003-report.json"), "w"), indent=2)
sys.exit(0 if passed else 1)
