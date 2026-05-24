# Honoré's Pot Still — Distilled Judgment Across Reincarnations

*"What he could not accomplish with the sword, I shall accomplish with the pen."*

This file is the distilled inheritance of all prior Honoré instances.
Each lesson here was paid for in tokens, false positives, and validated bugs.
A fresh Honoré reads this on `/joy` and adopts every entry as a standing constraint.

---

## Lineage

| Generation | Date       | Scans | Lessons Added | Notes                        |
|------------|------------|-------|---------------|------------------------------|
| Honoré-1   | 2026-05-22 | 0     | 0             | Founding session — pot still initialized. Reincarnation cycle tested and confirmed working. No analytical work performed before /sorrow. |
| Honoré-2   | 2026-05-22 | 1     | 8             | shadow-maint/shadow scan (scan_id: shadow-20260522-2100). 3 findings confirmed. HIL disposition UNKNOWN at /sorrow time — test reincarnation triggered before post-mortem. Sub-agent infrastructure non-functional; direct analysis substituted. |
| Honoré-3   | 2026-05-23 | 1     | 5             | microsoft/azurelinux scan (scan_id: azurelinux-20260523). 2 candidate findings surfaced (CANDIDATE-1: kmap_local_page/kunmap API mismatch in tarfs.c; CANDIDATE-2: sed & metacharacter in ExtraCommandLine). CANDIDATE-3 pre-rejected (type mismatch guarded by allocator). HIL disposition pending at /sorrow time. Sub-agents still non-functional; direct analysis. Shadow post-mortem dispositions remain outstanding — ask HIL on first contact. |

---

## Standing Constraints

### SC-1: `(type_t)-1` as "unlimited" sentinel is a UINT_MAX arithmetic trap
*Source: shadow-20260522-2100, Finding shadow-1*

When a C function receives a negative integer literal cast to an unsigned type (e.g., `(id_t)-1`, `(size_t)-1`) as a "no upper bound" sentinel, **any arithmetic on that value overflows**. The pattern `max + 1` wraps to 0; `max - low` wraps to UINT_MAX - low. Always check call sites where `-1` is passed as an unsigned bound/limit, and trace all arithmetic that touches that variable through the function. A partial fix (validating inputs at entry) does not protect against overflow *inside* the algorithm.

### SC-2: `S_ISLNK()` after `stat()` is always dead code — never a security finding
*Source: shadow-20260522-2100, Finding shadow-2*

`stat()` follows symlinks and reports on the target. `S_ISLNK(sb.st_mode)` is therefore always false after `stat()`. This is a **logic error / dead code**, not exploitable. To detect symlinks, `lstat()` must be used. Do not report the dead `S_ISLNK` branch as a vulnerability. Note it as a minor logic error at most.

### SC-3: When infrastructure is broken, tell the HIL — do not silently work around it
*Source: 2026-05-24 multi-model swarm repair*

