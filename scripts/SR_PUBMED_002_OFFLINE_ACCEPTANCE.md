# G-stage offline cloud acceptance

Manual-only workflow: .github/workflows/sr-pubmed-002-offline-acceptance.yml
Baseline ancestor: ca5e29388f4416665426f033f55dbb653067fdec

The preparation does not start Actions. Checkout/setup-python happen in the normal
runner namespace, then the Python audit/test process is launched in a fresh network
namespace with no interfaces except loopback, no external routes, a non-root UID,
no capabilities and no_new_privs. A documentation-address connect_ex probe must
return unreachable, and namespace/capability/route evidence is retained before any
unittest discovery. Isolation failure stops discovery. Artifact upload remains in
the normal Actions namespace after the isolated process exits.

Audits are isolated by Actions Run ID and Attempt. The wrapper executes exactly
python -m unittest discover -s tests -v and records actual counts/errors/skips and
elapsed time. A cloud-only successful test result is PARTIAL until Windows ZIP
acceptance. A missing audit launcher produces explicit launcher-failure evidence,
never fabricated test output. Hard cancellation/storage failures can still interrupt
evidence generation/upload. Artifact retention requests 90 days, subject to policy.

Known baseline test portability issue (not changed in this preparation):
tests/test_env_ncbi_003.py imports a nonexistent tests/env_ncbi_003.py and references
a Windows absolute sample path. Ubuntu discovery is expected to report an import
error. tests/test_independent_pubmed_regression.py explicitly skips its real sample
test if the uncommitted sample is absent. No tests are excluded to reproduce 81.
The actual Runner outcome must be recorded, even if FAIL or count differs from 81.

After user-triggered completion, retain independently obtained GitHub run/artifact
API metadata, effective expiration and upload digest. Download original ZIP into a
new Windows audit directory, compare ZIP digest to GitHub, safely extract without
overwriting, reread every manifest file and verify sizes/SHA256. Cross-check unittest
log/counts/exit code, network isolation evidence and final report. Do not reuse the
formal retrieval artifact verifier: G-stage is a different evidence schema.

No live NCBI access, formal retrieval workflow, formal run registration, screening
or PRISMA changes are included in this workflow. Its namespace probe is not a test
of NCBI connectivity. Test simulation directories are temporary test fixtures only.
