#!/usr/bin/env python3
import os
import sys
import csv
import math
import time
from multiprocessing import Pool
from pathlib import Path
from statistics import mean
from typing import List, Dict, Any, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sim_context_chain_v4 import simulate  # type: ignore


def getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return default if v is None or v == "" else int(v)


def getenv_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return default if v is None or v == "" else float(v)


def getenv_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v == "" else v


def parse_int_list_env(name: str, default: str) -> List[int]:
    raw = getenv_str(name, default)
    return [int(tok) for tok in raw.replace(",", " ").split()]


def parse_cases(raw: str, n_nodes: int) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    seen = set()
    for tok in raw.replace(",", " ").split():
        key = tok.strip()
        if not key:
            continue
        lower = key.lower()
        if lower in {"casea_50_50", "casea", "50_50", "50/50"}:
            label, a_size = "CaseA_50_50", n_nodes // 2
        elif lower in {"caseb_80_20", "caseb", "80_20", "80/20"}:
            label, a_size = "CaseB_80_20", int(round(0.8 * n_nodes))
        elif lower in {"casec_90_10", "casec", "90_10", "90/10"}:
            label, a_size = "CaseC_90_10", int(round(0.9 * n_nodes))
        else:
            raise ValueError(f"Unsupported case token: {key}")
        if label not in seen:
            out.append((label, a_size))
            seen.add(label)
    return out


def build_jobs() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    n_seeds = getenv_int("NSEEDS", 300)
    jobs = getenv_int("JOBS", max(1, os.cpu_count() or 1))
    n_nodes = getenv_int("N_NODES", 50)
    sim_time_s = getenv_float("SIM_TIME_S", 3600.0)
    block_interval_s = getenv_float("BLOCK_INTERVAL_S", 30.0)

    epoch_len_blocks = getenv_int("EPOCH_LEN_BLOCKS", 30)
    cp_epoch_mode = getenv_str("CP_EPOCH_MODE", "height")
    cp_tiebreak = getenv_str("CP_TIEBREAK", "none")

    net_mode = getenv_str("NET", "noisy").lower()
    if net_mode == "noisy":
        net_delay_mean = getenv_float("NET_DELAY_MEAN", 0.80)
        net_delay_jitter = getenv_float("NET_DELAY_JITTER", 0.20)
        net_drop_p = getenv_float("NET_DROP_P", 0.02)
    elif net_mode == "clean":
        net_delay_mean = getenv_float("NET_DELAY_MEAN", 0.25)
        net_delay_jitter = getenv_float("NET_DELAY_JITTER", 0.10)
        net_drop_p = getenv_float("NET_DROP_P", 0.00)
    else:
        raise ValueError(f"Unsupported NET={net_mode}; expected clean or noisy")

    l_score_window = getenv_int("L_SCORE_WINDOW", 40)
    k_converge = getenv_int("K_CONVERGE", 30)
    block_bytes_est = getenv_int("BLOCK_BYTES_EST", 250)

    cases = parse_cases(getenv_str("EXTRA_CASES", "CaseA_50_50 CaseB_80_20"), n_nodes)
    fixed_budgets = parse_int_list_env("FIXED_BUDGETS", "4 8 12 16")
    quar_budgets = parse_int_list_env("QUAR_BUDGETS", "4 8 12 16")

    variants: List[Tuple[str, bool, int, int]] = [
        ("NoQ_1_1", False, 1, 1),
        ("Q_only_1_1", True, 1, 1),
    ]
    for b in fixed_budgets:
        variants.append((f"Gossip_only_{b}_{b}", False, b, b))
    for b in quar_budgets:
        variants.append((f"Both_1_{b}", True, 1, b))

    common = {
        "n_nodes": n_nodes,
        "block_interval_s": block_interval_s,
        "sim_time_s": sim_time_s,
        "epoch_len_blocks": epoch_len_blocks,
        "cp_epoch_mode": cp_epoch_mode,
        "cp_tiebreak": cp_tiebreak,
        "L_score_window": l_score_window,
        "K_converge": k_converge,
        "net_delay_mean": net_delay_mean,
        "net_delay_jitter": net_delay_jitter,
        "net_drop_p": net_drop_p,
        "block_bytes_est": block_bytes_est,
    }

    jobs_out: List[Dict[str, Any]] = []
    for case_label, a_size in cases:
        for variant_label, use_quarantine, gp_norm, gp_quar in variants:
            for seed in range(n_seeds):
                jobs_out.append({
                    "case": case_label,
                    "variant": variant_label,
                    "A_size": a_size,
                    "use_quarantine": use_quarantine,
                    "gossip_pairs_normal": gp_norm,
                    "gossip_pairs_quarantine": gp_quar,
                    "seed": seed,
                    **common,
                })

    meta = {
        "n_seeds": n_seeds,
        "jobs": jobs,
        "cases": cases,
        "variants": variants,
        **common,
    }
    return jobs_out, meta


