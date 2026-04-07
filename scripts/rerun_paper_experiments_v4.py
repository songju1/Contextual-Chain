#!/usr/bin/env python3
"""
rerun_paper_experiments.py

Re-runs sim_context_chain_v4.py under multiple suites and writes:
  (1) per-run CSV (one row per seed)
  (2) aggregated summary CSV (mean/p50/p95 etc.)

Designed for macOS + Python 3.11. No third-party deps.

Usage examples:
  python rerun_paper_experiments.py --suite main_core --outdir out --n-seeds 1000 --epoch-len 30
  python rerun_paper_experiments.py --suite all --outdir out --n-seeds 1000 --epoch-len 30 600 --jobs 6

Notes:
- In sim_context_chain_v4.py, checkpoint level increments only when chain height hits multiples of epoch_len_blocks.
  If epoch_len_blocks is too large compared to expected total blocks in SIM_TIME, checkpointing never triggers.
  This script prints a warning when epoch_len_blocks > expected_total_blocks.
"""
from __future__ import annotations

import argparse, csv, os, time, math
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple
from multiprocessing import Pool, cpu_count

# Import your simulator (must be in the same directory, or on PYTHONPATH)
from sim_context_chain_v4 import simulate

# ------------------------ helpers ------------------------
def pct(sorted_vals: List[float], p: float) -> Optional[float]:
    """Percentile with linear interpolation. p in [0,100]. Returns None if empty."""
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

# ------------------------ experiment definitions ------------------------
@dataclass(frozen=True)
class Exp:
    suite: str
    scenario: str
    n_nodes: int
    ratio: float                 # A fraction in [0,1]
    net_name: str                # "clean" or "noisy"
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
    Variant("NoQ",        use_quarantine=False, gossip_pairs_normal=1, gossip_pairs_quarantine=1),
    Variant("Q_only",     use_quarantine=True,  gossip_pairs_normal=1, gossip_pairs_quarantine=1),
    Variant("Gossip_only",use_quarantine=False, gossip_pairs_normal=4, gossip_pairs_quarantine=4),
    Variant("Both",       use_quarantine=True,  gossip_pairs_normal=1, gossip_pairs_quarantine=4),
]

NETS: Dict[str, Dict[str, float]] = {
    "clean": {"net_delay_mean": 0.25, "net_delay_jitter": 0.10, "net_drop_p": 0.00},
    "noisy": {"net_delay_mean": 0.80, "net_delay_jitter": 0.20, "net_drop_p": 0.02},
}

def make_exps(which: str) -> List[Exp]:
    """Return experiments for a given suite name."""
    P0, P1 = 1200.0, 2400.0
    if which == "main_core":
        return [
            Exp("main_core", "CaseA_50_50", 20, 0.50, "clean", 3600.0, 30.0, P0, P1),
            Exp("main_core", "CaseA_50_50", 20, 0.50, "noisy", 3600.0, 30.0, P0, P1),
            Exp("main_core", "CaseB_80_20", 20, 0.80, "clean", 3600.0, 30.0, P0, P1),
            Exp("main_core", "CaseB_80_20", 20, 0.80, "noisy", 3600.0, 30.0, P0, P1),
        ]
    if which == "ratios_noisy":
        return [
            Exp("ratios_noisy", "CaseA_50_50", 20, 0.50, "noisy", 3600.0, 30.0, P0, P1),
            Exp("ratios_noisy", "CaseB_80_20", 20, 0.80, "noisy", 3600.0, 30.0, P0, P1),
            Exp("ratios_noisy", "CaseC_90_10", 20, 0.90, "noisy", 3600.0, 30.0, P0, P1),
        ]
    if which == "scaling_caseA_noisy":
        return [
            Exp("scaling_caseA_noisy", "CaseA_50_50", 20, 0.50, "noisy", 3600.0, 30.0, P0, P1),
            Exp("scaling_caseA_noisy", "CaseA_50_50", 50, 0.50, "noisy", 3600.0, 30.0, P0, P1),
            Exp("scaling_caseA_noisy", "CaseA_50_50", 100,0.50, "noisy", 3600.0, 30.0, P0, P1),
        ]
    if which == "longtime_noisy":
        return [
            Exp("longtime_noisy", "CaseA_50_50", 20, 0.50, "noisy", 5400.0, 30.0, P0, P1),
            Exp("longtime_noisy", "CaseB_80_20", 20, 0.80, "noisy", 5400.0, 30.0, P0, P1),
        ]
    if which == "epoch_sanity":
        # Single scenario; used to validate whether checkpointing ever triggers in your horizon.
        return [
            Exp("epoch_sanity", "CaseA_50_50", 20, 0.50, "noisy", 3600.0, 30.0, P0, P1),
        ]
    if which == "all":
        suites = ["main_core","ratios_noisy","scaling_caseA_noisy","longtime_noisy","epoch_sanity"]
        out: List[Exp] = []
        for s in suites:
            out.extend(make_exps(s))
        return out
    raise ValueError(f"Unknown suite: {which}")

