# malloc-style allocation risks and OpenBSD hardening

Purpose: feed this file to the N184 vulnerability scanner so it can identify C
allocation patterns where an unchecked or incorrectly checked `malloc`-style use
can become exploitable.

Scope: this file covers the ISO C/POSIX libc allocation family and adjacent
libc functions that return or resize heap storage. I do not know whether N184
has a formal built-in definition of "malloc-style"; this file treats a function
as malloc-style when it either allocates heap memory that must later be released
with `free(3)`, resizes such memory, or releases it.

Primary references:

- OpenBSD `malloc(3)`: <https://man.openbsd.org/malloc.3>
- OpenBSD `reallocarray(3)`: <https://man.openbsd.org/reallocarray.3>
- OpenBSD `posix_memalign(3)`: <https://man.openbsd.org/posix_memalign.3>
- OpenBSD `strdup(3)`: <https://man.openbsd.org/man3/strdup.3>
- OpenBSD `wcsdup(3)`: <https://man.openbsd.org/wcsdup.3>
- OpenBSD `getline(3)`: <https://man.openbsd.org/getline.3>
- OpenBSD `asprintf(3)`: <https://man.openbsd.org/asprintf.3>
- OpenBSD `explicit_bzero(3)`: <https://man.openbsd.org/explicit_bzero.3>
- OpenBSD `mmap(2)` for `MAP_CONCEAL`: <https://man.openbsd.org/mmap.2>
- Linux `malloc(3)` man page, used only for portable/glibc notes:
  <https://man7.org/linux/man-pages/man3/malloc.3.html>

## Scanner summary

High-confidence risky sinks:

- `malloc(expr)` where `expr` contains `*`, `+`, `<<`, a cast from signed to
  unsigned, or attacker-controlled length data.
- `realloc(ptr, expr)` where `expr` contains `*`, `+`, `<<`, a cast from signed
  to unsigned, or attacker-controlled length data.
- `realloc(ptr, size)` assigned directly back to `ptr`.
- `realloc(ptr, size)` where the tracked allocation-size variable is updated
  before the call succeeds.
- `malloc`, `realloc`, `calloc`, `reallocarray`, `strdup`, `strndup`, `wcsdup`,
  `asprintf`, `vasprintf`, `getline`, or `getdelim` result used without a
  `NULL` or error check.
- `free(ptr)` followed by any read, write, `free`, or reallocation use of `ptr`
  without setting it to `NULL` or proving ownership moved.
- `free(ptr)` on a pointer not proven to be the exact base pointer returned by
  the allocator.
- `malloc`/`realloc` used for sensitive data without later `explicit_bzero`,
  `freezero`, or an equivalent guaranteed wipe.

Preferred hardened replacements:

- Replace `malloc(n * size)` with `reallocarray(NULL, n, size)` or `calloc(n,
  size)` when zero-initialization is correct.
- Replace `realloc(p, n * size)` with `reallocarray(p, n, size)`.
- Replace sensitive-data resizing with `recallocarray(p, oldn, newn, size)`.
- Replace sensitive-data release with `freezero(p, size)`.
- Replace sensitive-data allocation with OpenBSD `malloc_conceal(size)` or
  `calloc_conceal(n, size)` when the target platform has those APIs.

## Function inventory

