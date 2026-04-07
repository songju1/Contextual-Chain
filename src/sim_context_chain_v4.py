# sim_context_chain_v4.py
# Changes vs v3:
# 1) checkpoint semantics: optional time-based epochs (cp_epoch_mode="time") so cp_level is not redundant with height
# 2) checkpoint affects fork-choice via optional cp_hash stickiness (cp_tiebreak="stick" or "stick_q")
# 3) bandwidth proxy counters: broadcast/gossip "block-units" and deliveries scheduled

import random, heapq, hashlib, math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from collections import deque

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
    cp_hash: str  # hex

# ----------------- Event -----------------
@dataclass(order=True)
class Event:
    time: float
    kind: str = field(compare=False)   # "propose" | "deliver" | "gossip_tick"
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
        inA = a in self.partition_A; inB = a in self.partition_B
        jnA = b in self.partition_A; jnB = b in self.partition_B
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

        # recent stats window (to avoid "permanent quarantine")
        self.recent_reorgs = deque(maxlen=recent_window)    # ints
        self.recent_equivs = deque(maxlen=recent_window)    # 0/1
        self.recent_forktops = deque(maxlen=recent_window)  # ints

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
        path = []
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
        # Use RECENT window stats (not cumulative maxima)
        forktop = self._forktop_count()
        self.recent_forktops.append(forktop)

        # recent reorg magnitude and equiv count
        reorg_recent = max(self.recent_reorgs) if self.recent_reorgs else 0
        equiv_recent = sum(self.recent_equivs) if self.recent_equivs else 0

        # weighted: forks + reorg + equiv
        fork_term = math.log1p(forktop)
        reorg_term = math.sqrt(reorg_recent)  # damped growth
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

            # 1) cp_level (time-based if enabled in proposer)
            if bt.cp_level > bb.cp_level:
                best = t
                continue
            if bt.cp_level < bb.cp_level:
                continue

            # 2) cp_hash stickiness (optional)
            if use_cp_hash and bt.cp_hash != bb.cp_hash:
                bt_match = (bt.cp_hash == head_cp_hash)
                bb_match = (bb.cp_hash == head_cp_hash)
                if bt_match and not bb_match:
                    best = t
                    continue
                if bb_match and not bt_match:
                    continue
                # else fall through

            # 3) height
            if bt.height > bb.height:
                best = t
                continue
            if bt.height < bb.height:
                continue

            # 4) branch score
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

        # In quarantine: still allow progress
        if cand.cp_level > cur.cp_level:
            self.head = candidate
            return

        if cand.cp_level == cur.cp_level:
            # allow +1 height improvement to avoid deadlock
            if cand.height >= cur.height + margin:
                self.head = candidate
                return
            # if same height, allow switching when score is clearly better
            if cand.height == cur.height:
                sc = self._branch_score(candidate, L=L, genesis_id=genesis_id)
                ss = self._branch_score(self.head, L=L, genesis_id=genesis_id)
                if sc >= ss + score_delta:
                    self.head = candidate
                    return

    # -------- orphan processing --------
    def _process_orphans(self, genesis_id: str, L: int, q_on: float, q_off: float, q_off_streak: int, q_margin: int, score_delta: float):
        # attach any orphan whose parent is now known; iterate until stable
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

        # equivocation detection (local, simple)
        equiv_now = 0
        if b.proposer >= 0:
            key = (b.proposer, b.height)
            prev_seen = self.seen_by_prop_height.get(key)
            if prev_seen is not None and prev_seen != b.bid:
                equiv_now = 1
                self.equivocations_total += 1
                # penalize proposer mildly (not too harsh in sim)
                self.R[b.proposer] = max(0.0, self.R.get(b.proposer, 0.0) - 1.0)
            else:
                self.seen_by_prop_height[key] = b.bid
        self.recent_equivs.append(equiv_now)

        old_head = self.head

        # store
        self.blocks[b.bid] = b
        self.children.setdefault(b.prev, []).append(b.bid)
        self.children.setdefault(b.bid, [])

        # update quarantine state
        self._update_quarantine(on_th=q_on, off_th=q_off, off_streak=q_off_streak)

        # choose candidate head
        cand = self._fork_choice(genesis_id, L)

        # switch policy
        self._maybe_switch_head(cand, genesis_id, L, margin=q_margin, score_delta=score_delta)

        # reorg magnitude when switching
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

        # reward proposer mildly
        if b.proposer >= 0:
            self.R[b.proposer] = self.R.get(b.proposer, 0.0) + 0.05


