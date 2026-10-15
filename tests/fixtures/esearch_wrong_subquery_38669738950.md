# Real failed ESearch response fixture

esearch_wrong_subquery_38669738950.xml contains the unchanged 427 response bytes
from esearch_0.attempt1.raw in the historical INCOMPLETE run:
SR-PUBMED-002-20261015T021223Z-93178e0b16c0.

GitHub Run ID: 38669738950; Artifact ID: 11943289883.
Artifact ZIP SHA256: ace29aa4475af5669223f469c8bdec0a892994a287e50f70524d9f1325f0b77b
Fixture SHA256: 095a7ff05185d1d794f40357eef6d2e94a4bb9512a8c4005dc86e03bf4c0d0e4

The server returned HTTP 200, Count=0, RetStart=0, RetMax=0, empty IdList and
QueryTranslation, and Wrong subquery #1 / No items found warnings. The request
used term=#1 and WebEnv but omitted usehistory=y. This fixture is only an error
regression sample, not a successful page or a verified zero-result search.

NCBI official History parameter contract:
https://www.ncbi.nlm.nih.gov/books/NBK25499/
ESearch requires usehistory=y when using WebEnv or history keys in term.
The initial real search returned Count=382. Do not confuse that Count with a
completed PMID set, exported record count or a PRISMA count.