| Function | Standard/source class | Unhardened risk pattern | Hardened OpenBSD variant or mitigation | References |
| --- | --- | --- | --- | --- |
| `malloc(size)` | ISO C allocator | Allocates uninitialized bytes; `malloc(n * size)` can under-allocate after integer overflow; zero-size behavior is implementation-defined outside OpenBSD. | Use `reallocarray(NULL, n, size)` or `calloc(n, size)` for array allocation. OpenBSD returns an access-protected unique pointer for zero-size allocation and warns that signed or unsigned overflow can lead to heap corruption and arbitrary code execution. | OpenBSD `malloc(3)` |
| `calloc(nmemb, size)` | ISO C allocator | Safer for array multiplication only if the implementation checks `nmemb * size`; still needs `NULL` handling; zero-size behavior is implementation-defined outside OpenBSD. | OpenBSD `calloc` checks multiplication overflow and returns `NULL` with `errno=ENOMEM` on overflow; it also zeroes the allocation. | OpenBSD `malloc(3)` |
| `realloc(ptr, size)` | ISO C allocator | `realloc(p, n * size)` can under-allocate after overflow; assigning directly to `p` loses the original allocation on failure; updating the tracked size before success corrupts program state; grown bytes are uninitialized; `realloc(p, 0)` is portability-sensitive. | Use `reallocarray(p, n, size)` for array resizing and a temporary pointer for the result. OpenBSD documents that the old object is unchanged on failure and gives the safe temporary-pointer idiom. | OpenBSD `malloc(3)`; Linux `malloc(3)` for the glibc `realloc(p, 0)` warning |
| `free(ptr)` | ISO C deallocator | Double free, invalid free, freeing interior pointers, and use-after-free are undefined behavior and exploitable ownership bugs. | OpenBSD allocator diagnostics can abort on bogus pointers, double frees, writes after free, modified chunk pointers, and canary corruption; use `freezero(ptr, size)` for sensitive data. | OpenBSD `malloc(3)` |
| `aligned_alloc(alignment, size)` | ISO C11 allocator | Undefined behavior if `alignment` is not a power of 2 or `size` is not a multiple of `alignment`; still has ordinary allocation failure and ownership risks. | Validate alignment and size before the call. OpenBSD returns `NULL`/`EINVAL` for non-power-of-2 alignment; the OpenBSD man page states undefined behavior when size is not a multiple of alignment. | OpenBSD `malloc(3)` |
| `posix_memalign(&ptr, alignment, size)` | POSIX allocator | Invalid alignment; callers may forget that success is indicated by return value `0`, not by `errno`; memory still needs `free`. | Validate `alignment` is a power of 2 and at least `sizeof(void *)`; OpenBSD says returned memory can be passed to `realloc`, `reallocarray`, and `free`, but not to `recallocarray` or `freezero`. | OpenBSD `posix_memalign(3)` |
| `strdup(s)` | POSIX heap-copy helper | Allocates based on `strlen(s) + 1`; unsafe if `s` is not a valid NUL-terminated string; result needs `NULL` check and later `free`. | No direct OpenBSD hardened replacement. Treat as `malloc(strlen(s)+1)` plus copy for scanner taint purposes. | OpenBSD `strdup(3)` |
| `strndup(s, maxlen)` | POSIX heap-copy helper | Safer than `strdup` for maximum read length, but still allocates heap memory, returns `NULL` on failure, and needs later `free`; unsafe if caller assumes the source had no truncation. | No direct OpenBSD hardened replacement. Track the returned buffer as heap-owned and check allocation failure. | OpenBSD `strdup(3)` |
| `wcsdup(str)` | POSIX heap-copy helper | Allocates based on wide-string length; unsafe if `str` is not a valid NUL-terminated wide string; result needs `NULL` check and later `free`. | No direct OpenBSD hardened replacement. Track as heap-owned memory returned by a `malloc`-using function. | OpenBSD `wcsdup(3)` |
| `asprintf(&ret, fmt, ...)` | libc formatted heap allocation, not ISO C99 per OpenBSD caveat | Format-controlled allocation; result ownership through output parameter; callers must check the return value and not use `ret` on failure unless implementation documents it. | Prefer it over fixed-buffer `sprintf`; still treat returned pointer as heap-owned and check the integer return. | OpenBSD `asprintf(3)` |
| `vasprintf(&ret, fmt, ap)` | libc formatted heap allocation, not ISO C99 per OpenBSD caveat | Same ownership and error-checking issues as `asprintf`; format string and arguments can drive allocation size. | Prefer it over fixed-buffer `vsprintf`; still check the integer return and free the returned buffer. | OpenBSD `asprintf(3)` |
| `getline(&line, &n, stream)` | POSIX resizable heap buffer helper | May allocate or reallocate `line`; caller must free it; OpenBSD says even failure may update `lineptr`, so stale-pointer assumptions can leak or double free. | Initialize `line = NULL` and `n = 0`, always free the final `line`, and treat `line`/`n` as possibly changed after failure. | OpenBSD `getline(3)` |
| `getdelim(&line, &n, delim, stream)` | POSIX resizable heap buffer helper | Same risks as `getline`; delimiter must be representable as `unsigned char`; can fail with `EOVERFLOW` after more than `SSIZE_MAX` bytes. | Initialize ownership variables and free final buffer; handle `EOVERFLOW` and ordinary allocation errors. | OpenBSD `getline(3)` |

