# ESearch source identity versus posted output identity

Protocol reference: NCBI E-utilities In-Depth, ESearch / History Server parameters:
https://www.ncbi.nlm.nih.gov/books/NBK25499/

This review uses the official documentation excerpts already retrieved during
SR-PUBMED-002-002. No NCBI request, including documentation fetch, is made in H.
The documentation states that usehistory=y posts the result set to History and is
required for WebEnv and history keys in term. A supplied existing WebEnv receives
appended results. Thus a returned QueryKey identifies a posted output set and need
not equal the input source set key; no assumption of consecutive key numbering is
made. This is a protocol-derived distinction, not a new live-response observation.

For every page, preserve initial WebEnv and QueryKey from the frozen-query search:
term=#INITIAL_KEY, WebEnv=INITIAL_ENV, usehistory=y. Never chain the returned key
into subsequent page requests. Returned WebEnv, when present, must match the source
environment; a returned key, when present, must be a positive integer. Missing
optional returned identity fields do not replace or erase the known request source.

The execution report records source and output identities separately for each page,
with Count, boundaries and ordered IDs. Independent acceptance derives these same
bindings from the immutable request log and raw XML, checks the report, and rejects
any later request that switches to an output key. Server warnings, boundary/Count
changes, overlaps, wrong cardinality and global duplicates remain fail-closed.

This does not prove membership against a malicious/incorrect server returning an
equally sized but different set while misreporting its context; no independent
membership reference exists before initial IDs are retrieved. Correct parameter
binding and complete page evidence are the validation boundary. No claim of real
successful pagination is made by offline fixtures. Translation checks are unchanged.
