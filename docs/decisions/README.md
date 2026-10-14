# Architecture Decision Records

`architecture.md` captures the decisions made during the design phase. This
directory captures decisions made *since* — including any that reverse
something in the baseline.

An architecture document without an ADR trail becomes fiction within a month:
the code drifts, nobody records why, and the document quietly stops describing
the system. One short file per decision is the cheapest defence.

Format: context → decision → consequences → alternatives rejected.

## Index

| ADR | Decision | Phase |
|---|---|---|
| [0001](ADR-0001-ai-provider-abstraction.md) | AI provider abstraction; Gemini first, Claude a seam | 0 |
| [0002](ADR-0002-credentials-validated-at-use-not-at-load.md) | Credentials validated at use, not at settings load | 0 |
| [0003](ADR-0003-empty-baseline-migration.md) | An empty baseline migration | 0 |
| [0004](ADR-0004-dependency-minimalism.md) | Dependency minimalism | 0 |
| [0005](ADR-0005-versioned-webhook-path.md) | Versioned webhook path | 1 |
| [0006](ADR-0006-changed-files-keyed-by-commit.md) | `changed_files` keyed on the commit, not the run | 1 |
| [0007](ADR-0007-error-hierarchy-is-framework-free.md) | The error hierarchy imports no web framework | 1 |
| [0008](ADR-0008-linemap-is-ground-truth.md) | LineMap is ground truth | 2 |
| [0009](ADR-0009-analysis-artifact-is-ephemeral.md) | The analysis artifact is ephemeral | 2 |
| [0010](ADR-0010-model-may-claim-left-side.md) | The model may claim `side: LEFT`; the validator rejects it | 3 |
| [0011](ADR-0011-anchor-relocation-window.md) | Anchors relocate within ±3 lines, unambiguously | 3 |
| [0012](ADR-0012-persist-rejected-findings.md) | Rejected claims are persisted, not discarded | 3 |
