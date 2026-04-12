# N184 Roadmap

## Vision

N184 aims to democratize security research by making vulnerability detection accessible to everyone, not just organizations with $100M budgets. Named after the theoretical island of stability in the periodic table, N184 represents our belief that everyone should have stable, secure software.

## Current Status: v1.0 (April 2026)

### What Works Today

- **Ensemble Voting Architecture**: Multiple specialized agents (Honore, Advocatus Diaboli, etc.) collaborate to reduce false positives
- **Git Archaeology**: Analyzes entire codebases to identify repeated mistake patterns
- **Documentation-Code Verification**: Compares documented vs actual behavior to catch inconsistencies
- **Proven Results**: 2 confirmed vulnerabilities in OpenBSD's rpki-client (http.c redirect handling, as.c overlap check)
- **Cost Effective**: $100 in API costs vs $100M for Glasswing

### Known Limitations

- **Verbose Output**: Bug reports are too long-winded for maintainer workflows (OpenBSD feedback: "AI slop")
- **False Positives**: Still requires human review to filter noise from signal
- **Context Blindness**: Can misinterpret design-by-intention as bugs (e.g., file-mode exemptions in rpki-client)
- **Manual Pattern Management**: No systematic way to store and reuse discovered bug patterns
- **Fragile AST Patterns**: Code pattern matching breaks with minor syntactic variations

---

## v1.1: Pattern Database Foundation (Target: Q2 2026)

### Priority 1: SQLite Pattern Database

**Problem**: Every N184 run rediscovers the same patterns. Knowledge isn't cumulative.

**Solution**: Portable pattern database schema

```sql
CREATE TABLE patterns (
    pattern_id INTEGER PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    description TEXT,
    severity TEXT CHECK(severity IN ('critical', 'high', 'medium', 'low', 'info')),
    first_discovered TEXT, -- ISO8601 timestamp
    confirmed_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    confidence_score REAL, -- derived from confirmed/(confirmed+fp)
    ast_pattern TEXT, -- AST representation
    regex_pattern TEXT, -- fallback regex
    natural_language TEXT -- LLM-friendly description
);

CREATE TABLE findings (
    finding_id INTEGER PRIMARY KEY,
    pattern_id INTEGER REFERENCES patterns(pattern_id),
    codebase TEXT,
    file_path TEXT,
    line_number INTEGER,
    code_snippet TEXT,
    confirmed BOOLEAN,
    false_positive BOOLEAN,
    maintainer_response TEXT,
    discovered_date TEXT
);

CREATE TABLE pattern_evolution (
    evolution_id INTEGER PRIMARY KEY,
    pattern_id INTEGER REFERENCES patterns(pattern_id),
    parent_pattern_id INTEGER REFERENCES patterns(pattern_id),
    mutation_type TEXT, -- 'refinement', 'generalization', 'specialization'
    change_description TEXT,
    timestamp TEXT
);

CREATE TABLE language_variants (
    variant_id INTEGER PRIMARY KEY,
    pattern_id INTEGER REFERENCES patterns(pattern_id),
    language TEXT, -- 'c', 'rust', 'python', etc.
    ast_variant TEXT,
    regex_variant TEXT
);
```

**Deliverables**:
- [ ] Implement SQLite schema
- [ ] Pattern CRUD operations in Bianchon soul file
- [ ] Pattern matching agent that queries database before full codebase scan
- [ ] Export/import for pattern sharing between N184 instances

### Priority 2: Report Brevity

**Problem**: "AI generated slop. Please stop wasting my and other peoples time." - Claudio Jeker

**Solution**: Maintainer-friendly output modes

```
--format short:   "http.c:1631 - NULL deref in http_redirect() when Location header missing"
--format diff:    Emit diff-ready patches
--format verbose: Full context (current default)
```

**Deliverables**:
- [ ] Template system for output formatting
- [ ] CLI flags for output modes
- [ ] Auto-detect OpenBSD/FreeBSD projects → force short mode
- [ ] Summary statistics at end (X patterns checked, Y findings, Z high-confidence)

