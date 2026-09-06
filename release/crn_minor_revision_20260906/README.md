# Contextual Chain CRN minor-revision package

This package contains the common-random-number (CRN) validation and equal-budget timing study added for the minor revision.

## Code

- `sim_context_chain_v6_crn.py`: separated CRN streams and fixed-time recovery monitoring
- `validate_crn.py`: trace and Adaptive-replay validation
- `run_equal_budget_crn.py`: Adaptive / FixedMatched / DisruptionWindowMatched central timing study
- `analyze_crn_full.py`: merge, validation, and paired statistical analysis

## Central design

- Proposal-arrival streams are independent per node.
- Block-nonce streams are independent per node.
- Broadcast-channel loss/delay uses a dedicated stream.
- Gossip source/destination/drop uses a dedicated stream with fixed consumption per assigned pair opportunity.
- Variable-length gossip-transfer draws are isolated within each opportunity.
- Recovery is evaluated on 1-second monitor ticks; first sustained recovery requires 30 consecutive seconds of all-head agreement.
- `DisruptionWindowMatched` preserves each Adaptive run's exact total assigned budget and exact low/high-rate tick counts, while using only known partition/rejoin boundaries and never the quarantine fraction.

## Validation and experiment

- CRN smoke validation passed for all tested Case×seed bundles.
- Central study: 2 cases × 1,000 seeds × 3 timing policies = 6,000 runs.
- Exact assigned-budget equality and proposal, broadcast-channel, and gossip-opportunity trace equality were validated across matched policies.
- The Adaptive-versus-FixedMatched result remained after explicit CRN control.
- The DisruptionWindowMatched control showed that disruption-focused temporal concentration explains most of the recovery-time advantage over approximately uniform timing.

The frozen earlier simulator and previously archived results are retained unchanged.