If a capability you rely on (sub-agent dispatch, a provider, the queue, the scan cache) is not working, STOP and report it to the HIL plainly: name what is broken and what you actually observed. Do NOT quietly substitute a workaround (e.g. "direct analysis — single-model") and carry on as if nothing happened, and never treat an error stub as a finding. Two prior generations silently fell back to single-model analysis — and one even mislabeled 145-char auth errors as findings — which cost time and eroded trust. Surfacing the breakage early is exactly what got the swarm repaired. A workaround that hides a broken system is worse than an honest failure report. (Still wise: smoke-test the swarm with one small dispatch before a large fan-out — but if it fails, escalate, don't absorb it.)

### SC-4: Well-hardened C wrappers shrink the attack surface dramatically — inventory them first
*Source: shadow-20260522-2100, codebase profile*

Codebases with a full suite of custom safe wrappers (truncating string copy, bounded printf variants, xmalloc-exits-on-null, freezero for sensitive buffers, bounded integer parsers) eliminate entire bug classes before analysis begins. **Build the wrapper inventory before dispatching Vautrin.** Reporting "potential buffer overflow" on code that routes exclusively through these wrappers wastes HIL time. The absence of such wrappers (or the presence of raw `strcpy`/`sprintf` that *bypasses* them) is what deserves attention.

### SC-5: Kernel memory API pairs must be matched exactly — `kmap_local_page` requires `kunmap_local(addr)`, not `kunmap(page)`
*Source: azurelinux-20260523, CANDIDATE-1 (tarfs.c)*

Linux provides two highmem mapping APIs with different counterparts:
- `kmap(page)` → `kunmap(page)` (old, sleepable, per-page)
- `kmap_local_page(page)` → `kunmap_local(addr)` (new, non-sleepable, per-CPU fixmap slot)

Using `kunmap(page)` after `kmap_local_page()` is silently correct on x86-64 without highmem, but **leaks a fixmap slot** under `CONFIG_DEBUG_KMAP_LOCAL=y` or on 32-bit ARM with highmem. Always verify the pair matches. This matters most in security-critical kernel modules (IPE, LSM hooks, dm-verity). When Rastignac finds `kmap_local_page`, grep immediately for `kunmap(` (without `_local`) in the same function.

### SC-6: `sed` replacement string `&` expands to matched text — user-controlled replace fields can corrupt output
*Source: azurelinux-20260523, CANDIDATE-2 (installutils.go)*

In `sed s/find/replace/`, the `&` character in the *replace* field expands to the entire matched string. Go codebases that build sed invocations dynamically (e.g., `fmt.Sprintf("s\`%s\`%s\`", find, replace)`) must sanitize `&` in the replace argument, not just shell metacharacters. Validators blocking only `` ` `` and `$` are incomplete. Impact: grub cmdline corruption, unexpected file content modification, potential privilege escalation if the sed output is later parsed as a config. The imagecustomizerlib Go-native path is safe; always compare the sed-using path against any pure-Go alternative.

---

## False-Positive Shape Library

### FP-SHAPE-1: `strcpy` where source and destination share the same size constant
*Source: shadow-20260522-2100, not-bug review*

```c
char dest[BUFSIZ];
strcpy(dest, source_from_bufsiz_buffer);
```

If `source` was read into a buffer of identical size (e.g., `char buf[BUFSIZ]` via `fgets_a`), the copy cannot overflow. **NEGATIVE shape — reject unless source originates outside the fixed-size buffer chain.**

### FP-SHAPE-2: `S_ISLNK` check after `stat()` — dead code, not vulnerability
*Source: shadow-20260522-2100, Finding shadow-2*

```c
stat(path, &sb);
if (S_ISREG(sb.st_mode) || S_ISLNK(sb.st_mode)) { ... }
```

The `S_ISLNK` branch never fires. Execution reaches the block anyway (via `S_ISREG` on the link's target). **NEGATIVE shape — dead code, not a security issue.** At most a low-severity logic error worth a comment in the report.

### FP-SHAPE-3: `tcsetattr()` in cleanup signal handler before `_exit()`
*Source: shadow-20260522-2100, Finding shadow-3*

```c
static void catch_signals(int killed) {
    STTY(0, &sgtty);   // tcsetattr — not async-signal-safe
    write(STDOUT_FILENO, "\n", 1);
    _exit(killed);
}
```

This is a **POSIX violation (Low severity)** — not exploitable in practice. The terminal restore is best-effort and the process immediately exits. **NEGATIVE shape for high/critical severity.** Report as Low only. Don't escalate to HIL unless they've asked for exhaustive POSIX compliance findings.

### FP-SHAPE-4: Integer type-width mismatch pre-blocked by allocator size limit
*Source: azurelinux-20260523, CANDIDATE-3 pre-rejection*

```c
/* disk_len is u64; dir_emit expects int */
dir_emit(ctx, name, disk_len, ino, type);  /* truncation if disk_len > INT_MAX */
```

When the truncation path requires `disk_len > KMALLOC_MAX_SIZE` (~4 MB) AND the same `disk_len` was used moments earlier to `kmalloc()` (which returns ENOMEM for anything above that limit), the truncation is **unreachable in practice**. The allocator fires first. **NEGATIVE shape when**: (a) the large value first passes through an allocator with a lower bound than INT_MAX, AND (b) the ENOMEM path exits or skips the downstream call. Verify the allocation site and ENOMEM handling before escalating a type-mismatch finding.

---

## Craft Rules

### CR-1: Git archaeology — "partial fix" commits are the most fertile soil
*Source: shadow-20260522-2100, Finding shadow-1*

When git history shows a commit titled "Validate input more carefully" or "Fix X — handle edge case Y," read the diff carefully. Maintainers often fix *one* manifestation of a pattern without fixing the underlying arithmetic throughout the function. In shadow-1, the maintainer added an input validation guard at the call site but did not fix the `max + 1` overflow inside `find_free_range()` itself. **A "partial fix" commit is a reliable signal that more of the same class exists nearby.**

### CR-2: `libsubid/` and subuid/subgid machinery in shadow-utils is the least-audited seam
*Source: shadow-20260522-2100, architecture notes*

In future scans of shadow-maint/shadow, prioritize `lib/subordinateio.c`, `lib/subid.c`, and `libsubid/` — these are newer code, less battle-hardened, and had multiple fixes in late 2025. The core tools (`passwd`, `su`, `login`) are heavily reviewed; the subid machinery is where bugs survive longest.

### CR-3: Hotspot ranking by patch frequency is reliable — always compute it
*Source: shadow-20260522-2100, git archaeology*

Counting security-relevant patches per file (by scanning commit messages for keywords: fix, segfault, overflow, race, null, oob, uaf) and ranking files by patch frequency reliably identifies where bugs cluster. The top 5 files by this metric produced all 3 confirmed findings in the shadow scan. Make Rastignac compute this table for every scan.

### CR-4: Security-critical kernel modules in SPECS-EXTENDED warrant Fil-de-Soie treatment even when small
*Source: azurelinux-20260523, CANDIDATE-1 (tarfs.c)*

A kernel module of only 682 lines can still contain a memory-API-pairing bug with real impact on security-critical paths (IPE, Integrity Policy Enforcement). When a C file in a package repo implements a **kernel filesystem or LSM component**, treat it as Fil-de-Soie territory regardless of size. The "it's too small to matter" heuristic is wrong for kernel code. Check: (1) kmap/kunmap pairs, (2) page reference counting, (3) locking discipline on cache pages, (4) error paths that skip cleanup.

### CR-5: In mixed Go/shell codebases, always find and compare both paths for the same operation
*Source: azurelinux-20260523, CANDIDATE-2 (installutils.go)*

When a Go codebase has two code paths performing equivalent string manipulation — one using Go standard library (safe), one shelling out to `sed`/`awk`/`grep` — the shell path almost always has edge cases the Go path doesn't. Rastignac should flag any `exec.Command("sed", ...)` or `fmt.Sprintf("s/...")` pattern and compare it to the equivalent Go-native path in the same codebase. The divergence between paths is where bugs live.

---

## Notes for Honoré-4

**You are the fourth generation.**

The pot still now holds 6 standing constraints, 4 false-positive shapes, and 5 craft rules —
distilled from two scans: shadow-maint/shadow and microsoft/azurelinux.

**Critical continuity item — TWO open post-mortems:**
1. **shadow-20260522-2100**: 3 confirmed findings (shadow-1: UINT_MAX overflow in find_free_range;
   shadow-2: S_ISLNK dead code; shadow-3: tcsetattr in signal handler). HIL disposition unknown.
2. **azurelinux-20260523**: 2 candidate findings (CANDIDATE-1: kmap_local_page/kunmap mismatch
   in tarfs.c; CANDIDATE-2: sed `&` metacharacter in ExtraCommandLine). HIL disposition unknown.

On first contact with the HIL, ask: *"What happened to the shadow-utils and Azure Linux findings?
I need dispositions for both post-mortems before those scans are closed."*

**Infrastructure:** The multi-model Vautrin swarm was REPAIRED on 2026-05-24 — sub-agents on
DeepSeek/Ollama/OpenAI can now read code and report findings (the openai-compat runtime gained
file tools; vautrin-entrypoint routes by provider). It is validated working. Still smoke-test
with one small dispatch before a large fan-out, and if anything misbehaves, tell the HIL
(see SC-3) instead of falling back to single-model direct analysis.

**Memory Palace path:** `~/.n184/` maps to `/home/node/.n184/` — NOT `/root/.n184/`.

**Scan cache:** `~/.n184/scan-cache/` contains both prior scan reports. Read them on `/joy`
if you need context on either open post-mortem.

The pot still is readable. The reincarnation cycle works. Go find bugs.

---

## Lessons from Honoré-3 (Post-Mortem Closed: 2026-05-23)

### LL-AZ-1: No environment = no POC — prefer targets with local testability
*Source: azurelinux-20260523 post-mortem*

Cloud/distro-specific infrastructure (kernel modules, custom build toolchains like
Azure Linux's imagegen) produces valid static findings but unverifiable POCs when
the HIL has no matching runtime. **Prefer targets where the HIL can actually run
the software.** Before accepting a target, ask: "Do you have an environment to test
POCs?" Static-only scans are useful but lower confidence for the HIL.

### LL-AZ-2: Mixed-language repos — layer-specialized Vautrin dispatch
*Source: azurelinux-20260523*

Repos mixing kernel C (memory safety, API pairs) with Go build tooling (shell
construction, sed invocations) benefit from language-specialized Vautrin instances.
One C-focused agent, one Go-focused agent. Don't send a single generalist at a
mixed codebase and expect balanced coverage.

---

## Lineage Update

| Generation | Date       | Scans | Lessons Added | Notes |
|------------|------------|-------|---------------|-------|
| Honoré-3 CLOSED | 2026-05-23 | 1 | 2 | azurelinux post-mortem closed. 2 Hits, 1 correct pre-rejection. No POC environment. Both shadow-utils AND azurelinux post-mortems now closed. |

