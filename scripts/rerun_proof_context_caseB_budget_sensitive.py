#!/usr/bin/env python3
"""Run only the Extension-B budget-sensitive validation on CaseB (80/20, noisy).

This is a file-based runner to avoid the macOS/Anaconda multiprocessing issue that
appears when using inline Python via stdin.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import time
from dataclasses import dataclass
from multiprocessing import Pool, cpu_count
from typing import Any, Dict, List, Optional, Tuple

from sim_context_chain_extB_budget_sensitive import simulate


def pct(sorted_vals: List[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 100:
        return float(sorted_vals[-1])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return float(d0 + d1)


def mean(vals: List[float]) -> Optional[float]:
    return (sum(vals) / len(vals)) if vals else None


def safe_bool(x: Any) -> int:
    return 1 if x else 0


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


@dataclass(frozen=True)
class Exp:
    suite: str
    scenario: str
    n_nodes: int
    ratio: float
    net_name: str
    sim_time_s: float
    block_interval_s: float
    partition_start_s: float
    partition_end_s: float


@dataclass(frozen=True)
class Variant:
    name: str
    use_quarantine: bool
    gossip_pairs_normal: int
    gossip_pairs_quarantine: int


VARIANTS: List[Variant] = [
    Variant("NoQ", use_quarantine=False, gossip_pairs_normal=1, gossip_pairs_quarantine=1),
    Variant("Q_only", use_quarantine=True, gossip_pairs_normal=1, gossip_pairs_quarantine=1),
    Variant("Gossip_only", use_quarantine=False, gossip_pairs_normal=4, gossip_pairs_quarantine=4),
    Variant("Both", use_quarantine=True, gossip_pairs_normal=1, gossip_pairs_quarantine=4),
]

NETS: Dict[str, Dict[str, float]] = {
    "clean": {"net_delay_mean": 0.25, "net_delay_jitter": 0.10, "net_drop_p": 0.00},
    "noisy": {"net_delay_mean": 0.80, "net_delay_jitter": 0.20, "net_drop_p": 0.02},
}


def make_exps() -> List[Exp]:
    P0, P1 = 1200.0, 2400.0
    return [
        Exp("proof_budget_caseB_noisy", "CaseB_80_20", 20, 0.80, "noisy", 3600.0, 30.0, P0, P1),
    ]


def select_variants(mode: str) -> List[Variant]:
    if mode == "both":
        return [v for v in VARIANTS if v.name == "Both"]
    if mode == "all":
        return VARIANTS
    raise ValueError(f"Unknown variants mode: {mode}")


def one_run(args: Tuple[Exp, Variant, int, int, int, int, str, str, float, int, int, int, int, float, str, float, str, int, int]) -> Dict[str, Any]:
    (
        exp,
        var,
        epoch_len_blocks,
        L_score_window,
        K_converge,
        seed,
        cp_epoch_mode,
        cp_tiebreak,
        epoch_len_s,
        block_bytes_est,
        proof_mem_cells,
        proof_cells_per_cp,
        proof_cell_bytes,
        proof_tick_s,
        proof_trigger_mode,
        proof_trigger_q_frac,
        proof_challenge_mode,
        proof_challenge_k,
        attacker_budget_contexts,
    ) = args

    A_size = max(1, min(exp.n_nodes - 1, int(round(exp.n_nodes * exp.ratio))))
    t0 = time.perf_counter()
    out = simulate(
        n_nodes=exp.n_nodes,
        block_interval_s=exp.block_interval_s,
        sim_time_s=exp.sim_time_s,
        partition=(exp.partition_start_s, exp.partition_end_s),
        A_size=A_size,
        epoch_len_blocks=epoch_len_blocks,
        cp_epoch_mode=cp_epoch_mode,
        cp_tiebreak=cp_tiebreak,
        epoch_len_s=((epoch_len_s if epoch_len_s > 0 else None) if (cp_epoch_mode == "time") else None),
        block_bytes_est=block_bytes_est,
        L_score_window=L_score_window,
        K_converge=K_converge,
        use_quarantine=var.use_quarantine,
        seed=seed,
        gossip_pairs_normal=var.gossip_pairs_normal,
        gossip_pairs_quarantine=var.gossip_pairs_quarantine,
        proof_mem_cells=proof_mem_cells,
        proof_cells_per_cp=proof_cells_per_cp,
        proof_cell_bytes=proof_cell_bytes,
        proof_tick_s=proof_tick_s,
        proof_trigger_mode=proof_trigger_mode,
        proof_trigger_q_frac=proof_trigger_q_frac,
        proof_challenge_mode=proof_challenge_mode,
        proof_challenge_k=proof_challenge_k,
        attacker_budget_contexts=attacker_budget_contexts,
        **NETS[exp.net_name],
    )
    t1 = time.perf_counter()

    row: Dict[str, Any] = {
        "suite": exp.suite,
        "scenario": exp.scenario,
        "n_nodes": exp.n_nodes,
        "ratio": exp.ratio,
        "A_size": A_size,
        "net": exp.net_name,
        "sim_time_s": exp.sim_time_s,
        "block_interval_s": exp.block_interval_s,
        "partition_start_s": exp.partition_start_s,
        "partition_end_s": exp.partition_end_s,
        "epoch_len_blocks": epoch_len_blocks,
        "L_score_window": L_score_window,
        "K_converge": K_converge,
        "variant": var.name,
        "use_quarantine": var.use_quarantine,
        "gossip_pairs_normal": var.gossip_pairs_normal,
        "gossip_pairs_quarantine": var.gossip_pairs_quarantine,
        "seed": seed,
        "runtime_ms": (t1 - t0) * 1000.0,
        "attacker_budget_contexts": attacker_budget_contexts,
        "proof_mem_cells": proof_mem_cells,
        "proof_cells_per_cp": proof_cells_per_cp,
        "proof_cell_bytes": proof_cell_bytes,
        "proof_tick_s": proof_tick_s,
        "proof_trigger_mode": proof_trigger_mode,
        "proof_trigger_q_frac": proof_trigger_q_frac,
        "proof_challenge_mode": proof_challenge_mode,
        "proof_challenge_k": proof_challenge_k,
    }
    row.update(out)
    row["success_end"] = safe_bool(row.get("heads_equal_end", False))
    row["success_converged"] = safe_bool(row.get("converged_at_s", None) is not None)
    return row


def summarize(rows: List[Dict[str, Any]], group_keys: List[str]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(tuple(r[g] for g in group_keys), []).append(r)

    out_rows: List[Dict[str, Any]] = []
    for k, rs in groups.items():
        base = {group_keys[i]: k[i] for i in range(len(group_keys))}
        n = len(rs)
        base["n_runs"] = n
        base["success_end_rate"] = sum(int(r["success_end"]) for r in rs) / n
        base["success_converged_rate"] = sum(int(r["success_converged"]) for r in rs) / n
        for key in [
            "attacker_success_end_prob",
            "attacker_success_rejoin_prob",
            "recovery_time_s",
            "runtime_ms",
            "contexts_required_peak",
            "contexts_required_mean",
            "required_mem_peak_bytes",
            "stored_mem_peak_bytes",
            "honest_mem_peak_bytes",
            "attacker_to_honest_mem_ratio_peak",
            "rejoin_contexts_required",
            "rejoin_mem_required_bytes",
            "rejoin_challenge_targets",
            "final_contexts_required",
            "challenge_targets_peak",
            "challenge_targets_mean",
            "challenge_targets_final",
            "attacker_challenge_success_prob_mean",
            "proof_challenge_count",
            "total_bytes_est",
            "gossip_blocks_sent",
            "gossip_pairs",
            "max_fork_peak",
            "max_reorg_depth",
            "max_equivocations_total_max",
        ]:
            vals = [float(r[key]) for r in rs if r.get(key) is not None]
            vals.sort()
            base[key + "_mean"] = mean(vals)
            base[key + "_p50"] = pct(vals, 50)
            base[key + "_p95"] = pct(vals, 95)

        out_rows.append(base)

    out_rows.sort(key=lambda d: tuple(d[g] for g in group_keys))
    return out_rows


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("No rows to write")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def warn_epoch(exp: Exp, epoch_len_blocks: int) -> None:
    expected_blocks = exp.sim_time_s / exp.block_interval_s
    if epoch_len_blocks > expected_blocks * 1.2:
        print(
            f"[WARN] epoch_len_blocks={epoch_len_blocks} is large vs expected chain height ~{expected_blocks:.1f} "
            f"(sim_time={exp.sim_time_s}, block_interval={exp.block_interval_s}). "
            f"Checkpointing may never trigger. Consider epoch_len 20-60 for 1-hour sims."
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--n-seeds", type=int, default=200)
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--epoch-len", type=int, nargs="+", default=[30])
    ap.add_argument("--cp-epoch-mode", default="height", choices=["height", "time"])
    ap.add_argument("--cp-tiebreak", default="none", choices=["none", "stick", "stick_q"])
    ap.add_argument("--epoch-len-s", type=float, default=None)
    ap.add_argument("--block-bytes-est", type=int, default=250)
    ap.add_argument("--L-score-window", type=int, default=40)
    ap.add_argument("--K-converge", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=max(1, min(8, cpu_count() // 2)))
    ap.add_argument("--variants-mode", default="both", choices=["both", "all"])
    ap.add_argument("--attacker-budget-contexts", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    ap.add_argument("--proof-mem-cells", type=int, default=16384)
    ap.add_argument("--proof-cells-per-cp", type=int, default=2048)
    ap.add_argument("--proof-cell-bytes", type=int, default=32)
    ap.add_argument("--proof-tick-s", type=float, default=5.0)
    ap.add_argument("--proof-trigger-mode", default="quarantine_or_rejoin", choices=["quarantine", "rejoin", "quarantine_or_rejoin", "always"])
    ap.add_argument("--proof-trigger-q-frac", type=float, default=0.25)
    ap.add_argument("--proof-challenge-mode", default="uniform_active", choices=["canonical", "uniform_active", "all_active"])
    ap.add_argument("--proof-challenge-k", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ensure_dir(args.outdir)
    exps = make_exps()
    variants = select_variants(args.variants_mode)

    for exp in exps:
        for e in args.epoch_len:
            warn_epoch(exp, e)

    tasks: List[Tuple[Exp, Variant, int, int, int, int, str, str, float, int, int, int, int, float, str, float, str, int, int]] = []
    for exp in exps:
        for e in args.epoch_len:
            for var in variants:
                for budget in args.attacker_budget_contexts:
                    for seed in range(args.seed_start, args.seed_start + args.n_seeds):
                        tasks.append((
                            exp,
                            var,
                            e,
                            args.L_score_window,
                            args.K_converge,
                            seed,
                            args.cp_epoch_mode,
                            args.cp_tiebreak,
                            (args.epoch_len_s or 0.0),
                            args.block_bytes_est,
                            args.proof_mem_cells,
                            args.proof_cells_per_cp,
                            args.proof_cell_bytes,
                            args.proof_tick_s,
                            args.proof_trigger_mode,
                            args.proof_trigger_q_frac,
                            args.proof_challenge_mode,
                            args.proof_challenge_k,
                            budget,
                        ))

    ts = time.strftime("%Y%m%d_%H%M%S")
    tag = (args.tag + "_") if args.tag else ""
    raw_path = os.path.join(args.outdir, f"{tag}runs_proof_budget_caseB_noisy_{ts}.csv")
    sum_path = os.path.join(args.outdir, f"{tag}summary_proof_budget_caseB_noisy_{ts}.csv")

    print(f"[INFO] tasks={len(tasks)} jobs={args.jobs}")
    print(f"[INFO] raw={raw_path}")
    print(f"[INFO] summary={sum_path}")

    if args.dry_run:
        return

    rows: List[Dict[str, Any]] = []
    if args.jobs <= 1:
        for task in tasks:
            rows.append(one_run(task))
    else:
        with Pool(processes=args.jobs) as pool:
            for row in pool.imap_unordered(one_run, tasks, chunksize=20):
                rows.append(row)

    write_csv(raw_path, rows)
    group_keys = [
        "suite",
        "scenario",
        "n_nodes",
        "ratio",
        "net",
        "epoch_len_blocks",
        "variant",
        "use_quarantine",
        "gossip_pairs_normal",
        "gossip_pairs_quarantine",
        "K_converge",
        "L_score_window",
        "sim_time_s",
        "block_interval_s",
        "partition_start_s",
        "partition_end_s",
        "attacker_budget_contexts",
        "proof_mem_cells",
        "proof_cells_per_cp",
        "proof_cell_bytes",
        "proof_tick_s",
        "proof_trigger_mode",
        "proof_trigger_q_frac",
        "proof_challenge_mode",
        "proof_challenge_k",
    ]
    summary_rows = summarize(rows, group_keys)
    write_csv(sum_path, summary_rows)
    print("[DONE] wrote raw + summary")


if __name__ == "__main__":
    main()
