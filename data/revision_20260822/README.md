# Controlled-experiment data — 2026-08-22

This directory contains derived statistics, independent audit summaries, and provenance for two controlled experiment families:

- `factorial_3x3/`: 3 head rules × 3 synchronization policies, 1,000 matched seeds per cell for each of two partition cases.
- `equal_budget/`: Adaptive versus FixedMatched under exactly equal total assigned pair-selection budget per Case×seed.

The full per-run outputs, matched schedules, and per-seed validation tables are reserved for the companion archival deposit. Their canonical SHA-256 values are recorded in the provenance files.

`gossip_budget_pair_draws` denotes the assigned synchronization-opportunity budget. It is distinct from packet-level traffic and from the legacy `gossip_pairs` counter, which is affected by the simulator's initial drop stage.
