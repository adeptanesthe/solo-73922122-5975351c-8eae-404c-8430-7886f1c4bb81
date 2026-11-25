"""Minimum-action stroke-order revision planner.

Model
-----
The reference character has ``N`` ordered strokes; the student has ``K``
actual strokes in writing order.  A finished draft has exactly one slot per
reference stroke (one-to-one by stroke order).  Slot ``j`` is either:

* an actual stroke whose discrete Frechet distance to reference stroke ``j``
  is within the web-set threshold, or
* an added stroke ("bu bi"), demonstrated with the dashed reference line.

Every unused actual stroke is deleted (1 action).  Among used strokes, the
ones forming an order-preserving "backbone" need no move; every other used
stroke is moved once to an arbitrary stroke-order position (1 action).  Hence
moves = used - longest feasible increasing assignment.

A teacher-preserved stroke may be neither deleted nor selected for a move, so
it is forced onto the backbone.  If preserved strokes cannot be assigned
strictly increasing compatible reference slots, no solution exists.

The DP processes actual strokes in their current order.  State is
``(last_backbone_slot, occupied_slot_mask)`` stored in one flat array.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .frechet import (
    MAX_POINTS_PER_STROKE,
    MAX_STROKES,
    discrete_frechet,
)

INF = 10 ** 9

# Decision: ("del",) | ("mv", slot) | ("bb", slot)
Decision = Tuple[Any, ...]


@dataclass
class SolveResult:
    feasible: bool
    cost: int = 0
    moves: int = 0
    deletes: int = 0
    adds: int = 0
    decisions: List[Decision] = field(default_factory=list)
    assign: Dict[int, int] = field(default_factory=dict)
    backbone: Set[int] = field(default_factory=set)
    add_slots: List[int] = field(default_factory=list)
    dist: List[List[float]] = field(default_factory=list)
    compatible: List[List[bool]] = field(default_factory=list)
    reason: str = ""

    def counts(self) -> Dict[str, int]:
        return {"move": self.moves, "delete": self.deletes, "add": self.adds}


def validate_payload(strokes: Any, model: Any) -> Optional[str]:
    """Return a Chinese error message if payload limits/shape are violated."""
    if not isinstance(strokes, list) or not isinstance(model, list):
        return "笔迹与范字必须是笔画数组"
    if len(model) == 0:
        return "范字至少要有一笔"
    if len(model) > MAX_STROKES:
        return f"范字至多 {MAX_STROKES} 笔"
    if len(strokes) > MAX_STROKES:
        return f"实写至多 {MAX_STROKES} 笔"

    def _check_one(stroke: Any, label: str, idx: int) -> Optional[str]:
        pts = stroke.get("points") if isinstance(stroke, dict) else stroke
        if not isinstance(pts, list) or len(pts) == 0:
            return f"{label}第 {idx + 1} 笔没有有效点"
        if len(pts) > MAX_POINTS_PER_STROKE:
            return f"{label}第 {idx + 1} 笔超过 {MAX_POINTS_PER_STROKE} 个点"
        for p in pts:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                return f"{label}第 {idx + 1} 笔存在非法坐标点"
            for value in p[:2]:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return f"{label}第 {idx + 1} 笔存在非法坐标点"
                try:
                    finite = math.isfinite(float(value))
                except OverflowError:
                    finite = False
                if not finite:
                    return f"{label}第 {idx + 1} 笔存在非法坐标点"
        return None

    for i, s in enumerate(strokes):
        err = _check_one(s, "实写", i)
        if err:
            return err
    for i, s in enumerate(model):
        err = _check_one(s, "范字", i)
        if err:
            return err
    return None


def points_of(stroke: Any) -> Sequence[Sequence[float]]:
    return stroke["points"] if isinstance(stroke, dict) else stroke


def preserved_of(strokes: Sequence[Any]) -> List[bool]:
    return [bool(s.get("preserved", False)) if isinstance(s, dict) else False
            for s in strokes]


def solve_matrix(
    compatible: Sequence[Sequence[bool]],
    preserved: Sequence[bool],
    n_slots: int,
) -> SolveResult:
    """Cost DP + reconstruction over a precomputed compatibility matrix."""
    K = len(compatible)
    N = n_slots
    M = 1 << N
    size = (N + 1) * M

    # Flat index = last1 * M + mask, last1 = last_backbone_slot + 1 (0 = none).
    dp = [INF] * size
    dp[0] = 0
    active: List[int] = [0]
    # backs[x][new_idx] = (old_idx, kind, slot)  kind: 0 del, 1 mv, 2 bb
    backs: List[Dict[int, Tuple[int, int, int]]] = []
    alive = True

    for x in range(K):
        ndp = [INF] * size
        nactive: List[int] = []
        back: Dict[int, Tuple[int, int, int]] = {}
        cx = compatible[x]
        is_p = bool(preserved[x])

        for idx in active:
            cost = dp[idx]
            last1 = idx // M
            mask = idx - last1 * M

            def offer(ni: int, nc: int, kind: int, slot: int) -> None:
                cur = ndp[ni]
                if nc < cur:
                    if cur == INF:
                        nactive.append(ni)
                    ndp[ni] = nc
                    back[ni] = (idx, kind, slot)

            # Delete (never for preserved strokes).
            if not is_p:
                offer(idx, cost + 1, 0, -1)

            for j in range(N):
                bit = 1 << j
                if mask & bit or not cx[j]:
                    continue
                new_mask = mask | bit
                # Moved use: no ordering constraint (non-preserved only).
                if not is_p:
                    offer(last1 * M + new_mask, cost + 1, 1, j)
                # Backbone / unmoved use: slot must follow previous backbone.
                if j >= last1:
                    offer((j + 1) * M + new_mask, cost, 2, j)

        dp = ndp
        active = nactive
        backs.append(back)
        if not active:
            alive = False
            break

    if not alive:
        return SolveResult(feasible=False, reason=_infeasible_reason(preserved, compatible))

    best_idx = -1
    best_total = INF
    for idx in active:
        last1 = idx // M
        mask = idx - last1 * M
        total = dp[idx] + (N - mask.bit_count())
        if total < best_total:
            best_total = total
            best_idx = idx

    if best_idx < 0 or best_total >= INF:
        return SolveResult(feasible=False, reason=_infeasible_reason(preserved, compatible))

    # Reconstruct.
    decisions: List[Decision] = [("del",)] * K
    idx = best_idx
    for x in range(K - 1, -1, -1):
        old_idx, kind, slot = backs[x][idx]
        if kind == 0:
            decisions[x] = ("del",)
        elif kind == 1:
            decisions[x] = ("mv", slot)
        else:
            decisions[x] = ("bb", slot)
        idx = old_idx

    assign: Dict[int, int] = {}
    occupied = 0
    for x, dec in enumerate(decisions):
        if dec[0] == "del":
            continue
        j = int(dec[1])
        assign[x] = j
        occupied |= 1 << j

    # The DP tie-break may reconstruct one maximum-length backbone while the
    # step builder's deterministic forced-LIS tie-break chooses another.  They
    # have the same cardinality/cost; normalise the public result to the exact
    # forced LIS used to construct steps so a declared unmoved stroke is never
    # targeted by a move/delete operation.
    survivors = sorted(assign)
    ranks = [assign[x] for x in survivors]
    forced_positions = {i for i, x in enumerate(survivors) if bool(preserved[x])}
    lis_positions = maximum_forced_lis(ranks, forced_positions)
    backbone = {survivors[i] for i in lis_positions}

    moves = deletes = 0
    for x in range(K):
        if decisions[x][0] == "del":
            deletes += 1
            continue
        j = assign[x]
        if x in backbone:
            decisions[x] = ("bb", j)
        else:
            decisions[x] = ("mv", j)
            moves += 1

    add_slots = [j for j in range(N) if not (occupied & (1 << j))]
    return SolveResult(
        feasible=True,
        cost=best_total,
        moves=moves,
        deletes=deletes,
        adds=len(add_slots),
        decisions=decisions,
        assign=assign,
        backbone=backbone,
        add_slots=add_slots,
    )


def solve(
    strokes: Sequence[Any],
    preserved: Sequence[bool],
    model: Sequence[Any],
    threshold: float,
) -> SolveResult:
    """Compute the optimum from the current actual stroke order."""
    K, N = len(strokes), len(model)
    # Exact discrete Frechet on the provided (<=64 point) ordered polylines;
    # direction is the recorded writing direction and is never reversed.
    dist = [
        [discrete_frechet(points_of(strokes[x]), points_of(model[j])) for j in range(N)]
        for x in range(K)
    ]
    compatible = [[dist[x][j] <= threshold + 1e-9 for j in range(N)] for x in range(K)]
    pres = list(preserved) + [False] * max(0, K - len(preserved))

    result = solve_matrix(compatible, pres, N)
    result.dist = dist
    result.compatible = compatible
    if not result.feasible and not result.reason:
        result.reason = _infeasible_reason(pres, compatible)
    return result


def _infeasible_reason(preserved: Sequence[bool], compatible: Sequence[Sequence[bool]]) -> str:
    for x, is_p in enumerate(preserved):
        if not is_p or x >= len(compatible):
            continue
        slots = [j + 1 for j, ok in enumerate(compatible[x]) if ok]
        if not slots:
            return (
                f"无解：标记保留的第 {x + 1} 笔与任何范字笔画的离散 "
                "Fréchet 距离都超过上限，无法对应；请调大上限或取消该笔保留。"
            )
    return (
        "无解：保留笔画在实写中的先后顺序与范字笔序冲突，"
        "而保留笔不能移动；请取消部分保留标记。"
    )


# ---------------------------------------------------------------------------
# List operation shared with the frontend semantics.
# ---------------------------------------------------------------------------

def apply_move(items: List[Any], frm: int, to: int) -> List[Any]:
    """Move item at ``frm`` to resulting-list index ``to``.

    Pop at ``frm`` then insert at ``to`` into the length-n-1 list; ``to`` is
    therefore the desired index in the resulting list (0 <= to < n).
    """
    n = len(items)
    if n == 0:
        return items
    frm = max(0, min(frm, n - 1))
    item = items.pop(frm)
    to = max(0, min(to, n - 1))
    items.insert(to, item)
    return items


def maximum_forced_lis(ranks: Sequence[int], forced: Set[int]) -> Set[int]:
    """Longest strictly increasing subset of positions containing ``forced``.

    ``ranks[i]`` is the target slot of current position ``i``.  With at most
    10 positions, enumerating masks is simplest and obviously correct.
    """
    n = len(ranks)
    best: Set[int] = set()
    for mask in range(1 << n):
        ok = True
        for f in forced:
            if not (mask >> f) & 1:
                ok = False
                break
        if not ok:
            continue
        idx = [i for i in range(n) if (mask >> i) & 1]
        for a in range(len(idx)):
            ra = ranks[idx[a]]
            for b in range(a + 1, len(idx)):
                if ra >= ranks[idx[b]]:
                    ok = False
                    break
            if not ok:
                break
        if ok and len(idx) > len(best):
            best = set(idx)
    return best


def move_operations_to_target(
    current_uids: List[Any],
    target_uids: List[Any],
    fixed_uids: Set[Any],
) -> List[Dict[str, Any]]:
    """Concrete move ops that turn ``current_uids`` into ``target_uids``.

    Only uids not in ``fixed_uids`` are ever moved, each exactly once.  Process
    the target left to right; move a movable uid to immediately after its
    immediate target predecessor (index computed on the post-removal list).
    Validated by exhaustive/random property tests.
    """
    live = list(current_uids)
    ops: List[Dict[str, Any]] = []
    for p, uid in enumerate(target_uids):
        if uid in fixed_uids:
            continue
        frm = live.index(uid)
        popped = live[:frm] + live[frm + 1:]
        if p == 0:
            to = 0
        else:
            predecessor = target_uids[p - 1]
            to = popped.index(predecessor) + 1
        ops.append({"uid": uid, "from": frm, "to": to})
        apply_move(live, frm, to)
    return ops


# ---------------------------------------------------------------------------
# Full plan assembly.
# ---------------------------------------------------------------------------

def build_plan(
    strokes: Sequence[Dict[str, Any]],
    model: Sequence[Any],
    threshold: float,
) -> Dict[str, Any]:
    validation_error = validate_payload(strokes, model)
    if validation_error is not None:
        return {"feasible": False, "reason": validation_error, "steps": []}

    result = solve(strokes, preserved_of(strokes), model, threshold)
    if not result.feasible:
        return {
            "feasible": False,
            "reason": result.reason,
            "steps": [],
            "dist": result.dist,
            "compatible": result.compatible,
        }

    n_model = len(model)
    steps: List[Dict[str, Any]] = []
    k_strokes = len(strokes)
    pres_flags = preserved_of(strokes)

    # External uids are echoed exactly when supplied.  Internally, however,
    # original positions are the only identity: an omitted uid defaults to an
    # integer index in the API, and that value can collide with an explicit
    # numeric uid from the request.
    def external_uid(x: int) -> Any:
        stroke = strokes[x]
        if isinstance(stroke, dict) and "uid" in stroke:
            return stroke["uid"]
        return x

    # --- Delete steps: repeatedly remove the leftmost doomed stroke. -------
    # ``live_keys`` stores immutable internal identities (original indexes).
    doomed = {x for x, d in enumerate(result.decisions) if d[0] == "del"}
    live_keys: List[int] = list(range(k_strokes))

    while doomed:
        pos = next(pos for pos, key in enumerate(live_keys) if key in doomed)
        x = live_keys.pop(pos)
        doomed.remove(x)
        steps.append({
            "kind": "delete",
            # pos was found before popping and is therefore the required
            # zero-based index in the state immediately before this step.
            "index": pos,
            "uid": external_uid(x),
            "points": [list(p) for p in points_of(strokes[x])],
            "label": f"删除当前第 {pos + 1} 笔",
        })

    # --- Move steps for surviving strokes ----------------------------------
    survivors: List[int] = [x for x in range(k_strokes)
                            if result.decisions[x][0] != "del"]
    current_keys = list(live_keys)
    # Target order = survivors sorted by their unique assigned model slot.
    target_keys = sorted(survivors, key=lambda x: (result.assign[x], x))

    # ``solve_matrix`` has already normalised its decisions/backbone to the
    # same deterministic maximum forced LIS used by the operation builder.
    fixed_keys = set(result.backbone)
    fixed_keys.update(x for x in survivors if pres_flags[x])

    move_ops = move_operations_to_target(current_keys, target_keys, fixed_keys)
    for op in move_ops:
        x = int(op["uid"])  # passed as an internal original-position key
        j = result.assign[x]
        steps.append({
            "kind": "move",
            "from": op["from"],
            "to": op["to"],
            "uid": external_uid(x),
            "slot": j,
            "label": (f"把当前第 {op['from'] + 1} 笔移到第 {op['to'] + 1} 个"
                      f"实写笔序位置（对应范字第 {j + 1} 笔）"),
        })
        apply_move(live_keys, op["from"], op["to"])

    # Sanity: generated real steps must match the DP counts exactly and must
    # leave surviving internal identities in assignment-slot order.
    n_del_steps = sum(1 for s in steps if s["kind"] == "delete")
    n_mv_steps = sum(1 for s in steps if s["kind"] == "move")
    if live_keys != target_keys:
        raise RuntimeError("failed to construct move steps for the optimal assignment")
    unmoved_indexes = sorted(fixed_keys)
    unmoved_uids = [external_uid(x) for x in unmoved_indexes]

    # --- Add steps (dashed reference strokes) ------------------------------
    for j in result.add_slots:
        steps.append({
            "kind": "add",
            "slot": j,
            "label": f"补写范字第 {j + 1} 笔（虚线示意）",
            "points": points_of(model[j]),
        })

    for i, st in enumerate(steps):
        st["step"] = i

    next_action: Optional[Dict[str, Any]] = None
    for st in steps:
        if st["kind"] in ("move", "delete"):
            next_action = {k: v for k, v in st.items() if k != "step"}
            break

    return {
        "feasible": True,
        "cost": result.cost,
        "counts": {"move": n_mv_steps, "delete": n_del_steps, "add": result.adds},
        "steps": steps,
        "next_action": next_action,
        "dist": result.dist,
        "compatible": result.compatible,
        "assign": {str(x): j for x, j in result.assign.items()},
        # Original zero-based request indexes forming the zero-move backbone.
        "backbone": unmoved_indexes,
        # Exact request uid values for those indexes (implicit uids are indexes).
        "backbone_uids": unmoved_uids,
        "fixed_uids": unmoved_uids,
        "fixed_indexes": unmoved_indexes,
        "unmoved_indexes": unmoved_indexes,
        "unmoved_uids": unmoved_uids,
        "threshold": threshold,
        "n_model": n_model,
    }
