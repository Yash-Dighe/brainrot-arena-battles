"""
Ring escape: headless sim.

A ball bounces under gravity inside a stack of concentric rings. Each ring
spins and has one gap. When the ball slips through the gap of the ring it is
trapped in, that ring breaks and the next one out becomes the wall. The run
ends when the last ring breaks.

Same discipline as the arena: the sim decides everything and emits a log,
the renderer only draws it. Deterministic by seed.

    python escape/engine.py --seed 7                 # one run -> out/escape_7.json
    python escape/engine.py --find 200               # best of 200 seeds
    python escape/engine.py --survey 100             # duration spread, no output
"""
import argparse, json, math, os, random, sys

W, H = 1080, 1920
FPS = 60
CX, CY = W / 2, H / 2
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "escape.json")


def load_config(path=CONFIG_PATH):
    with open(path) as f:
        return json.load(f)


def wrap(a):
    """Angle into (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def make_rings(cfg, rng):
    """Every gap starts lined up at t=0; each ring spins `spin_step` deg/s
    faster than the one inside it, so the gaps drift apart over the run.

    Per-ring gap widths come straight from the config list, inner first.
    The difficulty curve is physical, not scripted: early on the aligned
    gaps let the ball fly through several rings at once, and as they drift
    apart each ring has to be escaped on its own.
    """
    rc = cfg["rings"]
    n, nb = len(rc["gap_deg"]), rc["boss_rings"]
    phase0 = rng.uniform(0, 360)
    rings = []
    for i, gap in enumerate(rc["gap_deg"]):
        spin = rc["spin_deg_per_sec"] + rc["spin_step"] * i
        if rc["alternate"] and i % 2:
            spin = -spin
        rings.append({
            # the boss rings sit apart from the rest, as a final wall
            "r": (rc["inner_radius"] + rc["spacing"] * i
                  + (rc["boss_push_px"] if i >= n - nb else 0)),
            "width": rc["width"],
            "gap": gap,
            "spin": spin,
            "phase": phase0,
        })
    return rings


def ring_angle(ring, t):
    """Centre of the gap, degrees, screen coordinates (y down)."""
    return (ring["phase"] + ring["spin"] * t) % 360.0


def simulate(seed, cfg=None):
    cfg = cfg or load_config()
    rng = random.Random(seed)
    bc, pc, vc = cfg["ball"], cfg["physics"], cfg["video"]
    rings = make_rings(cfg, rng)
    br = bc["radius"]
    g, e = pc["gravity"], pc["restitution"]
    sub = pc["substeps"]
    dt = 1.0 / (FPS * sub)
    vmax = bc["max_speed"]
    near_deg = pc["near_miss_deg"]

    x, y = CX, CY
    a = rng.uniform(0, 2 * math.pi)
    vx, vy = bc["launch_speed"] * math.cos(a), bc["launch_speed"] * math.sin(a)

    k = 0                 # index of the ring the ball is trapped in
    passing = False       # ball is inside ring k's gap, on its way out
    frames, events = [], []
    t, end_t = 0.0, None
    fi = 0

    while True:
        t = fi / FPS
        frames.append({
            "t": round(t, 4), "x": round(x, 2), "y": round(y, 2),
            "vx": round(vx, 1), "vy": round(vy, 1),
            "a": [round(ring_angle(r, t), 2) if i >= k else None
                  for i, r in enumerate(rings)],
        })
        if end_t is not None and t >= end_t:
            break
        if t > vc["timeout"]:
            break

        for s in range(sub):
            ts = t + s * dt
            vy += g * dt
            x += vx * dt
            y += vy * dt
            sp = math.hypot(vx, vy)
            if sp > vmax:
                vx, vy = vx * vmax / sp, vy * vmax / sp
            if k >= len(rings):
                continue

            ring = rings[k]
            inner = ring["r"] - ring["width"] / 2
            outer = ring["r"] + ring["width"] / 2
            dx, dy = x - CX, y - CY
            d = math.hypot(dx, dy) or 1e-9

            if passing:
                if d - br > outer:
                    # clean through: the ring breaks
                    events.append({"type": "escape", "t": round(ts, 4),
                                   "ring": k, "x": round(x, 2), "y": round(y, 2),
                                   "angle": round(math.degrees(math.atan2(dy, dx)), 2),
                                   "speed": round(math.hypot(vx, vy), 1)})
                    k += 1
                    passing = False
                    if k >= len(rings):
                        end_t = ts + vc["tail_seconds"]
                elif d + br < inner:
                    passing = False          # fell back in: the closest miss there is
                    events.append({"type": "near_miss", "t": round(ts, 4), "ring": k,
                                   "kind": "lip", "margin": 0.0,
                                   "x": round(x, 2), "y": round(y, 2)})
                continue

            if d + br < inner:
                continue

            # Touching the wall. Is the whole ball inside the gap?
            phi = math.degrees(math.atan2(dy, dx))
            off = abs(wrap(phi - ring_angle(ring, ts)))
            half_ball = math.degrees(math.asin(min(1.0, br / ring["r"])))
            clear = ring["gap"] / 2 - half_ball   # widest offset that fits through
            if off < clear:
                passing = True
                continue

            nx, ny = dx / d, dy / d
            vn = vx * nx + vy * ny
            if vn > 0:
                vx -= (1 + e) * vn * nx
                vy -= (1 + e) * vn * ny
                events.append({"type": "bounce", "t": round(ts, 4), "ring": k,
                               "x": round(x, 2), "y": round(y, 2),
                               "speed": round(abs(vn), 1)})
                # missed the gap by only a few degrees: the ball clipped its edge
                if off - clear < near_deg:
                    events.append({"type": "near_miss", "t": round(ts, 4), "ring": k,
                                   "kind": "edge", "margin": round(off - clear, 2),
                                   "x": round(x, 2), "y": round(y, 2)})
            x, y = CX + nx * (inner - br), CY + ny * (inner - br)

        fi += 1

    escapes = [ev["t"] for ev in events if ev["type"] == "escape"]
    done = len(escapes) == len(rings)
    return {
        "meta": {
            "format": "escape", "seed": seed, "fps": FPS, "w": W, "h": H,
            "center": [CX, CY], "ball_radius": br, "rings": rings,
            "escaped": done,
            "duration": round(escapes[-1], 3) if done else None,
            "video_seconds": round(len(frames) / FPS, 3),
        },
        "frames": frames,
        "events": events,
    }


# -------------------------------------------------------------- selection
def segments(log):
    """Seconds each ring held the ball, inner first (only rings that broke)."""
    ts = [0.0] + [e["t"] for e in log["events"] if e["type"] == "escape"]
    return [b - a for a, b in zip(ts, ts[1:])]


def window(v, lo, hi):
    """1 inside [lo, hi], falling off smoothly outside it."""
    if v < lo:
        return v / lo
    if v > hi:
        return hi / v
    return 1.0


def shape_terms(log, cfg):
    """The burst -> dry -> escape shape, term by term, each 0..1 (1 = on target).

    burst    at least burst_rings rings gone inside the first burst_seconds
    cadence  mean gap between breaks after the burst inside
             middle_break_interval_lo..hi: occasional pops, not busy
    silence  no gap after the burst longer than silence_max_seconds
    escape   the last ring holds the ball at least finale_min_seconds
             before it breaks, so the ending reads as earned
    """
    sc = cfg["shape"]
    escapes = [e["t"] for e in log["events"] if e["type"] == "escape"]
    if not escapes:
        return {"burst": 0.0, "cadence": 0.0, "silence": 0.0, "escape": 0.0,
                "burst_n": 0, "interval": None}
    early = [t for t in escapes if t <= sc["burst_seconds"]]
    burst = min(1.0, len(early) / sc["burst_rings"])

    # gaps from the last burst break (or t=0) to each later break, final included
    ts = [early[-1] if early else 0.0] + escapes[len(early):]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    if gaps:
        interval = sum(gaps) / len(gaps)
        cadence = window(interval, sc["middle_break_interval_lo"],
                         sc["middle_break_interval_hi"])
        silence = min(1.0, sc["silence_max_seconds"] / max(gaps)) ** 2
    else:
        interval, cadence, silence = None, 0.0, 0.0   # burst swallowed every ring

    final = escapes[-1] - (escapes[-2] if len(escapes) > 1 else 0.0)
    escape = min(1.0, final / sc["finale_min_seconds"])
    return {"burst": burst, "cadence": cadence, "silence": silence, "escape": escape,
            "burst_n": len(early), "interval": interval}


def score(log, cfg):
    """0..1, higher is better. None if the run is unusable.

    Unusable: no escape (timeout), or a video outside the duration window
    (the 65s floor is the whole business case). Usable runs are scored on
    the burst -> dry -> escape shape; terms combine as a geometric mean, so
    any one failing sinks the run.
    """
    m, vc = log["meta"], cfg["video"]
    if not m["escaped"]:
        return None
    # max bounds the whole video, end tail included
    if not (vc["min_seconds"] <= m["duration"] <= vc["max_seconds"] - vc["tail_seconds"]):
        return None
    t = shape_terms(log, cfg)
    s = (t["burst"] * t["cadence"] * t["silence"] * t["escape"]) ** 0.25
    return round(s, 3)


def find_best(n, cfg=None, start=0):
    cfg = cfg or load_config()
    best = None
    for seed in range(start, start + n):
        log = simulate(seed, cfg)
        s = score(log, cfg)
        if s is not None and (best is None or s > best[0]):
            best = (s, seed, log)
    return best


def summarize(log):
    m = log["meta"]
    n = sum(1 for e in log["events"] if e["type"] == "bounce")
    if not m["escaped"]:
        k = sum(1 for e in log["events"] if e["type"] == "escape")
        return f"seed {m['seed']}: TIMEOUT after {k}/{len(m['rings'])} rings"
    nm = sum(1 for e in log["events"] if e["type"] == "near_miss")
    return f"seed {m['seed']}: escaped in {m['duration']:.1f}s, {n} bounces, {nm} near-misses"


def survey(n, cfg):
    """Duration spread, usable rate, and how long each ring holds the ball."""
    bar = cfg["shape"]["usable_score"]
    durs, scores, fails = [], [], 0
    seg = [[] for _ in cfg["rings"]["gap_deg"]]
    for seed in range(n):
        log = simulate(seed, cfg)
        for i, g in enumerate(segments(log)):
            seg[i].append(g)
        if not log["meta"]["escaped"]:
            fails += 1
            continue
        durs.append(log["meta"]["duration"])
        s = score(log, cfg)
        if s is not None:
            scores.append(s)

    def q(v, p):
        return v[min(len(v) - 1, int(p * len(v)))]

    ok = sum(s >= bar for s in scores)
    print(f"{len(durs)}/{n} escaped, {fails} timeouts; {len(scores)}/{n} in the "
          f"duration window; usable (score >= {bar}) {ok}/{n}")
    if durs:
        durs.sort()
        print(f"duration  min {durs[0]:.1f}  p25 {q(durs, .25):.1f}  "
              f"median {q(durs, .5):.1f}  p75 {q(durs, .75):.1f}  max {durs[-1]:.1f}")
    print("ring   p10   med   p90   (seconds the ring held the ball)")
    for i, v in enumerate(seg):
        v.sort()
        if v:
            print(f"{i:4d} {q(v, .1):5.1f} {q(v, .5):5.1f} {q(v, .9):5.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--find", type=int, default=0, help="search N seeds, keep the best")
    ap.add_argument("--survey", type=int, default=0,
                    help="print the duration spread over N seeds and exit")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = load_config()

    if args.survey:
        survey(args.survey, cfg)
        return

    if args.find:
        best = find_best(args.find, cfg)
        if best is None:
            sys.exit(f"no usable run in {args.find} seeds; see --survey")
        s, seed, log = best
        print(f"best of {args.find}: score {s}  " + summarize(log))
    else:
        seed = args.seed if args.seed is not None else random.randrange(10**6)
        log = simulate(seed, cfg)
        print(summarize(log), f"score {score(log, cfg)}")

    out = args.out or os.path.join(ROOT, "out", f"escape_{log['meta']['seed']}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(log, f, separators=(",", ":"))
    print(f"  -> {out}")


if __name__ == "__main__":
    main()
