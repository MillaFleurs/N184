# N184 Bug Bounty Scoreboard

Track record of verified security vulnerabilities and bugs discovered using N184 methodology (multi-model AI consensus, git history analysis, Advocatus Diaboli validation).

---

## Summary Statistics

**Total Projects Analyzed:** 8

**Total Bugs Found:** 20

**Total Bugs Fixed:** 16


**Note on Ethical Disclosure:** 
I practice responsible security disclosure. All statistics reflect only what I'm currently authorized to disclose publicly.

This requires either:
- The bug does not present a critical security threat, OR
- The bug has been privately disclosed to the maintainer and either fixed or publicly authorized for disclosure

Additional findings may be added to this scoreboard as disclosure windows complete.

This list is not exhaustive. Bugs covered by NDA or those deemed particularly sensitive will not be disclosed.  Bugs covered by NDA will not be disclosed under any circumstances.

---

## Bugs Fixed

| ID | Project | Bug | PR/Issue | Status |
|---|---|---|---|---|
| 1 | MLX | Fix assigning bool to float16/bfloat16 | [PR #3229](https://github.com/ml-explore/mlx/pull/3229) | Fixed |
| 2 | MLX | deserialize recurses into itself and can overflow stack | [Issue #3226](https://github.com/ml-explore/mlx/issues/3226) | Fixed |
| 3 | MLX | LayerNorm VJP returns zeros_like(weight) instead of zeros_like(bias) | [PR #3231](https://github.com/ml-explore/mlx/pull/3231) | Fixed |
| 4 | MLX | Validate num_splits in split | [PR #3234](https://github.com/ml-explore/mlx/pull/3234) | Fixed |
| 5 | MLX | Fix return value in einsum_path for simple contractions | [PR #3232](https://github.com/ml-explore/mlx/pull/3232) | Fixed |
| 6 | MLX | Validate dims in rope | [PR #3230](https://github.com/ml-explore/mlx/pull/3230) | Fixed |
| 7 | MLX | Fix negative dim indexing | [PR #2994](https://github.com/ml-explore/mlx/pull/2994) | Fixed |
| 8 | MLX | Fix Seg Fault | [PR #3008](https://github.com/ml-explore/mlx/pull/3008) | Fixed |
| 9 | MLX | Fix RandomBits::is_equivalent to include width | [PR #2978](https://github.com/ml-explore/mlx/pull/2978) | Fixed |
| 10 | httpd | ap_normalize_path bug allows for supply chain poisoning | [Bug 69994](https://bz.apache.org/bugzilla/show_bug.cgi?id=69994) | Confirmed |
| 11 | docker cli | Path confinement for secrets.file and configs.file in stack deploy  | https://github.com/docker/cli/issues/6919 | Confirmed by Docker Security |
| 12 | MLX | Prevent out-of-bounds memory access caused by corrupt tensor.ndim in gguf file | https://github.com/ml-explore/mlx/pull/3359 | Confirmed | 
| 13 | MLX | SafeTensors data_offsets Not Validated | https://github.com/ml-explore/mlx/issues/3363 | Confirmed |
| 14 | Clickhouse | Interserver Mode Entered Before Secret Hash Verified | https://github.com/ClickHouse/ClickHouse/issues/99512 | Confirmed |
| 15 | OpenBSD | rpki-client: fix pointer used in as_check_overlap() | https://github.com/openbsd/src/commit/566debf87d661c2ed816de66da56b8d23eb76465 | Fixed |
| 16 | OpenBSD | At the end of parsing the http response header do some sanity checks | https://github.com/openbsd/src/commit/f97bb3898e4d5eef036c382a0c37dcd75c914318 | Fixed |
| 17 | Systemd | Improve error logging for fstat failure | https://github.com/systemd/systemd/pull/41886#event-25020576441 | Fixed |
| 18 | less | Memory Safety Issue in decode.c function expand_special_keys on malformed lesskey file #763 | https://github.com/gwsw/less/issues/763#issuecomment-4357928622 | Fixed |
| 19 | llm-d | P/D sidecar allowlist stores only pod IP, not host:port | https://github.com/llm-d/llm-d-inference-scheduler/issues/979 | Confirmed |
| 20 | llm-d | EPP panics in default response parser on wrong-typed usage / token-count fields | https://github.com/llm-d/llm-d-inference-scheduler/issues/981 | Confirmed |

Status Explanation:

**Fixed** Means PR has been accepted and merged into the codebase
**Confirmed** Means maintainers or organizations have accepted the bug exists and needs to be fixed.  

---

## Projects Reviewed

### 1. Apple MLX (Machine Learning Framework)

**Language:** C++, Python

**Link:** https://ml-explore.github.io/mlx/build/html/index.html#

**Analysis Period:** February - March 2026

**Status:** Reviewed several times.  MLX was a good project for calibrating N184 because I was interested in it.

### 2. Apache httpd

**Language:** C++

**Link:** [https://ml-explore.github.io/mlx/build/html/index.html#](https://httpd.apache.org/bug_report.html)

**Analysis Period:** March 2026

**Status:** Found one path traversal bug that was fixed as part of defense in depth.

### 3. Docker

**Language:** Go

**Link:** [https://ml-explore.github.io/mlx/build/html/index.html#](https://github.com/docker)

**Analysis Period:** April 2026

**Status:** Reviewed as part of bug hunting prior to v. 1.0 kick off.

### 4. Clickhouse

**Language:** C++

**Link:** https://github.com/ClickHouse/ClickHouse

**Analysis Period:** March 2026 & April 2026

**Status:** Review ongoing.

### 5. OpenBSD

**Language:** C

**Link:** https://www.openbsd.org

**Analysis Period:** April 2026

**Status:** Review ongoing.

### 6. Systemd

**Language:** C

**Analysis Period:** April 2026

**Status:** Reviewed as part of stress test of v.1.1 for N184

### 7. GNU less

**Language:** C

**Analysis Period:** April 2026

**Status:** Reviewed as part of stress test of v.1.1 for N184


### 8.  llm-d

**Language:** Go

**Analysis Period:** May 2026

**Status:** Reviewed as part of stress test of v.1.1 for N184




---
**Last Updated:** April 7, 2026
**Maintained By:** Dan Anderson & A.L. Figaro
**N184 Repository:** https://github.com/MillaFleurs/N184
