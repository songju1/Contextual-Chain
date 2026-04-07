#!/usr/bin/env python3
"""
Executable simulation model for authentication extension B / idea 2.

This file starts from the current v4 contextual-authentication simulator and adds a
*shadow* proof-of-context layer intended to estimate the adversary's contextual
memory burden.

What is modeled here:
- Each block carries a branch-specific context commitment root `ctx_root`.
- A budget-limited attacker tries to stay prepared for any *currently plausible*
  honest context by storing one proof-memory object per distinct active head root.
- Proof checks are triggered periodically when inconsistency is high (quarantine),
  after rejoin while multiple heads remain active, or always, depending on mode.
- Challenge success is evaluated with a *budget-sensitive* rule: instead of asking
  only for the current majority/canonical context, the checker can require
  coverage over uniformly sampled active contexts or over all currently plausible
  contexts.
- The simulator reports peak required contexts/bytes, stored contexts under a
  chosen attacker budget, and challenge success probabilities under the selected
  challenge rule.

What is *not* claimed here:
- This is not a cryptographic security proof.
- The proof object is abstracted as a per-context memory array of configurable size;
  the code does not implement a real DRG/Merkle proof system.
- The challenge rule is still a simulator abstraction; it is stronger than the
  earlier canonical-only check, but it is not yet a full cryptographic protocol.
- Honest-node fork choice remains the same as in v4; the proof layer is evaluated
  in shadow mode so you can quantify the memory-side story before deciding on tone.
"""
from __future__ import annotations

