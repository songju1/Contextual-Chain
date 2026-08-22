# Contextual Chain

This repository contains the public research release associated with the Contextual Chain systems study.

## Current controlled results

Contextual Chain is a **context-based protocol design principle**: synchronized participants use evolving operational context to make local decisions and to allocate recovery resources.

The current controlled experiments separate two questions: **how much synchronization is provisioned** and **when synchronization effort is applied**.

The 3×3 matched-schedule factorial shows that synchronization provisioning is the dominant recovery factor under the studied synthetic partition/rejoin conditions. The specific contextual head-selection rule tested here does not show a consistent independent advantage over `HeightOnly`.

A second, exact equal-total-assigned-budget control shows that synchronization **quantity alone does not explain the Adaptive result**. When the total assigned pair-selection budget is matched exactly for each Case×seed, context-triggered temporal allocation improves final agreement and shortens recovery relative to approximately uniform allocation in the tested setting.

For the Full rule, Adaptive versus FixedMatched gives:

- Case A: final agreement **+6.2 percentage points**; mean recovery **−10.8 s**.
- Case B: final agreement **+6.0 percentage points**; mean recovery **−14.0 s**.

These are simulation-level results under an oracle-assisted trigger. They are **not** claims of measured packet-level bandwidth or energy savings. This release also does not claim cryptographic authentication, post-quantum security, Byzantine security, or Sybil resistance.

## Repository layout

The earlier public simulator and experiment files remain in `src/`, `scripts/`, and `data/`.

Files added for the 2026-08-22 controlled-experiment release are:

- `release/contextual_chain_revision_code_20260822.tar.gz` — exact simulator, runners, analyses, and validation scripts used for the new controlled experiments
- `release/README.md` — contents and code-snapshot hashes for that bundle
- `data/revision_20260822/` — derived statistics, audits, and provenance
- `REPRODUCIBILITY.md` — design and reproduction notes
- `requirements-revision-20260822.txt` — pinned analysis dependencies
- `REVISION_CHECKSUMS.txt` — SHA-256 checksums of the newly released public files

The full per-run outputs, matched schedules, and per-seed structural-validation tables are intentionally kept out of Git history. They are intended for the companion archival deposit, whose persistent identifier can be added here after that deposit is created.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
