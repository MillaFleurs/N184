# N184 Bug Bounty Scoreboard

Track record of verified security vulnerabilities and bugs discovered using N184 methodology (multi-model AI consensus, git history analysis, Devil's Advocate validation).

---

## Summary Statistics

**Total Projects Analyzed:** 1
**Total Bugs Found:** 6

---

## Bugs Fixed

| ID | Project | Bug | PR/Issue |
|---|---|---|---|
| 1 | MLX | Fix assigning bool to float16/bfloat16 | [PR #3229](https://github.com/ml-explore/mlx/pull/3229) |
| 2 | MLX | deserialize recurses into itself and can overflow stack | [Issue #3226](https://github.com/ml-explore/mlx/issues/3226) |
| 3 | MLX | LayerNorm VJP returns zeros_like(weight) instead of zeros_like(bias) | [PR #3231](https://github.com/ml-explore/mlx/pull/3231) |
| 4 | MLX | Validate num_splits in split | [PR #3234](https://github.com/ml-explore/mlx/pull/3234) |
| 5 | MLX | Fix return value in einsum_path for simple contractions | [PR #3232](https://github.com/ml-explore/mlx/pull/3232) |
| 6 | MLX | Validate dims in rope | [PR #3230](https://github.com/ml-explore/mlx/pull/3230) |
| 7 | MLX | Fix negative dim indexing | [PR #2994](https://github.com/ml-explore/mlx/pull/2994) |
| 8 | MLX | Fix Seg Fault | [PR #3008](https://github.com/ml-explore/mlx/pull/3008) |
| 9 | MLX | Fix RandomBits::is_equivalent to include width | [PR #2978](https://github.com/ml-explore/mlx/pull/2978) |


---

## Projects

### 1. Apple MLX (Machine Learning Framework)

**Language:** C++
**Analysis Period:** February - March 2026
**Status:** 


---
**Last Updated:** April 5, 2026
**Maintained By:** Dan Anderson & A.L. Figaro
**N184 Repository:** https://github.com/MillaFleurs/N184