## OpenBSD hardened APIs

### `reallocarray(ptr, nmemb, size)`

OpenBSD describes `reallocarray` as similar to `realloc`, except it operates on
`nmemb` members of `size` bytes and checks overflow in `nmemb * size`.

Stops these mistakes:

- `malloc(count * sizeof(*p))` or `realloc(p, count * sizeof(*p))` where
  `count * sizeof(*p)` wraps to a smaller allocation.
- Signed multiplication that overflows before conversion to `size_t`.
- Unsigned `size_t` multiplication that wraps by definition.
- Subsequent writes that use the logical element count and overrun the smaller
  wrapped allocation.

Scanner rule:

```text
Flag malloc/realloc size expressions containing multiplication unless all
operands are compile-time bounded or an overflow check dominates the call.
Recommend reallocarray(NULL, n, size) for allocation and reallocarray(p, n,
size) for resize.
```

Exploitability model:

```c
size_t bytes = n * sizeof(*items);   /* wraps small */
items = malloc(bytes);               /* allocates too little */
for (i = 0; i < n; i++)
    items[i] = value;                /* heap overflow */
```

OpenBSD states that signed or unsigned integer overflow in allocation sizing is
a security risk when less memory is returned than intended, because later code
may corrupt the heap and an attacker may be able to execute arbitrary code.

### `recallocarray(ptr, oldnmemb, nmemb, size)`

OpenBSD describes `recallocarray` as similar to `reallocarray`, except newly
allocated memory is cleared like `calloc`; it also explicitly discards memory
that becomes unallocated.

Stops these mistakes:

- Sensitive data remains in freed heap chunks after shrinking or freeing.
- Newly grown sensitive buffers contain uninitialized bytes.
- Resizing secrets with `reallocarray` and then separately clearing only some
  paths.
- Passing the wrong old size can be detected by OpenBSD diagnostics in cases
  where recorded metadata catches the mismatch.

Scanner rule:

```text
If a buffer name, type, or dataflow indicates secrets (key, token, password,
credential, auth, private, seed, nonce, session, cookie), flag realloc/reallocarray
unless the code uses recallocarray or proves equivalent explicit clearing of
discarded regions and newly grown regions.
```

### `freezero(ptr, size)`

OpenBSD describes `freezero` as similar to `free`, except it guarantees that the
memory range starting at `ptr` for `size` bytes is explicitly discarded while
the whole original object is deallocated.

Stops these mistakes:

- Freeing keys, passwords, tokens, or plaintext without wiping the bytes first.
- Using `memset(ptr, 0, size)` before `free(ptr)`, where an optimizer can remove
  a dead store unless the wipe primitive is guaranteed.
- Wiping the wrong length after the program lost the allocation size.

Scanner rule:

```text
Flag free(secret_ptr) when secret_ptr reaches free without a dominating
explicit_bzero/freezero/equivalent guaranteed wipe for the live secret length.
```

### `malloc_conceal(size)` and `calloc_conceal(nmemb, size)`

