"""
Fight-shape scoring: is this fight worth rendering?

The old gate only asked "did it last long enough and was the final margin
close?". That cannot tell a nail-biter apart from a fight that was decided at
20s and then ground out for another minute -- the final HP looks the same, but
one is watchable and the other is dead air.

So we build a win-probability curve over the fight and score its SHAPE. The
theory is settled in the sports-analytics literature: suspense is the variance
of the next-period belief, surprise is the distance between consecutive
beliefs, and both independently predict whether people keep watching. Game
Excitement Index is the standard practical implementation, with the known
correction that one sudden lurch beats a drip of small changes.

We have no trained win model and don't need one -- time-to-death is a perfectly
good proxy:

    dps_i  = EWMA of the damage landing on ball i
    ttd_i  = hp_i / dps_i            seconds until i dies
    lead   = (ttd_0 - ttd_1) / (ttd_0 + ttd_1)
    p      = sigmoid(K * lead)       P(ball 0 wins)

The sim runs in ~0.07s and a render costs ~250s, so it is nearly free to
simulate hundreds of seeds and render only the best-shaped one. That ratio is
the whole reason this module exists.
"""
import math

SAMPLE_HZ = 1.0        # win-probability series resolution
HALFLIFE = 6.0         # seconds, for the damage-rate EWMA
K = 4.0                # sigmoid steepness on the ttd lead
EPS = 1e-9

# -- hard gates. A fight failing any of these is rejected before scoring. ----
MIN_SEC, MAX_SEC = 62.0, 75.0
DECIDED_EARLY = 0.90   # p this lopsided before 60% through = dead air
LATE_FRAC = 0.60       # "late" means after this fraction of the fight
COMEBACK_P = 0.40      # winner must have been at least this buried, OR...
SHOCK_DP = 0.15        # ...a 2-second belief swing at least this violent
# Calibrated against the actual damage curve, not picked from theory. With
# exp(t/TAU) ramping against a ~90k pool, the first hit worth 1% of the pool
# does not land until ~61% through -- asking for that by 25% is unsatisfiable
# by construction. 0.1% by 40% is achievable and still rejects the fights that
# stay flat longest. The genuinely weak opening is covered by the cold open,
# not by this gate.
FIRST_HIT_FRAC = 0.001
FIRST_HIT_BY = 0.40
FINAL_MARGIN = 0.55    # winner may not finish above this HP fraction
ACCEPT = 0.60          # minimum shape score


def clip01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def prob_series(frames, max_hp, fps=60):
    """P(ball 0 wins) sampled at SAMPLE_HZ, derived from time-to-death."""
    step = max(1, int(round(fps / SAMPLE_HZ)))
    alpha = 1.0 - 0.5 ** (1.0 / HALFLIFE)
    dps = [0.0, 0.0]
    prev = [float(max_hp[0]), float(max_hp[1])]
    out = []
    for f in frames[::step]:
        hp = [float(f["balls"][i]["hp"]) for i in (0, 1)]
        ttd = []
        for i in (0, 1):
            loss = max(0.0, prev[i] - hp[i])
            dps[i] += alpha * (loss - dps[i])
            prev[i] = hp[i]
            ttd.append(hp[i] / dps[i] if dps[i] > EPS else 1e6)
        lead = (ttd[0] - ttd[1]) / (ttd[0] + ttd[1] + EPS)
        out.append(1.0 / (1.0 + math.exp(-K * lead)))
    return out


def _spearman(xs):
    """Rank correlation of a series against time. +1 = monotonically rising."""
    n = len(xs)
    if n < 3:
        return 0.0
    order = sorted(range(n), key=lambda i: xs[i])
    rank = [0.0] * n
    for r, i in enumerate(order):
        rank[i] = float(r)
    mr = (n - 1) / 2.0
    num = sum((rank[i] - mr) * (i - mr) for i in range(n))
    den = math.sqrt(sum((rank[i] - mr) ** 2 for i in range(n))
                    * sum((i - mr) ** 2 for i in range(n)))
    return num / den if den > EPS else 0.0


def _lead_changes(ps, start=0):
    """Crossings of even odds, with hysteresis so noise doesn't count."""
    n, side = 0, None
    for p in ps[start:]:
        if p >= 0.60 and side != 1:
            n, side = n + (side is not None), 1
        elif p <= 0.40 and side != 0:
            n, side = n + (side is not None), 0
    return n


def evaluate(sim):
    """Score one fight. Returns a dict; `ok` is the render/no-render verdict."""
    frames = sim.frames
    fighters = sim.balls
    max_hp = [b.max_hp for b in fighters]
    T = sim.t
    win = sim.winner
    ps = prob_series(frames, max_hp)
    n = len(ps)
    if n < 8:
        return {"ok": False, "score": 0.0, "secs": T, "why": "too short to score",
                "p": ps, "metrics": {}}

    # P(the eventual winner wins), so every metric reads the same direction
    pw = ps if win == 0 else [1.0 - p for p in ps]
    dp = [abs(pw[i] - pw[i - 1]) for i in range(1, n)]
    late_i = int(n * LATE_FRAC)
    tail = pw[int(n * 0.75):] or pw[-1:]

    gei = (90.0 / max(T, EPS)) * sum(dp)
    tension = 2.0 * (sum(min(p, 1 - p) for p in tail) / len(tail))
    comeback = (0.5 - min(pw)) / 0.35
    lurch = max((abs(pw[i + 2] - pw[i]) for i in range(n - 2)), default=0.0)
    changes = _lead_changes(pw)
    late_changes = _lead_changes(pw[late_i:])

    # is escalation actually monotone? damage per 10s bucket vs time
    buckets = {}
    for e in sim.events:
        if "dmg" in e:
            k = int(e["t"] // 10)
            buckets[k] = buckets.get(k, 0) + e["dmg"]
    ramp_src = [buckets.get(k, 0) for k in range(int(T // 10) + 1)]
    ramp = _spearman(ramp_src) if len(ramp_src) >= 3 else 0.0

    big = FIRST_HIT_FRAC * max(max_hp)
    first_big = next((e["t"] for e in sim.events if e.get("dmg", 0) >= big), None)
    final_frac = fighters[win].hp / fighters[win].max_hp

    score = (0.20 * clip01(gei / 3.0) + 0.25 * clip01(tension)
             + 0.20 * clip01(comeback) + 0.15 * clip01(lurch / 0.25)
             + 0.10 * clip01(changes / 3.0) + 0.10 * clip01(ramp))

    fails = []
    if not (MIN_SEC <= T <= MAX_SEC):
        fails.append(f"duration {T:.1f}s")
    if any(max(p, 1 - p) >= DECIDED_EARLY for p in pw[:late_i]):
        fails.append("decided early")
    if min(pw) >= COMEBACK_P and late_changes < 1:
        fails.append("no late drama")
    if lurch < SHOCK_DP:
        fails.append("no real swing")
    if first_big is None or first_big > FIRST_HIT_BY * T:
        fails.append("slow open")
    if final_frac > FINAL_MARGIN:
        fails.append(f"margin {final_frac*100:.0f}%")
    if score < ACCEPT:
        fails.append(f"score {score:.2f}")

    return {
        "ok": not fails, "score": score, "secs": T, "why": ", ".join(fails),
        "final_frac": final_frac, "p": ps,
        "metrics": {"gei": gei, "tension": tension, "comeback": comeback,
                    "lurch": lurch, "changes": changes,
                    "late_changes": late_changes, "ramp": ramp,
                    "first_big": first_big},
    }
