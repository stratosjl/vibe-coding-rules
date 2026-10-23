# Example assurance record

This record is synthetic. It describes an imaginary static documentation site at revision `1111111`, and shows one control of each kind of outcome: an applicable control that passed, a justified non-applicable control, a stale review and a failed check. `python3 bin/check-assurance.py docs/examples/assurance-record.md --revision 1111111` accepts it as well formed; adding `--release` rejects it, because the review is stale and the evidence manifest failed.

## Assurance

| Control | Requirements | Applies | Reason | Owner | Status | Evidence | Kind | Result | Revision | Reviewer | Independence | Trigger |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-TRACE | R-1, R-2 | yes | Link checker and build are the site's contract | site maintainer | passed | `tests/test_links.py` | test | pass | 1111111 | - | - | - |
| AC-RESTORE | - | no | Stateless: the site is rebuilt from the repository; no data to restore | site maintainer | not-applicable | - | - | n/a | n/a | - | - | - |
| AC-REVIEW | R-3 | yes | The deploy token's scope changed | site maintainer | stale | `reviews/token-scope.md` | review | pass | 0000000 | second maintainer | Did not write the change; reviewed the diff with read access only | - |
| AC-EVIDENCE | - | yes | Release evidence for the site | site maintainer | failed | `release/manifest.json` | manifest | fail | 1111111 | - | - | - |