import hashlib
import heapq
import math
from math import comb
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ----------------- Utilities -----------------
def H(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def hx(b: bytes) -> str:
    return b.hex()


# ----------------- Block -----------------
@dataclass(frozen=True)
class Block:
    bid: str
    height: int
    prev: str
    proposer: int
    time: float
    cp_level: int
    cp_hash: str      # checkpoint commitment (existing v4 notion)
    ctx_root: str     # extension-B branch-specific context commitment


# ----------------- Event -----------------
@dataclass(order=True)
class Event:
    time: float
    kind: str = field(compare=False)   # "propose" | "deliver" | "gossip_tick" | "proof_tick"
    src: int = field(compare=False)
    dst: int = field(compare=False, default=-1)
    block: Optional[Block] = field(compare=False, default=None)


# ----------------- Network -----------------
class Network:
    def __init__(self, n: int, delay_mean: float = 0.25, delay_jitter: float = 0.10, drop_p: float = 0.0):
        self.n = n
        self.delay_mean = delay_mean
        self.delay_jitter = delay_jitter
        self.drop_p = drop_p
        self.partition_A: Set[int] = set()
        self.partition_B: Set[int] = set()
        self.partition_on = False

    def set_partition(self, A: Set[int], B: Set[int], on: bool):
        self.partition_A, self.partition_B, self.partition_on = A, B, on

    def can_send(self, a: int, b: int) -> bool:
        if not self.partition_on:
            return True
        inA = a in self.partition_A
        inB = a in self.partition_B
        jnA = b in self.partition_A
        jnB = b in self.partition_B
        if (inA and jnB) or (inB and jnA):
            return False
        return True

    def sample_delay(self) -> float:
        d = random.gauss(self.delay_mean, self.delay_jitter)
        return max(0.0, d)

    def dropped(self) -> bool:
        return random.random() < self.drop_p


# ----------------- Node -----------------
class Node:
    def __init__(self, nid: int, genesis: Block, recent_window: int = 200, cp_tiebreak: str = "none"):
        self.nid = nid
        self.blocks: Dict[str, Block] = {genesis.bid: genesis}
        self.children: Dict[str, List[str]] = {genesis.bid: []}
        self.head: str = genesis.bid

        # checkpoint tie-break behavior:
        # - "none": ignore cp_hash
        # - "stick": prefer tips with same cp_hash as current head when cp_level equal
        # - "stick_q": same as "stick" but only when in quarantine
        self.cp_tiebreak = cp_tiebreak

        # orphans
        self.orphans_by_prev: Dict[str, List[Block]] = {}

        # local reputation table
        self.R: Dict[int, float] = {}

        # equivocation tracking
        self.seen_by_prop_height: Dict[Tuple[int, int], str] = {}
        self.equivocations_total: int = 0

        # recent stats window
        self.recent_reorgs = deque(maxlen=recent_window)
        self.recent_equivs = deque(maxlen=recent_window)
        self.recent_forktops = deque(maxlen=recent_window)

        # metrics
        self.reorg_depth_max: int = 0
        self.fork_peak: int = 0

        # quarantine EMA
        self.inconsistency_ema: float = 0.0
        self.ema_alpha: float = 0.85
        self.quarantine: bool = False
        self.below_off_streak: int = 0

    # -------- chain utilities --------
    def _tips(self) -> Set[str]:
        tips = set(self.blocks.keys())
        for p, ch in self.children.items():
            if ch:
                tips.discard(p)
        return tips

    def _chain(self, tip: str, genesis_id: str) -> List[str]:
        path: List[str] = []
        cur = tip
        while True:
            if cur not in self.blocks:
                return []
            path.append(cur)
            if cur == genesis_id:
                break
            cur = self.blocks[cur].prev
        path.reverse()
        return path

    # -------- scoring --------
    def _branch_score(self, tip: str, L: int, genesis_id: str) -> float:
        path = self._chain(tip, genesis_id)
        if not path:
            return -1e9
        tail = path[-L:] if len(path) > L else path
        s = 0.0
        for bid in tail:
            b = self.blocks[bid]
            if b.proposer >= 0:
                r = self.R.get(b.proposer, 0.0)
                s += math.log1p(max(0.0, r))
        return s

    def _forktop_count(self) -> int:
        tips = list(self._tips())
        self.fork_peak = max(self.fork_peak, len(tips))
        if not tips:
            return 0
        maxh = max(self.blocks[t].height for t in tips)
        top = [t for t in tips if self.blocks[t].height == maxh]
        return len(top)

    def _inconsistency_snapshot(self) -> float:
        forktop = self._forktop_count()
        self.recent_forktops.append(forktop)

        reorg_recent = max(self.recent_reorgs) if self.recent_reorgs else 0
        equiv_recent = sum(self.recent_equivs) if self.recent_equivs else 0

        fork_term = math.log1p(forktop)
        reorg_term = math.sqrt(reorg_recent)
        equiv_term = math.log1p(equiv_recent)

        return 1.2 * fork_term + 0.7 * reorg_term + 0.9 * equiv_term

    def _update_quarantine(self, on_th: float, off_th: float, off_streak: int):
        snap = self._inconsistency_snapshot()
        self.inconsistency_ema = self.ema_alpha * self.inconsistency_ema + (1 - self.ema_alpha) * snap

        if not self.quarantine:
            if self.inconsistency_ema > on_th:
                self.quarantine = True
                self.below_off_streak = 0
        else:
            if self.inconsistency_ema < off_th:
                self.below_off_streak += 1
            else:
                self.below_off_streak = 0
            if self.below_off_streak >= off_streak:
                self.quarantine = False
                self.below_off_streak = 0

    def _prefer_same_cp_hash(self) -> bool:
        if self.cp_tiebreak == "none":
            return False
        if self.cp_tiebreak == "stick":
            return True
        if self.cp_tiebreak == "stick_q":
            return self.quarantine
        return False

    def _fork_choice(self, genesis_id: str, L: int) -> str:
        tips = list(self._tips())
        if not tips:
            return self.head

        best = self.head
        head_cp_hash = self.blocks[self.head].cp_hash
        use_cp_hash = self._prefer_same_cp_hash()

        for t in tips:
            bt = self.blocks[t]
            bb = self.blocks[best]

            if bt.cp_level > bb.cp_level:
                best = t
                continue
            if bt.cp_level < bb.cp_level:
                continue

            if use_cp_hash and bt.cp_hash != bb.cp_hash:
                bt_match = bt.cp_hash == head_cp_hash
                bb_match = bb.cp_hash == head_cp_hash
                if bt_match and not bb_match:
                    best = t
                    continue
                if bb_match and not bt_match:
                    continue

            if bt.height > bb.height:
                best = t
                continue
            if bt.height < bb.height:
                continue

            st = self._branch_score(t, L=L, genesis_id=genesis_id)
            sb = self._branch_score(best, L=L, genesis_id=genesis_id)
            if st > sb:
                best = t
            elif st == sb and t < best:
                best = t

        return best

    def _maybe_switch_head(self, candidate: str, genesis_id: str, L: int, margin: int, score_delta: float):
        cur = self.blocks[self.head]
        cand = self.blocks[candidate]

        if not self.quarantine:
            self.head = candidate
            return

        if cand.cp_level > cur.cp_level:
            self.head = candidate
            return

        if cand.cp_level == cur.cp_level:
            if cand.height >= cur.height + margin:
                self.head = candidate
                return
            if cand.height == cur.height:
                sc = self._branch_score(candidate, L=L, genesis_id=genesis_id)
                ss = self._branch_score(self.head, L=L, genesis_id=genesis_id)
                if sc >= ss + score_delta:
                    self.head = candidate
                    return

    # -------- orphan processing --------
    def _process_orphans(self, genesis_id: str, L: int, q_on: float, q_off: float, q_off_streak: int, q_margin: int, score_delta: float):
        progressed = True
        while progressed:
            progressed = False
            ready_parents = [p for p in list(self.orphans_by_prev.keys()) if p in self.blocks]
            for p in ready_parents:
                orphans = self.orphans_by_prev.pop(p, [])
                for ob in orphans:
                    if ob.bid in self.blocks:
                        continue
                    self._accept_block_internal(ob, genesis_id, L, q_on, q_off, q_off_streak, q_margin, score_delta)
                    progressed = True

    # -------- block acceptance --------
    def accept_block(self, b: Block, genesis_id: str, L: int,
                     q_on: float, q_off: float, q_off_streak: int, q_margin: int, score_delta: float):
        if b.bid in self.blocks:
            return
        if b.prev not in self.blocks:
            self.orphans_by_prev.setdefault(b.prev, []).append(b)
            return
        self._accept_block_internal(b, genesis_id, L, q_on, q_off, q_off_streak, q_margin, score_delta)
        self._process_orphans(genesis_id, L, q_on, q_off, q_off_streak, q_margin, score_delta)

    def _accept_block_internal(self, b: Block, genesis_id: str, L: int,
                               q_on: float, q_off: float, q_off_streak: int, q_margin: int, score_delta: float):
        if b.bid in self.blocks:
            return
        if b.prev not in self.blocks:
            self.orphans_by_prev.setdefault(b.prev, []).append(b)
            return

        equiv_now = 0
        if b.proposer >= 0:
            key = (b.proposer, b.height)
            prev_seen = self.seen_by_prop_height.get(key)
            if prev_seen is not None and prev_seen != b.bid:
                equiv_now = 1
                self.equivocations_total += 1
                self.R[b.proposer] = max(0.0, self.R.get(b.proposer, 0.0) - 1.0)
            else:
                self.seen_by_prop_height[key] = b.bid
        self.recent_equivs.append(equiv_now)

        old_head = self.head

        self.blocks[b.bid] = b
        self.children.setdefault(b.prev, []).append(b.bid)
        self.children.setdefault(b.bid, [])

        self._update_quarantine(on_th=q_on, off_th=q_off, off_streak=q_off_streak)

        cand = self._fork_choice(genesis_id, L)
        self._maybe_switch_head(cand, genesis_id, L, margin=q_margin, score_delta=score_delta)

        if self.head != old_head:
            old_path = self._chain(old_head, genesis_id)
            new_path = self._chain(self.head, genesis_id)
            if old_path and new_path:
                lca = 0
                for x, y in zip(old_path, new_path):
                    if x == y:
                        lca += 1
                    else:
                        break
                reorg = len(old_path) - lca
                self.reorg_depth_max = max(self.reorg_depth_max, reorg)
                self.recent_reorgs.append(reorg)
        else:
            self.recent_reorgs.append(0)

        if b.proposer >= 0:
            self.R[b.proposer] = self.R.get(b.proposer, 0.0) + 0.05


# ----------------- Proof-of-context helpers -----------------
def _ctx_root(prev_ctx_root: str, prev_bid: str, cp_hash: str, proposer: int, height: int) -> str:
    payload = bytes.fromhex(prev_ctx_root) + bytes.fromhex(prev_bid) + bytes.fromhex(cp_hash) + f"{proposer}:{height}".encode()
    return hx(H(payload))


def _context_mem_bytes(block: Block, proof_mem_cells: int, proof_cells_per_cp: int, proof_cell_bytes: int) -> int:
    cells = int(proof_mem_cells + proof_cells_per_cp * max(0, block.cp_level))
    return cells * proof_cell_bytes


def _active_head_stats(nodes: List[Node]) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    for nd in nodes:
        b = nd.blocks[nd.head]
        entry = stats.setdefault(b.ctx_root, {"count": 0, "height": b.height, "cp_level": b.cp_level})
        entry["count"] += 1
        if b.height > entry["height"]:
            entry["height"] = b.height
        if b.cp_level > entry["cp_level"]:
            entry["cp_level"] = b.cp_level
    return stats


def _canonical_context_label(nodes: List[Node]) -> Optional[str]:
    stats = _active_head_stats(nodes)
    if not stats:
        return None
    # Majority support, then higher height, then lexicographic label.
    ranked = sorted(stats.items(), key=lambda kv: (kv[1]["count"], kv[1]["height"], kv[0]), reverse=True)
    return ranked[0][0]


def _select_stored_contexts(active_stats: Dict[str, Dict[str, int]], budget_contexts: int) -> Set[str]:
    if budget_contexts < 0 or budget_contexts >= len(active_stats):
        return set(active_stats.keys())
    ranked = sorted(active_stats.items(), key=lambda kv: (kv[1]["count"], kv[1]["height"], kv[0]), reverse=True)
    return {label for label, _ in ranked[:budget_contexts]}


def _challenge_targets(
    active_stats: Dict[str, Dict[str, int]],
    challenge_mode: str,
    challenge_k: int,
    canonical_label: Optional[str],
) -> Set[str]:
    labels = list(active_stats.keys())
    if not labels:
        return set()
    if challenge_mode == "canonical":
        return {canonical_label} if canonical_label is not None else set()
    if challenge_mode == "all_active":
        return set(labels)
    if challenge_mode == "uniform_active":
        k = max(1, min(int(challenge_k), len(labels)))
        if k >= len(labels):
            return set(labels)
        sampled = random.sample(labels, k)
        return set(sampled)
    raise ValueError(f"Unknown challenge_mode={challenge_mode}")


def _challenge_success_prob(
    active_stats: Dict[str, Dict[str, int]],
    stored_labels: Set[str],
    challenge_mode: str,
    challenge_k: int,
    canonical_label: Optional[str],
) -> Optional[float]:
    labels = list(active_stats.keys())
    n = len(labels)
    if n == 0:
        return None
    if challenge_mode == "canonical":
        if canonical_label is None:
            return None
        return 1.0 if canonical_label in stored_labels else 0.0
    if challenge_mode == "all_active":
        return 1.0 if set(labels).issubset(stored_labels) else 0.0
    if challenge_mode == "uniform_active":
        k = max(1, min(int(challenge_k), n))
        m = sum(1 for label in labels if label in stored_labels)
        if m < k:
            return 0.0
        return float(comb(m, k) / comb(n, k))
    raise ValueError(f"Unknown challenge_mode={challenge_mode}")


# ----------------- Simulation -----------------
def simulate(
    n_nodes: int = 20,
    block_interval_s: float = 30.0,
    sim_time_s: float = 3600.0,
    partition: Tuple[float, float] = (1200.0, 2400.0),
    A_size: int = 10,

    # checkpoint params
    epoch_len_blocks: int = 30,
    cp_epoch_mode: str = "height",
    epoch_len_s: Optional[float] = None,
    cp_tiebreak: str = "none",

    # scoring + convergence
    L_score_window: int = 40,
    K_converge: int = 30,
    use_quarantine: bool = True,
    seed: int = 0,

    # network
    net_delay_mean: float = 0.25,
    net_delay_jitter: float = 0.10,
    net_drop_p: float = 0.00,

    # gossip
    gossip_tick_s: float = 1.0,
    gossip_pairs_normal: int = 1,
    gossip_pairs_quarantine: int = 4,

    # bandwidth proxy settings
    block_bytes_est: int = 250,

    # extension B / proof-of-context settings
    proof_mem_cells: int = 16384,
    proof_cells_per_cp: int = 2048,
    proof_cell_bytes: int = 32,
    proof_tick_s: float = 5.0,
    proof_trigger_mode: str = "quarantine_or_rejoin",  # quarantine | rejoin | quarantine_or_rejoin | always
    proof_trigger_q_frac: float = 0.25,
    proof_challenge_mode: str = "uniform_active",      # canonical | uniform_active | all_active
    proof_challenge_k: int = 2,
    attacker_budget_contexts: int = 4,
):
    random.seed(seed)
    genesis_id = "00" * 32
    genesis_cp = hx(H(b"CP0"))
    genesis_ctx = hx(H(b"CTX0"))
    genesis = Block(
        bid=genesis_id,
        height=0,
        prev=genesis_id,
        proposer=-1,
        time=0.0,
        cp_level=0,
        cp_hash=genesis_cp,
        ctx_root=genesis_ctx,
    )

    nodes = [Node(i, genesis, recent_window=200, cp_tiebreak=cp_tiebreak) for i in range(n_nodes)]
    net = Network(n_nodes, delay_mean=net_delay_mean, delay_jitter=net_delay_jitter, drop_p=net_drop_p)

    A = set(range(A_size))
    B = set(range(A_size, n_nodes))

    total_rate = 1.0 / block_interval_s
    per_node_rate = total_rate / n_nodes

    q_on = 1.05 if use_quarantine else 1e9
    q_off = 0.75 if use_quarantine else -1e9
    q_off_streak = 25
    q_margin = 1
    score_delta = 0.15

    if epoch_len_s is None:
        epoch_len_s = float(epoch_len_blocks) * float(block_interval_s)

    bw = {
        "broadcast_blocks_sent": 0,
        "broadcast_deliveries": 0,
        "gossip_pairs": 0,
        "gossip_blocks_sent": 0,
        "gossip_deliveries": 0,
    }

    # Shadow proof-of-context metrics
    proof = {
        "challenge_count": 0,
        "challenge_success_prob_sum": 0.0,
        "challenge_targets_peak": 1,
        "challenge_targets_sum": 0.0,
        "contexts_required_peak": 1,
        "contexts_stored_peak": 1,
        "required_mem_peak_bytes": _context_mem_bytes(genesis, proof_mem_cells, proof_cells_per_cp, proof_cell_bytes),
        "stored_mem_peak_bytes": _context_mem_bytes(genesis, proof_mem_cells, proof_cells_per_cp, proof_cell_bytes),
        "honest_mem_peak_bytes": _context_mem_bytes(genesis, proof_mem_cells, proof_cells_per_cp, proof_cell_bytes),
        "contexts_required_sum": 0.0,
        "live_steps": 0,
        "rejoin_contexts_required": 1,
        "rejoin_mem_required_bytes": _context_mem_bytes(genesis, proof_mem_cells, proof_cells_per_cp, proof_cell_bytes),
        "rejoin_challenge_targets": 1,
        "rejoin_challenge_success_prob": 1.0,
    }

    pq: List[Event] = []

    def schedule_propose(node_id: int, now: float):
        t = now + random.expovariate(per_node_rate)
        heapq.heappush(pq, Event(time=t, kind="propose", src=node_id))

    def schedule_gossip_tick(now: float):
        heapq.heappush(pq, Event(time=now + gossip_tick_s, kind="gossip_tick", src=-1))

    def schedule_proof_tick(now: float):
        if proof_tick_s > 0:
            heapq.heappush(pq, Event(time=now + proof_tick_s, kind="proof_tick", src=-1))

    for i in range(n_nodes):
        schedule_propose(i, now=0.0)
    schedule_gossip_tick(now=0.0)
    schedule_proof_tick(now=0.0)

    rejoin_t = partition[1]
    converged_at = None
    stable = 0
    last_head = None
    rejoin_recorded = False

    def all_heads_equal() -> bool:
        h = nodes[0].head
        return all(nd.head == h for nd in nodes)

    def proof_live(now: float) -> bool:
        active_stats = _active_head_stats(nodes)
        distinct_heads = len(active_stats)
        q_frac = sum(1 for nd in nodes if nd.quarantine) / n_nodes
        if proof_trigger_mode == "always":
            return True
        if proof_trigger_mode == "quarantine":
            return q_frac >= proof_trigger_q_frac
        if proof_trigger_mode == "rejoin":
            return now >= rejoin_t and distinct_heads > 1
        if proof_trigger_mode == "quarantine_or_rejoin":
            return (q_frac >= proof_trigger_q_frac) or (now >= rejoin_t and distinct_heads > 1)
        raise ValueError(f"Unknown proof_trigger_mode={proof_trigger_mode}")

    def update_proof_metrics(now: float):
        active_stats = _active_head_stats(nodes)
        canonical = _canonical_context_label(nodes)
        distinct = len(active_stats)
        if distinct <= 0 or canonical is None:
            return

        # memory requirements for all currently plausible contexts
        label_to_block = {}
        for nd in nodes:
            b = nd.blocks[nd.head]
            label_to_block.setdefault(b.ctx_root, b)

        required_mem = sum(
            _context_mem_bytes(label_to_block[label], proof_mem_cells, proof_cells_per_cp, proof_cell_bytes)
            for label in active_stats.keys()
        )
        honest_mem = max(
            _context_mem_bytes(b, proof_mem_cells, proof_cells_per_cp, proof_cell_bytes)
            for b in label_to_block.values()
        )
        stored_labels = _select_stored_contexts(active_stats, attacker_budget_contexts)
        stored_mem = sum(
            _context_mem_bytes(label_to_block[label], proof_mem_cells, proof_cells_per_cp, proof_cell_bytes)
            for label in stored_labels
        )

        proof["contexts_required_peak"] = max(proof["contexts_required_peak"], distinct)
        proof["contexts_stored_peak"] = max(proof["contexts_stored_peak"], len(stored_labels))
        proof["required_mem_peak_bytes"] = max(proof["required_mem_peak_bytes"], required_mem)
        proof["stored_mem_peak_bytes"] = max(proof["stored_mem_peak_bytes"], stored_mem)
        proof["honest_mem_peak_bytes"] = max(proof["honest_mem_peak_bytes"], honest_mem)

        challenge_prob = _challenge_success_prob(
            active_stats,
            stored_labels,
            proof_challenge_mode,
            proof_challenge_k,
            canonical,
        )
        challenge_targets = _challenge_targets(
            active_stats,
            proof_challenge_mode,
            proof_challenge_k,
            canonical,
        )
        proof["challenge_targets_peak"] = max(proof["challenge_targets_peak"], len(challenge_targets))

        if proof_live(now):
            proof["live_steps"] += 1
            proof["contexts_required_sum"] += distinct
            proof["challenge_count"] += 1
            proof["challenge_targets_sum"] += len(challenge_targets)
            proof["challenge_success_prob_sum"] += (challenge_prob if challenge_prob is not None else 0.0)

        if not rejoin_recorded and now >= rejoin_t:
            proof["rejoin_contexts_required"] = distinct
            proof["rejoin_mem_required_bytes"] = required_mem
            proof["rejoin_challenge_targets"] = len(challenge_targets)
            proof["rejoin_challenge_success_prob"] = (challenge_prob if challenge_prob is not None else 0.0)

    while pq:
        ev = heapq.heappop(pq)
        t = ev.time
        if t > sim_time_s:
            break

        if partition[0] <= t < partition[1]:
            if not net.partition_on:
                net.set_partition(A, B, True)
        else:
            if net.partition_on:
                net.set_partition(A, B, False)

        if ev.kind == "propose":
            proposer = ev.src
            nd = nodes[proposer]
            prev = nd.head
            prev_block = nd.blocks[prev]
            height = prev_block.height + 1

            cp_level = prev_block.cp_level
            cp_hash = prev_block.cp_hash

            if cp_epoch_mode == "height":
                if height % epoch_len_blocks == 0:
                    cp_level += 1
                    cp_hash = hx(H(bytes.fromhex(cp_hash) + bytes.fromhex(prev)))
            elif cp_epoch_mode == "time":
                new_level = int(t // epoch_len_s)
                if new_level > cp_level:
                    cp_level = new_level
                    cp_hash = hx(H(bytes.fromhex(cp_hash) + bytes.fromhex(prev)))
            else:
                raise ValueError(f"Unknown cp_epoch_mode={cp_epoch_mode}")

            bid = hx(H(f"{proposer}:{height}:{prev}:{random.random()}".encode()))
            ctx_root = _ctx_root(prev_block.ctx_root, prev, cp_hash, proposer, height)
            blk = Block(
                bid=bid,
                height=height,
                prev=prev,
                proposer=proposer,
                time=t,
                cp_level=cp_level,
                cp_hash=cp_hash,
                ctx_root=ctx_root,
            )

            nd.accept_block(blk, genesis_id, L_score_window, q_on, q_off, q_off_streak, q_margin, score_delta)

            for j in range(n_nodes):
                if j == proposer:
                    continue
                if not net.can_send(proposer, j) or net.dropped():
                    continue
                dt = net.sample_delay()
                heapq.heappush(pq, Event(time=t + dt, kind="deliver", src=proposer, dst=j, block=blk))
                bw["broadcast_blocks_sent"] += 1
                bw["broadcast_deliveries"] += 1

            schedule_propose(proposer, now=t)

        elif ev.kind == "deliver":
            if not net.can_send(ev.src, ev.dst) or net.dropped():
                continue
            nodes[ev.dst].accept_block(ev.block, genesis_id, L_score_window, q_on, q_off, q_off_streak, q_margin, score_delta)

        elif ev.kind == "gossip_tick":
            schedule_gossip_tick(now=t)

            if net.partition_on:
                pass
            else:
                q_frac = sum(1 for nd in nodes if nd.quarantine) / n_nodes
                pairs = gossip_pairs_quarantine if q_frac >= 0.25 else gossip_pairs_normal

                for _ in range(pairs):
                    src = random.randrange(n_nodes)
                    dst = random.randrange(n_nodes)
                    while dst == src:
                        dst = random.randrange(n_nodes)
                    if not net.can_send(src, dst) or net.dropped():
                        continue

                    bw["gossip_pairs"] += 1

                    s = nodes[src]
                    d = nodes[dst]

                    s_chain = s._chain(s.head, genesis_id)
                    if not s_chain:
                        continue
                    common_idx = -1
                    for i in range(len(s_chain) - 1, -1, -1):
                        if s_chain[i] in d.blocks:
                            common_idx = i
                            break
                    if common_idx == -1:
                        common_idx = 0
                    missing_ids = s_chain[common_idx + 1:]

                    time_cursor = t
                    for bid in missing_ids:
                        blk = s.blocks[bid]
                        if blk.bid in d.blocks:
                            continue
                        if net.dropped():
                            continue
                        time_cursor += 0.005 + net.sample_delay() * 0.05
                        heapq.heappush(pq, Event(time=time_cursor, kind="deliver", src=src, dst=dst, block=blk))
                        bw["gossip_blocks_sent"] += 1
                        bw["gossip_deliveries"] += 1

        elif ev.kind == "proof_tick":
            schedule_proof_tick(now=t)
            update_proof_metrics(t)
            if t >= rejoin_t:
                rejoin_recorded = True

        if t >= rejoin_t and converged_at is None:
            if all_heads_equal():
                h = nodes[0].head
                if h == last_head:
                    stable += 1
                else:
                    last_head = h
                    stable = 1
                if stable >= K_converge:
                    converged_at = t

    # final proof update in case the last proof tick was before the end.
    update_proof_metrics(sim_time_s)

    heads_equal_end = all_heads_equal()
    max_reorg = max(nd.reorg_depth_max for nd in nodes)
    max_fork = max(nd.fork_peak for nd in nodes)
    max_equiv = max(nd.equivocations_total for nd in nodes)
    q_on_end = sum(1 for nd in nodes if nd.quarantine)

    final_canonical = _canonical_context_label(nodes)
    final_stats = _active_head_stats(nodes)
    final_stored = _select_stored_contexts(final_stats, attacker_budget_contexts)
    final_challenge_prob = _challenge_success_prob(
        final_stats,
        final_stored,
        proof_challenge_mode,
        proof_challenge_k,
        final_canonical,
    )
    final_challenge_targets = _challenge_targets(
        final_stats,
        proof_challenge_mode,
        proof_challenge_k,
        final_canonical,
    )

    bw_bytes = {
        "broadcast_bytes_est": bw["broadcast_blocks_sent"] * block_bytes_est,
        "gossip_bytes_est": bw["gossip_blocks_sent"] * block_bytes_est,
        "total_bytes_est": (bw["broadcast_blocks_sent"] + bw["gossip_blocks_sent"]) * block_bytes_est,
    }

    attacker_success_prob_mean = (
        proof["challenge_success_prob_sum"] / proof["challenge_count"] if proof["challenge_count"] > 0 else None
    )
    contexts_required_mean = (
        proof["contexts_required_sum"] / proof["live_steps"] if proof["live_steps"] > 0 else None
    )
    challenge_targets_mean = (
        proof["challenge_targets_sum"] / proof["challenge_count"] if proof["challenge_count"] > 0 else None
    )

    return {
        "use_quarantine": use_quarantine,
        "converged_at_s": converged_at,
        "recovery_time_s": (converged_at - rejoin_t) if (converged_at is not None) else None,
        "heads_equal_end": heads_equal_end,
        "max_reorg_depth": max_reorg,
        "max_fork_peak": max_fork,
        "max_equivocations_total_max": max_equiv,
        "nodes_quarantine_end": q_on_end,
        "cp_epoch_mode": cp_epoch_mode,
        "cp_tiebreak": cp_tiebreak,
        "epoch_len_s": epoch_len_s,
        **bw,
        **bw_bytes,
        # extension B metrics
        "proof_trigger_mode": proof_trigger_mode,
        "proof_trigger_q_frac": proof_trigger_q_frac,
        "proof_challenge_mode": proof_challenge_mode,
        "proof_challenge_k": proof_challenge_k,
        "proof_mem_cells": proof_mem_cells,
        "proof_cells_per_cp": proof_cells_per_cp,
        "proof_cell_bytes": proof_cell_bytes,
        "proof_tick_s": proof_tick_s,
        "attacker_budget_contexts": attacker_budget_contexts,
        "proof_challenge_count": proof["challenge_count"],
        "attacker_challenge_success_prob_mean": attacker_success_prob_mean,
        "attacker_success_end_prob": (final_challenge_prob if final_challenge_prob is not None else 0.0),
        "attacker_success_rejoin_prob": proof["rejoin_challenge_success_prob"],
        "challenge_targets_peak": proof["challenge_targets_peak"],
        "challenge_targets_mean": challenge_targets_mean,
        "challenge_targets_final": len(final_challenge_targets),
        "contexts_required_peak": proof["contexts_required_peak"],
        "contexts_required_mean": contexts_required_mean,
        "contexts_stored_peak": proof["contexts_stored_peak"],
        "required_mem_peak_bytes": proof["required_mem_peak_bytes"],
        "stored_mem_peak_bytes": proof["stored_mem_peak_bytes"],
        "honest_mem_peak_bytes": proof["honest_mem_peak_bytes"],
        "attacker_to_honest_mem_ratio_peak": (
            proof["required_mem_peak_bytes"] / max(1, proof["honest_mem_peak_bytes"])
        ),
        "rejoin_contexts_required": proof["rejoin_contexts_required"],
        "rejoin_mem_required_bytes": proof["rejoin_mem_required_bytes"],
        "rejoin_challenge_targets": proof["rejoin_challenge_targets"],
        "final_contexts_required": len(final_stats),
    }