# ------------------------ runner ------------------------
def one_run(args: Tuple[Exp, Variant, int, int, int, int, str, str, float, int]) -> Dict[str, Any]:
    exp, var, epoch_len_blocks, L_score_window, K_converge, seed, cp_epoch_mode, cp_tiebreak, epoch_len_s, block_bytes_est = args
    # derived
    A_size = max(1, min(exp.n_nodes-1, int(round(exp.n_nodes * exp.ratio))))
    # run
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
        epoch_len_s=((epoch_len_s if epoch_len_s>0 else None) if (cp_epoch_mode=="time") else None),
        block_bytes_est=block_bytes_est,
        L_score_window=L_score_window,
        K_converge=K_converge,
        use_quarantine=var.use_quarantine,
        seed=seed,
        gossip_pairs_normal=var.gossip_pairs_normal,
        gossip_pairs_quarantine=var.gossip_pairs_quarantine,
        **NETS[exp.net_name],
    )
    t1 = time.perf_counter()
    # flatten row
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
    }
    # Merge simulator outputs (may include use_quarantine again; overwrite is fine via update)
    row.update(out)
    # Derived indicators
    row["success_end"] = safe_bool(row.get("heads_equal_end", False))
    row["success_converged"] = safe_bool(row.get("converged_at_s", None) is not None)
    return row

def summarize(rows: List[Dict[str, Any]], group_keys: List[str]) -> List[Dict[str, Any]]:
    # group
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for r in rows:
        k = tuple(r[g] for g in group_keys)
        groups.setdefault(k, []).append(r)

    out_rows: List[Dict[str, Any]] = []
    for k, rs in groups.items():
        # base
        base = {group_keys[i]: k[i] for i in range(len(group_keys))}
        n = len(rs)
        base["n_runs"] = n

        # success rates
        base["success_end_rate"] = sum(int(r["success_end"]) for r in rs) / n
        base["success_converged_rate"] = sum(int(r["success_converged"]) for r in rs) / n

        # recovery stats (only when available)
        rec = [float(r["recovery_time_s"]) for r in rs if r.get("recovery_time_s") is not None]
        rec.sort()
        base["recovery_mean_s"] = mean(rec)
        base["recovery_p50_s"]  = pct(rec, 50)
        base["recovery_p95_s"]  = pct(rec, 95)

        # runtime stats
        rt = [float(r["runtime_ms"]) for r in rs if r.get("runtime_ms") is not None]
        rt.sort()
        base["runtime_mean_ms"] = mean(rt)
        base["runtime_p50_ms"]  = pct(rt, 50)
        base["runtime_p95_ms"]  = pct(rt, 95)

        # bandwidth proxies (mean/p50/p95)
        for key in ["broadcast_blocks_sent","gossip_blocks_sent","gossip_pairs","total_bytes_est"]:
            vals = [float(r.get(key, 0.0)) for r in rs]
            vals.sort()
            base[key + "_mean"] = mean(vals)
            base[key + "_p50"]  = pct(vals, 50)
            base[key + "_p95"]  = pct(vals, 95)

        # fork/reorg/equiv maxima (mean + p95)
        for key in ["max_reorg_depth","max_fork_peak","max_equivocations_total_max","nodes_quarantine_end"]:
            vals = [float(r[key]) for r in rs if r.get(key) is not None]
            vals.sort()
            base[key + "_mean"] = mean(vals)
            base[key + "_p95"]  = pct(vals, 95)

        out_rows.append(base)

    # stable sort
    out_rows.sort(key=lambda d: tuple(d[g] for g in group_keys))
    return out_rows