def run_one(job: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    result = simulate(
        n_nodes=job["n_nodes"],
        block_interval_s=job["block_interval_s"],
        sim_time_s=job["sim_time_s"],
        partition=(1200.0, 2400.0),
        A_size=job["A_size"],
        epoch_len_blocks=job["epoch_len_blocks"],
        cp_epoch_mode=job["cp_epoch_mode"],
        cp_tiebreak=job["cp_tiebreak"],
        L_score_window=job["L_score_window"],
        K_converge=job["K_converge"],
        use_quarantine=job["use_quarantine"],
        seed=job["seed"],
        net_delay_mean=job["net_delay_mean"],
        net_delay_jitter=job["net_delay_jitter"],
        net_drop_p=job["net_drop_p"],
        gossip_tick_s=1.0,
        gossip_pairs_normal=job["gossip_pairs_normal"],
        gossip_pairs_quarantine=job["gossip_pairs_quarantine"],
        block_bytes_est=job["block_bytes_est"],
    )
    t1 = time.perf_counter()
    row = dict(job)
    row.update(result)
    row["runtime_mean_ms"] = (t1 - t0) * 1000.0
    row["success_end_rate"] = 1.0 if result["heads_equal_end"] else 0.0
    row["recovered"] = 1.0 if result["recovery_time_s"] is not None else 0.0
    return row


def percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    idx = (len(xs) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return xs[lo]
    frac = idx - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def summarize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["case"], r["variant"]), []).append(r)

    out: List[Dict[str, Any]] = []
    for (case, variant), grp in sorted(groups.items()):
        rec_times = [r["recovery_time_s"] for r in grp if r["recovery_time_s"] is not None]
        out.append({
            "case": case,
            "variant": variant,
            "n_runs": len(grp),
            "n_nodes": grp[0]["n_nodes"],
            "use_quarantine": grp[0]["use_quarantine"],
            "gossip_pairs_normal": grp[0]["gossip_pairs_normal"],
            "gossip_pairs_quarantine": grp[0]["gossip_pairs_quarantine"],
            "net_delay_mean": grp[0]["net_delay_mean"],
            "net_delay_jitter": grp[0]["net_delay_jitter"],
            "net_drop_p": grp[0]["net_drop_p"],
            "epoch_len_blocks": grp[0]["epoch_len_blocks"],
            "cp_epoch_mode": grp[0]["cp_epoch_mode"],
            "cp_tiebreak": grp[0]["cp_tiebreak"],
            "success_end_rate": mean([r["success_end_rate"] for r in grp]),
            "recovered_rate": mean([r["recovered"] for r in grp]),
            "recovery_mean_s": mean(rec_times) if rec_times else float("nan"),
            "recovery_p50_s": percentile(rec_times, 0.50) if rec_times else float("nan"),
            "recovery_p95_s": percentile(rec_times, 0.95) if rec_times else float("nan"),
            "runtime_mean_ms": mean([r["runtime_mean_ms"] for r in grp]),
            "gossip_pairs_mean": mean([r["gossip_pairs"] for r in grp]),
            "gossip_blocks_sent_mean": mean([r["gossip_blocks_sent"] for r in grp]),
            "broadcast_blocks_sent_mean": mean([r["broadcast_blocks_sent"] for r in grp]),
            "total_bytes_est_mean": mean([r["total_bytes_est"] for r in grp]),
            "max_reorg_depth_mean": mean([r["max_reorg_depth"] for r in grp]),
            "max_fork_peak_mean": mean([r["max_fork_peak"] for r in grp]),
            "max_equivocations_total_max_mean": mean([r["max_equivocations_total_max"] for r in grp]),
        })
    return out


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No rows to write: {path}")
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    outdir = Path(getenv_str("OUTDIR", str(SCRIPT_DIR / f"n50_budget_study_{time.strftime('%Y%m%d_%H%M%S')}"))).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    jobs_list, meta = build_jobs()

    print("[INFO] Running N=50 budget study")
    print(f"[INFO] outdir={outdir}")
    print(f"[INFO] n_runs={len(jobs_list)} n_seeds={meta['n_seeds']} jobs={meta['jobs']}")
    print(f"[INFO] cases={meta['cases']}")
    print(f"[INFO] variants={[v[0] for v in meta['variants']]}")

    t0 = time.perf_counter()
    with Pool(processes=meta["jobs"]) as pool:
        rows = list(pool.imap_unordered(run_one, jobs_list, chunksize=8))
    t1 = time.perf_counter()

    rows_sorted = sorted(rows, key=lambda r: (r["case"], r["variant"], r["seed"]))
    summary_rows = summarize(rows_sorted)

    write_csv(outdir / "raw_n50_budget_study.csv", rows_sorted)
    write_csv(outdir / "summary_n50_budget_study.csv", summary_rows)
    with (outdir / "provenance.txt").open("w") as f:
        f.write("N=50 budget study\n")
        for k, v in meta.items():
            f.write(f"{k}: {v}\n")
        f.write(f"wall_clock_s: {t1 - t0:.3f}\n")

    print(f"[INFO] wrote: {outdir / 'raw_n50_budget_study.csv'}")
    print(f"[INFO] wrote: {outdir / 'summary_n50_budget_study.csv'}")
    print(f"[INFO] wrote: {outdir / 'provenance.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