OpenBSD describes these as `malloc`/`calloc` equivalents whose returned
allocation is marked with `MAP_CONCEAL`; freeing such an allocation explicitly
discards its contents, and reallocation keeps those properties.

Stops these mistakes:

- Secret heap pages becoming visible in core dumps or similar memory disclosure
  surfaces controlled by `MAP_CONCEAL`.
- Forgetting to clear concealed sensitive allocations on `free`.
- Losing concealment properties across reallocation.

Scanner rule:

```text
For high-value secrets, prefer malloc_conceal/calloc_conceal on OpenBSD targets.
On portable targets, require a documented equivalent or mark residual disclosure
risk.
```

## OpenBSD allocator behavior that changes bug outcomes

OpenBSD's allocator adds runtime behavior that can turn latent heap bugs into
fail-stop crashes during testing or production. These do not make bad code
correct, but they reduce silent exploitation windows on OpenBSD.

| OpenBSD behavior | Bad behavior caught or constrained | Reference |
| --- | --- | --- |
| Zero-size allocation returns a unique access-protected zero-size object. | Code that incorrectly dereferences a zero-size allocation crashes instead of silently touching adjacent memory. | OpenBSD `malloc(3)` |
| `calloc`, `reallocarray`, and `recallocarray` return `NULL`/`ENOMEM` on `nmemb * size` overflow. | Wrapped array-size calculations do not produce undersized allocations. | OpenBSD `malloc(3)` |
| `recallocarray` returns `NULL`/`EINVAL` when `oldnmemb * size` overflows for non-`NULL` `ptr`. | Invalid old-size metadata for sensitive resize is rejected. | OpenBSD `malloc(3)` |
| Canaries can detect writes past the requested allocation length. | Heap overflows at the end of an allocation are detected when the canary is checked. | OpenBSD `malloc(3)` |
| Freecheck and junking can detect double free and write-after-free. | Reusing stale pointers, double freeing, or modifying freed chunks can abort instead of corrupting allocator state. | OpenBSD `malloc(3)` |
| Guard pages can follow page-sized or larger allocations. | Linear overflows past guarded allocations fault on guard-page access. | OpenBSD `malloc(3)` |
| Free unmap protects larger freed allocations. | Use-after-free on unused pages can fault on read or write. | OpenBSD `malloc(3)` |
| Diagnostics abort on bogus pointers, double free, write-after-free, modified chunk pointers, canary corruption, and recursive allocator use. | Invalid ownership and heap metadata corruption become explicit allocator failures. | OpenBSD `malloc(3)` |

## Risk patterns for N184

### N184-MALLOC-MUL-OVERFLOW

Match:

```c
malloc(a * b)
malloc(a * sizeof(T))
malloc(sizeof(T) * a)
malloc((size_t)a * b)
malloc(a << k)
```

Risk: arithmetic can overflow before allocation, returning less memory than
callers later write.

Require one of:

- `reallocarray(NULL, a, b)`.
- `calloc(a, b)` when zero-initialization is acceptable and target libc checks
  multiplication overflow.
- A dominating overflow check for the exact type and expression.

Notes:

- `if (a > SIZE_MAX / b)` is only valid for unsigned `size_t` operands.
- If operands are signed, also require non-negative checks and signed-overflow
  checks before conversion.

### N184-REALLOC-MUL-OVERFLOW

Match:

```c
realloc(p, a * b)
realloc(p, a * sizeof(*p))
realloc(p, a << k)
```

Risk: same under-allocation as `malloc`, plus old-object ownership complexity.

Require:

- `tmp = reallocarray(p, a, b);`
- `if (tmp == NULL) { ... }`
- Only after success: `p = tmp; capacity = a;`

### N184-REALLOC-DIRECT-ASSIGN

Match:

```c
p = realloc(p, newsize);
```

Risk: if `realloc` fails, the old object remains allocated, but the only pointer
to it may be overwritten with `NULL`, causing a leak and leaving later state
inconsistent.