# ----------------- Simulation -----------------
def simulate(
    n_nodes: int = 20,
    block_interval_s: float = 30.0,
    sim_time_s: float = 3600.0,
    partition: Tuple[float, float] = (1200.0, 2400.0),
    A_size: int = 10,

    # checkpoint params
    epoch_len_blocks: int = 600,
    cp_epoch_mode: str = "height",   # "height" (v3 behavior) or "time"
    epoch_len_s: Optional[float] = None,  # if None and cp_epoch_mode="time": epoch_len_s = epoch_len_blocks * block_interval_s
    cp_tiebreak: str = "none",       # "none" | "stick" | "stick_q"

    # scoring + convergence
    L_score_window: int = 40,
    K_converge: int = 30,   # event-based in this simulator
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

    # bandwidth proxy settings (for bytes estimation)
    block_bytes_est: int = 250,
):
    random.seed(seed)
    genesis_id = "00"*32
    genesis_cp = hx(H(b"CP0"))
    genesis = Block(
        bid=genesis_id, height=0, prev=genesis_id, proposer=-1, time=0.0,
        cp_level=0, cp_hash=genesis_cp
    )

    nodes = [Node(i, genesis, recent_window=200, cp_tiebreak=cp_tiebreak) for i in range(n_nodes)]
    net = Network(n_nodes, delay_mean=net_delay_mean, delay_jitter=net_delay_jitter, drop_p=net_drop_p)

    A = set(range(A_size))
    B = set(range(A_size, n_nodes))

    total_rate = 1.0 / block_interval_s
    per_node_rate = total_rate / n_nodes

    # quarantine thresholds
    q_on = 1.05 if use_quarantine else 1e9
    q_off = 0.75 if use_quarantine else -1e9
    q_off_streak = 25

    # head switching policy in quarantine
    q_margin = 1
    score_delta = 0.15

    # checkpoint epoch length in seconds (if time-based)
    if epoch_len_s is None:
        epoch_len_s = float(epoch_len_blocks) * float(block_interval_s)

    # bandwidth proxy counters (counts "block deliveries scheduled" as a proxy)
    bw = {
        "broadcast_blocks_sent": 0,
        "broadcast_deliveries": 0,   # same as blocks_sent here
        "gossip_pairs": 0,
        "gossip_blocks_sent": 0,
        "gossip_deliveries": 0,      # same as blocks_sent here
    }

    pq: List[Event] = []

    def schedule_propose(node_id: int, now: float):
        t = now + random.expovariate(per_node_rate)
        heapq.heappush(pq, Event(time=t, kind="propose", src=node_id))

    def schedule_gossip_tick(now: float):
        heapq.heappush(pq, Event(time=now + gossip_tick_s, kind="gossip_tick", src=-1))

    for i in range(n_nodes):
        schedule_propose(i, now=0.0)
    schedule_gossip_tick(now=0.0)

    rejoin_t = partition[1]
    converged_at = None
    stable = 0
    last_head = None

    def all_heads_equal() -> bool:
        h = nodes[0].head
        return all(nd.head == h for nd in nodes)

    while pq:
        ev = heapq.heappop(pq)
        t = ev.time
        if t > sim_time_s:
            break

        # partition toggle
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

            # checkpoint: compute cp_level/cp_hash
            cp_level = prev_block.cp_level
            cp_hash = prev_block.cp_hash

            if cp_epoch_mode == "height":
                if height % epoch_len_blocks == 0:
                    cp_level += 1
                    cp_hash = hx(H(bytes.fromhex(cp_hash) + bytes.fromhex(prev)))
            elif cp_epoch_mode == "time":
                # global time-based epochs: cp_level depends on wall-clock, not chain height
                new_level = int(t // epoch_len_s)
                if new_level > cp_level:
                    cp_level = new_level
                    cp_hash = hx(H(bytes.fromhex(cp_hash) + bytes.fromhex(prev)))
            else:
                raise ValueError(f"Unknown cp_epoch_mode={cp_epoch_mode}")

            bid = hx(H(f"{proposer}:{height}:{prev}:{random.random()}".encode()))
            blk = Block(bid=bid, height=height, prev=prev, proposer=proposer, time=t, cp_level=cp_level, cp_hash=cp_hash)

            nd.accept_block(blk, genesis_id, L_score_window, q_on, q_off, q_off_streak, q_margin, score_delta)

            # broadcast current block
            for j in range(n_nodes):
                if j == proposer:
                    continue
                if not net.can_send(proposer, j) or net.dropped():
                    continue
                dt = net.sample_delay()
                heapq.heappush(pq, Event(time=t+dt, kind="deliver", src=proposer, dst=j, block=blk))
                bw["broadcast_blocks_sent"] += 1
                bw["broadcast_deliveries"] += 1

            schedule_propose(proposer, now=t)

        elif ev.kind == "deliver":
            if not net.can_send(ev.src, ev.dst) or net.dropped():
                continue
            nodes[ev.dst].accept_block(ev.block, genesis_id, L_score_window, q_on, q_off, q_off_streak, q_margin, score_delta)

        elif ev.kind == "gossip_tick":
            schedule_gossip_tick(now=t)

            # decide gossip budget based on quarantine prevalence (if network is connected)
            if net.partition_on:
                continue

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

                # sender shares head chain until common ancestor
                s_chain = s._chain(s.head, genesis_id)
                if not s_chain:
                    continue
                # find highest common ancestor from tail
                common_idx = -1
                for i in range(len(s_chain)-1, -1, -1):
                    if s_chain[i] in d.blocks:
                        common_idx = i
                        break
                if common_idx == -1:
                    common_idx = 0
                missing_ids = s_chain[common_idx+1:]

                # send missing blocks in order (compressed transfer)
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

        # convergence check after rejoin
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

    heads_equal_end = all_heads_equal()
    max_reorg = max(nd.reorg_depth_max for nd in nodes)
    max_fork = max(nd.fork_peak for nd in nodes)
    max_equiv = max(nd.equivocations_total for nd in nodes)
    q_on_end = sum(1 for nd in nodes if nd.quarantine)

    # bytes proxy (optional)
    bw_bytes = {
        "broadcast_bytes_est": bw["broadcast_blocks_sent"] * block_bytes_est,
        "gossip_bytes_est": bw["gossip_blocks_sent"] * block_bytes_est,
        "total_bytes_est": (bw["broadcast_blocks_sent"] + bw["gossip_blocks_sent"]) * block_bytes_est,
    }

    return {
        "use_quarantine": use_quarantine,
        "converged_at_s": converged_at,
        "recovery_time_s": (converged_at - rejoin_t) if (converged_at is not None) else None,
        "heads_equal_end": heads_equal_end,
        "max_reorg_depth": max_reorg,
        "max_fork_peak": max_fork,
        "max_equivocations_total_max": max_equiv,
        "nodes_quarantine_end": q_on_end,

        # new: checkpoint config echoed
        "cp_epoch_mode": cp_epoch_mode,
        "cp_tiebreak": cp_tiebreak,
        "epoch_len_s": epoch_len_s,

        # new: bandwidth proxies
        **bw,
        **bw_bytes,
    }
