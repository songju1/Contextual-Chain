# Reproducibility guide — controlled-experiment release 2026-08-22

## 1. 3×3 matched-schedule factorial

The controlled factorial crosses three head rules (`Full`, `HeightOnly`, `BranchScoreOnly`) with three synchronization policies (`FixedLow`, `FixedHigh`, `Adaptive`) in Case A (50/50 partition) and Case B (80/20 partition), using 1,000 matched seeds per cell.

For each Case×seed, the Adaptive low/high schedule is generated once by the Full rule and replayed identically across all three head rules. This isolates head-rule comparisons from differences in the Adaptive schedule itself.

The exact Step 7 simulator and runner are inside `release/contextual_chain_revision_code_20260822.tar.gz` under `archive/step7/`.

## 2. Equal-total-assigned-budget control

The second experiment compares `Adaptive` with `FixedMatched` for `Full` and `HeightOnly`, again in Cases A and B with 1,000 matched seeds per cell.

For every Case×seed, Adaptive and FixedMatched receive exactly the same **total assigned pair-selection budget**. FixedMatched spreads that realized total approximately uniformly over connected gossip ticks, whereas Adaptive concentrates it according to the contextual inconsistency signal.

The exact Step 10 simulator, runner, and statistical analysis are included in the release code bundle.

## 3. Public data in Git

`data/revision_20260822/` contains derived statistical tables, independent audit summaries, and provenance. The full per-run CSVs, exact matched schedules, and per-seed validation tables are reserved for the companion archival deposit to avoid placing large generated datasets into Git history.

The canonical raw-data SHA-256 values are recorded in the provenance files so that the archival deposit can be checked against the data used for the reported analyses.

## 4. Environment

The statistical analyses were run with Python 3.13.5 and:

```text
numpy==2.3.5
pandas==2.2.3
scipy==1.17.0
```

## 5. Interpretation boundary

The systems evidence supports two distinct findings:

1. Synchronization provisioning is a dominant determinant of recovery under the studied synthetic partition/rejoin conditions.
2. At the **same total assigned synchronization-opportunity budget**, context-triggered temporal allocation improves post-rejoin recovery relative to approximately uniform allocation in the tested setting.

The present specific contextual head-selection formula does not show a consistent independent recovery advantage over `HeightOnly`. Packet-level traffic, radio energy, a deployable distributed trigger, cryptographic authentication, post-quantum security, Byzantine security, and Sybil resistance are outside the demonstrated claims of this release.