Require:

```c
tmp = realloc(p, newsize);
if (tmp == NULL) {
    /* preserve or explicitly free p */
} else {
    p = tmp;
}
```

### N184-REALLOC-SIZE-STATE-BEFORE-SUCCESS

Match:

```c
capacity += delta;
tmp = realloc(p, capacity);
```

Risk: on failure, the program records a capacity that it does not own. Later
writes may use the inflated capacity and overflow the old allocation.

Require:

```c
new_capacity = capacity + delta;
tmp = realloc(p, new_capacity);
if (tmp != NULL) {
    p = tmp;
    capacity = new_capacity;
}
```

Also check `capacity + delta` for overflow before the allocation.

### N184-ALLOC-UNCHECKED-NULL

Match use of returned pointer without checking:

```c
p = malloc(size);
p[0] = x;
```

Risk: allocation failure becomes `NULL` dereference or error-path corruption.

Apply to:

- `malloc`
- `calloc`
- `realloc`
- `reallocarray`
- `recallocarray`
- `aligned_alloc`
- `strdup`
- `strndup`
- `wcsdup`
- `asprintf`
- `vasprintf`
- `getline`
- `getdelim`

### N184-FREE-SECRET-NO-WIPE

Match:

```c
free(key);
free(password);
free(token);
```

Risk: sensitive bytes can remain in allocator caches, reusable heap chunks, core
dumps, or other memory disclosure surfaces.

Require:

- `freezero(ptr, len)` on OpenBSD.
- `explicit_bzero(ptr, len); free(ptr);` or an equivalent guaranteed wipe on
  other targets.
- `malloc_conceal`/`calloc_conceal` for high-value OpenBSD secrets.

### N184-GETLINE-STALE-POINTER

Match:

```c
char *line = old;
size_t n = oldn;
if (getline(&line, &n, fp) == -1)
    free(old);
```

Risk: OpenBSD documents that `getdelim`/`getline` may update `lineptr` even when
the function fails. Freeing stale aliases can leak, double free, or free the
wrong pointer.

Require:

- Initialize with `char *line = NULL; size_t n = 0;`.
- Treat `line` and `n` as authoritative after every call.
- Free the final `line` exactly once.

### N184-ALIGNED-ALLOC-BAD-CONTRACT

Match:

```c
aligned_alloc(alignment, size)
posix_memalign(&p, alignment, size)
```

Risk:

- `aligned_alloc`: `alignment` must be a power of 2, and `size` must be a
  multiple of `alignment` under the OpenBSD-documented C11 contract.
- `posix_memalign`: `alignment` must be a power of 2 and at least
  `sizeof(void *)`.

Require validation before the call unless the values are compile-time constants
that satisfy the contract.

## Ranking guidance

Use this ranking when N184 reports findings:

- Critical: overflowable `malloc`/`realloc` size expression where attacker data
  controls an element count and later code writes by element count.
- High: direct `realloc` assignment or size-state update before success in a
  path reachable with attacker-influenced sizes.
- High: secret-bearing buffer freed without guaranteed wipe.
- Medium: unchecked allocator result used on an attacker-triggerable allocation
  path.
- Medium: `getline`/`getdelim` stale pointer handling.
- Low: alignment contract violations with only constant, non-attacker values.

## Repo-specific seed findings

These patterns were observed in an external workspace and are good seeds for
scanner validation:

- `logrotate/config.c` contains a fallback `reallocarray` implementation that
  returns `realloc(ptr, nmemb * size)`. If compiled on a platform without native
  `reallocarray`, this fallback does not implement the OpenBSD overflow check.
- `logrotate/config.c` and `logrotate/logrotate.c` contain multiple
  `malloc(sizeof(...) * count)` or `malloc(count * sizeof(...))` patterns.

The scanner should verify current line numbers from the checked-out source
instead of relying on this file, because these seed findings are repository
state, not API facts.