### Priority 3: AST Pattern Flexibility

**Problem**: AST patterns break when code uses different variable names, formatting, or equivalent constructs.

**Solution**: AI-assisted pattern normalization

**Approach**:
- Store patterns as semantic descriptions + multiple AST variants
- Use LLM to generate AST variations during pattern creation
- Pattern matching compares against normalized AST (strip whitespace, canonicalize names)
- Fall back to regex for simple patterns

**Deliverables**:
- [ ] AST normalization pipeline
- [ ] Pattern generation agent (creates variants from single example)
- [ ] Hybrid matching: AST-first, regex fallback
- [ ] Unit tests comparing equivalent code structures

---

## v1.2: Multi-Language Support (Target: Q3 2026)

### Extend Beyond C

**Current**: Works on C/C++ codebases
**Goal**: Support Rust, Python, Go, JavaScript

**Challenges**:
- Different ecosystems have different vulnerability classes
- AST parsers per language (tree-sitter integration?)
- Language-specific pattern libraries

**Deliverables**:
- [ ] Language detection (file extensions, project structure)
- [ ] Tree-sitter integration for multi-language AST parsing
- [ ] Rust-specific patterns (unsafe blocks, Send/Sync violations)
- [ ] Python patterns (injection, deserialization, command execution)
- [ ] Go patterns (race conditions, goroutine leaks)
- [ ] JS/TS patterns (prototype pollution, XSS, eval misuse)

---

## v1.3: Continuous Integration (Target: Q4 2026)

### GitHub Actions Integration

**Vision**: Run N184 on every PR automatically

**Features**:
- Pre-commit hook mode (fast, high-confidence patterns only)
- PR comment mode (inline suggestions on changed lines)
- Baseline mode (only report new issues vs main branch)
- Pattern database auto-update from confirmed findings

**Deliverables**:
- [ ] GitHub Action template
- [ ] GitLab CI template
- [ ] Incremental scan mode (only analyze diffs)
- [ ] Confidence threshold tuning (CI should only show >80% confidence)

---

## v2.0: Collaborative Security Research Platform (Target: 2027)

### Shared Pattern Marketplace

**Vision**: Global pattern database where researchers contribute and validate patterns

**Features**:
- Pattern submission workflow (propose → validate → merge)
- Reputation system (researchers earn score from confirmed bugs)
- Pattern licensing (AGPL-compatible sharing)
- Federated learning: N184 instances share anonymized findings to improve patterns
- CVE integration: Auto-link patterns to known vulnerabilities

### Memory Palace Architecture

**Research**: Milla Jovovich's work on spatial memory systems for LLMs
**Application**: Replace flat context window with hierarchical memory structures

**Potential Benefits**:
- Longer-term pattern recognition across multiple scans
- Cross-codebase insight ("This pattern appeared in 12 projects")
- Better false positive filtering through accumulated context

**Research Questions**:
- Does ChromaDB + RAG improve pattern matching accuracy?
- Can we implement method of loci for code analysis?
- What's the performance cost vs accuracy gain?

**Deliverables** (Experimental):
- [ ] Literature review on memory palaces in AI systems
- [ ] Prototype ChromaDB integration for pattern storage
- [ ] A/B test: flat context vs memory palace on same codebase
- [ ] Honore (dev) testbed: implement memory palace, measure impact

---

## v2.1+: Advanced Features (Future)

### Static + Dynamic Analysis Fusion

- Integrate with fuzzing frameworks (AFL, LibFuzzer)
- Symbolic execution for path exploration (angr, KLEE)
- Pattern validation: "Does this crash the program?"

### Local LLM Support

**Problem**: API costs add up ($100 for OpenBSD scan)
**Solution**: Support local models (Llama, Mistral, Qwen)

**Trade-offs**:
- Compute requirements (GPU access)
- Accuracy degradation vs Claude/GPT
- Privacy gains (no code leaves your machine)