def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("No rows to write")
    fields = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def warn_epoch(exp: Exp, epoch_len_blocks: int) -> None:
    expected_blocks = exp.sim_time_s / exp.block_interval_s
    # expected chain height roughly expected_blocks, but can be slightly higher/lower.
    if epoch_len_blocks > expected_blocks * 1.2:
        print(f"[WARN] epoch_len_blocks={epoch_len_blocks} is large vs expected chain height ~{expected_blocks:.1f} "
              f"(sim_time={exp.sim_time_s}, block_interval={exp.block_interval_s}). "
              f"Checkpointing may never trigger. Consider epoch_len 20–60 for 1-hour sims.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all",
                    choices=["main_core","ratios_noisy","scaling_caseA_noisy","longtime_noisy","epoch_sanity","all"])
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--n-seeds", type=int, default=1000)
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--epoch-len", type=int, nargs="+", default=[30],
                    help="One or more epoch_len_blocks values to run (e.g., 30 600).")
    ap.add_argument("--cp-epoch-mode", default="height", choices=["height","time"],
                    help="Checkpoint level semantics: height-based (v3) or time-based (v4).")
    ap.add_argument("--cp-tiebreak", default="none", choices=["none","stick","stick_q"],
                    help="Optional cp_hash stickiness in fork-choice (v4).")
    ap.add_argument("--epoch-len-s", type=float, default=None,
                    help="If set and --cp-epoch-mode=time, use this epoch length in seconds. If None, epoch_len_blocks*block_interval_s is used.")
    ap.add_argument("--block-bytes-est", type=int, default=250,
                    help="Bytes per block-unit for bandwidth proxy estimates.")
    ap.add_argument("--L-score-window", type=int, default=40)
    ap.add_argument("--K-converge", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=max(1, min(8, cpu_count()//2)))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ensure_dir(args.outdir)

    exps = make_exps(args.suite)

    # warn about epoch lengths
    for exp in exps:
        for e in args.epoch_len:
            warn_epoch(exp, e)

    # build task list
    tasks: List[Tuple[Exp, Variant, int, int, int, int, str, str, float, int]] = []
    for exp in exps:
        for e in args.epoch_len:
            # epoch sanity suite: only run Both by default to save time (but still configurable in code if needed)
            variants = VARIANTS
            if exp.suite == "epoch_sanity":
                variants = [v for v in VARIANTS if v.name in ("NoQ","Both")]
            for var in variants:
                for seed in range(args.seed_start, args.seed_start + args.n_seeds):
                    tasks.append((exp, var, e, args.L_score_window, args.K_converge, seed, args.cp_epoch_mode, args.cp_tiebreak, (args.epoch_len_s or 0.0), args.block_bytes_est))

    ts = time.strftime("%Y%m%d_%H%M%S")
    tag = (args.tag + "_") if args.tag else ""
    raw_path = os.path.join(args.outdir, f"{tag}runs_{args.suite}_{ts}.csv")
    sum_path = os.path.join(args.outdir, f"{tag}summary_{args.suite}_{ts}.csv")

    print(f"[INFO] tasks={len(tasks)}  jobs={args.jobs}")
    print(f"[INFO] raw={raw_path}")
    print(f"[INFO] summary={sum_path}")

    if args.dry_run:
        return

    # execute
    rows: List[Dict[str, Any]] = []
    if args.jobs <= 1:
        for t in tasks:
            rows.append(one_run(t))
    else:
        with Pool(processes=args.jobs) as pool:
            for r in pool.imap_unordered(one_run, tasks, chunksize=20):
                rows.append(r)

    # write raw
    write_csv(raw_path, rows)

    # summarize
    group_keys = ["suite","scenario","n_nodes","ratio","net","epoch_len_blocks","variant","use_quarantine","gossip_pairs_normal","gossip_pairs_quarantine","K_converge","L_score_window","sim_time_s","block_interval_s","partition_start_s","partition_end_s"]
    summary_rows = summarize(rows, group_keys)
    write_csv(sum_path, summary_rows)

    print("[DONE] wrote raw + summary")

if __name__ == "__main__":
    main()
