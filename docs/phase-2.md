# Phase 2 — Deterministic Diff Extraction, LineMap and AST Analysis

## Objective

Everything between "a pull request is synchronised" and "we know exactly which
lines changed and which declarations they live in" — with no AI, no comments
and no repository execution.

```
synchronised PR (Phase 1)
  ↓ classify            path-only; decides what is worth fetching, and records why not
  ↓ parse patch         unified diff → hunks, two independent line cursors
  ↓ build LineMap       ★ ground truth: added / context / deleted, side, commentability
  ↓ fetch source        one blob, at the exact head SHA — never a clone
  ↓ parse               native `ast` (Python) or Tree-sitter → declarations
  ↓ map                 changed line → innermost declaration + parent chain
  ↓ PullRequestAnalysis deterministic, source-free, ephemeral
```

## LineMap semantics

The vocabulary every later phase validates against.

| Term | Meaning |
|---|---|
| `old_line` | 1-based line in the **pre-image** (base file) |
| `new_line` | 1-based line in the **post-image** (head file) |
| `side` | `LEFT` = pre-image, `RIGHT` = post-image (GitHub's own words) |
| `change_type` | `added` · `deleted` · `context` |
| `is_commentable` | `RIGHT` side, inside a hunk — added **or** context |

Rules, each with a reason:

- **A line has `old_line`, `new_line`, or both.** Added → new only. Deleted →
  old only. Context → both.
- **Deletions are never folded into new-file lines.** A deleted line's number
  refers to a file that no longer exists at head; treating it as a new-file
  line is how a comment lands on unrelated code.
- **Context lines are commentable.** GitHub accepts a comment on any line in
  the diff, and a finding about an unchanged line the change *made* wrong is
  legitimate.
- **`side: LEFT` comments are never emitted.** Accepted by the API, but
  confusing and a reliable source of 422s (architecture.md §9.5).
- **`position` is not the internal representation.** GitHub documents it as
  closing down; `line` + `side` is what we carry.

## Source retrieval

`SourceProvider` protocol, two implementations: `GitHubSourceProvider` and
`InMemorySourceProvider` (a real second implementation for tests — not a fake
GitHub).

- **Never clones.** Only the files the PR touched, one blob each.
- **Blob SHA preferred** over path + ref: exact, immune to a racing
  force-push, no 1 MB truncation, and no repository-controlled path ever
  reaches a URL. The contents endpoint is the fallback, and there paths are
  percent-encoded with traversal rejected first.
- **Deleted files are never fetched.** Classified before any request.
- **Decoding is strict UTF-8.** A failure is reported, not papered over with
  `errors="replace"` — replacement characters would corrupt the exact text
  anchoring depends on.

### Exact anchoring

`SourceFile.line(n)` returns the exact content of a 1-based line, or `None`
outside the file. Preserved verbatim: tabs, trailing whitespace, Unicode,
blank lines, very long lines. One normalisation, applied on **both** sides so
they agree: a line's content never includes its terminator, and a CRLF pair's
`\r` is dropped. Splitting is on `\n` only — `str.splitlines()` also breaks on
`\x0b`, `\x0c` and ` `, which occur inside real source and would invent
lines.

## Supported languages

| Language | Extensions | Backend |
|---|---|---|
| Python | `.py` `.pyi` | native `ast`, Tree-sitter fallback |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | Tree-sitter |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | Tree-sitter |
| Java | `.java` | Tree-sitter |

Detection is a lookup into a closed extension table whose miss case is an
explicit `UNSUPPORTED_LANGUAGE` classification — never a guess, and never
content sniffing that could vary between runs.

Python prefers native `ast` because it is semantically canonical, has no
dependency, and handles decorators, `async def` and comprehension scopes
without grammar-node guesswork. Tree-sitter takes over when `ast.parse` raises,
because it recovers declarations from mid-edit code.

## AST declaration model

```
Declaration
  declaration_id   sha256(path, kind, qualified_name, span)[:16] — deterministic
  kind             module · class · interface · enum · struct · function ·
                   async_function · method · constructor · arrow_function ·
                   property · type_alias · field
  name, qualified_name       e.g. "Invoice.total"
  start_line, end_line       1-based, inclusive
  start_column, end_column   0-based (parser convention; the difference is
                             stated rather than discovered)
  parent_id, modifiers, signature
```

Every file gets an implicit `<module>` declaration spanning it, so a changed
import has a real structural home rather than being forced into a nearby
function.

Ids are content-addressed, never UUIDs: the same snapshot always yields the
same ids, so artifacts are comparable across runs.

## Changed-line → declaration mapping

**Innermost wins**; ties broken deterministically by (later start, earlier end,
qualified name) so parse order can never leak into the result.

```
changed line 9
  ↓ chain, outermost → innermost
<module> → Invoice → Invoice.total
```

- A line inside a class but outside a method maps to the class.
- A line outside every declaration maps to `<module>` — never to a fake
  enclosing function.
- Deletions are **not** mapped: their numbers refer to the base file, and the
  declarations were parsed from head.

## Error and limit handling

Per-file isolation throughout: one bad file never stops the others.

| Situation | Outcome |
|---|---|
| Syntax error | `ANALYSED_WITH_ERRORS` or `FAILED`; other files unaffected |
| Missing Tree-sitter grammar | `PARSER_UNAVAILABLE` — distinct from a parse failure |
| Malformed patch | `PatchStatus.MALFORMED` + reason; no exception |
| Absent patch | `ABSENT_BINARY` / `ABSENT_TOO_LARGE` / `ABSENT_UNKNOWN` — never "no changes" |
| Binary / vendored / generated / lockfile | Skipped with a specific reason |
| Over a size limit | Skipped, **never silently truncated** |
| Fetch failure | `FAILED` for that file only |

Limits are configuration (`AnalysisConfig`): `max_file_bytes`,
`max_total_bytes`, `max_changed_lines_per_file`, `max_files`. A truncated read
sets `truncated=True` and `is_authoritative=False`, so nothing derived from a
partial file can be used for line validation.

## Determinism guarantees

Same repository, head SHA, source, diff and configuration → byte-identical
`to_dict()`. Enforced by construction: content-hashed ids, no wall-clock values
in the artifact, files processed in sorted path order, every collection sorted
before serialisation. Two tests assert it, including one that feeds the same
files in reverse order.

## Persistence

**None.** See [ADR-0009](decisions/ADR-0009-analysis-artifact-is-ephemeral.md):
`architecture.md` §16 defines no Phase 2 table, the artifact is reproducible
from stored inputs, and it carries third-party source text.

## Limitations

- Tree-sitter grammars must be installed for JS/TS/Java; without them those
  files report `PARSER_UNAVAILABLE`. Python always works (`ast` is stdlib).
- Only head-revision source is parsed, so deleted lines have no declaration
  context. Base-revision parsing is a future option; the mapper already has the
  `include_deletions` seam.
- Arrow functions assigned to a `const` take their name from the declarator;
  genuinely anonymous ones are `<anonymous>`.
- No cross-file resolution: no imports followed, no call graph, no types.
  Structural mapping only, which is what review context needs.

## Next

**Phase 3 — Gemini AI review, structured findings, finding validation and
GitHub inline comments.** The LineMap and `SourceFile.line()` built here are
what Phase 3's anchor validation runs against.