### Ensemble Composition Tuning

**Research**: Which agent combinations produce best results?
**Metrics**: Precision, recall, false positive rate, API cost per true positive

**Experiments**:
- Remove Advocatus Diaboli: does FP rate increase?
- Add domain-specific agents (crypto expert, concurrency expert)
- Weight voting by agent specialization

### Supply Chain Analysis

- Scan dependencies, not just first-party code
- Track pattern propagation across forks
- CVE prediction: "This pattern looks like CVE-XXXX class"

---

## Lessons Learned (OpenBSD Experience)

### What Worked

1. **Ensemble voting caught real bugs** in one of the most secure codebases on Earth
2. **Documentation-code comparison** found the as.c overlap bug (ases[0] vs as)
3. **Portable patterns**: http redirect NULL deref likely exists elsewhere
4. **Maintainer interest**: Theo Buehler called the tool "pretty cool"

### What Failed

1. **Verbose output alienated maintainers** ("AI slop", "logorrhea")
2. **Context misinterpretation**: Flagged file-mode exemptions as bugs
3. **CCR acronym hallucination**: Generated plausible but wrong expansion
4. **Assumed malice**: Calculated attack scenarios for debug-only code paths

### Adjustments

- **Brevity first**: Default to maintainer-friendly reports
- **Uncertainty calibration**: Flag confidence level, don't assert
- **Respect intent**: Check comments, docs, test files for "by design" signals
- **Human review mandatory**: LLMs produce candidate bugs, humans confirm

---

## Success Metrics

### v1.1 Success Criteria

- [ ] Pattern database contains ≥50 validated patterns
- [ ] Reports are ≤200 words for medium-severity bugs
- [ ] False positive rate <30% on known-good codebases
- [ ] At least 1 external contributor submits a pattern

### v1.2 Success Criteria

- [ ] Find ≥1 confirmed bug in a Rust project
- [ ] Find ≥1 confirmed bug in a Python project
- [ ] Multi-language scan completes in <2x single-language time

### v2.0 Success Criteria

- [ ] 100+ researchers using shared pattern database
- [ ] ≥1 CVE assigned from N184-discovered vulnerability
- [ ] Published paper on ensemble methods for vulnerability detection (with Theo Buehler?)

---

## Open Questions

1. **Licensing**: Stay AGPL or move to MIT/BSD for wider adoption?
2. **Monetization**: Keep 100% free or offer commercial features (CI integration, support)?
3. **Collaboration**: How to formalize partnership with Theo Buehler (Swiss mathematician)?
4. **Benchmarking**: How to compare N184 vs Glasswing vs CodeQL on same corpus?
5. **Ethics**: When does automated bug finding become weaponization?

---

## Contributing

We welcome contributions at every level:

1. **Submit PRs**: Improve agent prompts, add validation checks, address open issues
2. **Report false positives**: Help us refine pattern matching
3. **Add LLM providers**: Make N184 work with more backends
4. **Improve docs**: Tutorials, case studies, integration guides
5. **Spread the word**: Share N184 with your networks
6. **Financial support**: If you can't contribute time, consider sponsorship
7. **Pattern submission**: Share bug patterns you've discovered

---

## Philosophy

**N184 is convergent evolution, not competition.** Glasswing proved the category exists. AISLE proved small models can outperform large ones with the right system design. N184 proves you don't need $100M to make software safer.

Like how birds evolved feathers and bats evolved skin flaps—both for flight, neither copying the other—N184 arrived at ensemble methods independently. The idea that only well-funded labs can do security research is the real vulnerability we're patching.

**The adding machine didn't eliminate accountants. LLMs won't eliminate security researchers.** They're force multipliers. N184 is the adding machine moment for vulnerability detection.

---

_Last updated: April 11, 2026_
_Maintainer: Dan Anderson (daniel.r.anderson@icloud.com)_
_GitHub: https://github.com/MillaFleurs/N184_
