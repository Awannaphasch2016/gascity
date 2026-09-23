# Verification record: <project name>

<!--
Shaped after the test-report outline of ISO/IEC/IEEE 29119-3: what was
tested, against what, how, with what result, and what was not tested and
why. Written by the tester after code review has approved, against the
commit that was approved.
-->

## 1. Subject

Repository, branch, and commit verified. The requirements document version
(commit) the tests were derived from.

## 2. Environment

Node version, operating system, browsers or tools used, how the site was
started for testing.

## 3. What was run

| Command | Purpose | Result |
|---|---|---|
| `npm test` | acceptance tests | |
| `npm run build` | clean build | |
| | | |

## 4. Requirements coverage

| Requirement | Test(s) | Result |
|---|---|---|
| FR-1 | | pass / fail / not tested |
| NFR-1 | | |

Every FR and NFR from docs/requirements.md appears here. "Not tested" needs a
reason in section 7.

## 5. Findings

What failed at any point, what was changed to fix it, and the commit that
fixed it. Empty is a valid answer only if nothing ever failed.

## 6. Review

Who approved the code review, when, and against which commit.

## 7. Not verified

Anything in the requirements this record does not cover, and why. The owner
reads this section first.
