# N184 Bug Bounty Scoreboard

Track record of verified security vulnerabilities and bugs discovered using N184 methodology (multi-model AI consensus, git history analysis, Advocatus Diaboli validation).

---

## Summary Statistics

**Total Projects Analyzed:** 3

**Total Bugs Found:** 11

**Total Bugs Fixed:** 9


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



---
**Last Updated:** April 5, 2026
**Maintained By:** Dan Anderson & A.L. Figaro
**N184 Repository:** https://github.com/MillaFleurs/N184
