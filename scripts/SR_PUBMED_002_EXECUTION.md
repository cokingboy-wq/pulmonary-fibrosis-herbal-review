# Execution safeguards

This implementation is prepared for explicit human-authorized execution only.
Importing modules and all committed unit tests use no live NCBI requests.

* Only workflow_dispatch can start the formal workflow, with the exact confirmation
  EXECUTE_FROZEN_PUBMED_QUERY. Frozen SHA256 is checked before execution.
* Count greater than 9999 produces INCOMPLETE and stops. No truncation or automatic
  date partitions are permitted. Larger counts require a separately reviewed strategy.
* Each execution creates a UUID-suffixed directory with exist_ok=False. The former
  RUN-001 preflight directory is never used as a formal output directory.
* Initial metadata, immutable state events, and final run_metadata.json distinguish
  NOT_ATTEMPTED, REQUEST_ATTEMPTED, HTTP_RESPONSE_RECEIVED and VALID_SEARCH_RESULT.
  ncbi_accessed means an HTTP response was received, not merely an attempted socket.
  Simulations use SIMULATION identities and always keep ncbi_accessed false.
* API_RETRIEVAL_COMPLETE is separate from PENDING_WEB_EVIDENCE. Failure before a
  valid initial search is FAILED; later incomplete retrieval is INCOMPLETE.
* Raw bytes, partial IncompleteRead bytes, per-attempt logs, partial PMID lists,
  batch reconciliation, metadata and hashes are retained on handled errors.
  Transport exceptions without an exposed partial body cannot recover unseen bytes.
* MEDLINE records require one numeric PMID, TI and DP; AU/JT/MH are not universally
  mandatory across all PubMed record types. Malformed lines and wrong batch IDs fail.
  Record field validation cannot prove that every abstract sentence is untruncated.
* Artifact upload uses always() and requests 90 days retention. Actual retention
  remains subject to repository/organization limits; upload success is not local
  persistence acceptance. Download promptly to an independent Windows directory,
  verify each relative path/size/SHA256 after reopening, and retain the downloaded ZIP.
  Never overwrite previous evidence. Repository settings are not changed here.
* Abrupt runner cancellation or storage failure may interrupt finalization or upload.
  A future run is not accepted until its artifact and local hashes are verified.
* No filtering tables or PRISMA counts are written by this program.

Offline verification: python -m unittest discover -s tests -v
