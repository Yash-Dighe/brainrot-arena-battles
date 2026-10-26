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
    rc = cfg["rings"]
    phase0 = rng.uniform(0, 360)
    rings = []
    for i in range(rc["count"]):
        spin = rc["spin_deg_per_sec"] * (1.0 + rc["spin_step"] * i)
        if rc["alternate"] and i % 2:
            spin = -spin
        rings.append({
            "r": rc["inner_radius"] + rc["spacing"] * i,
            "width": rc["width"],
            "gap": rc["gap_deg"],
            "spin": spin,
            "phase": (phase0 + rc["spiral_deg"] * i) % 360.0,
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
                    passing = False          # fell back in
                continue

            if d + br < inner:
                continue

            # Touching the wall. Is the whole ball inside the gap?
            phi = math.degrees(math.atan2(dy, dx))
            off = abs(wrap(phi - ring_angle(ring, ts)))
            half_ball = math.degrees(math.asin(min(1.0, br / ring["r"])))
            if off < ring["gap"] / 2 - half_ball:
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
def score(log, cfg):
    """0..1, higher is better. None if the run is unusable.

    Unusable: didn't finish, or finished outside the duration window (the
    60s floor is the whole business case). Among usable runs, prefer even
    pacing -- a 25s drought on one ring is where viewers leave -- and a
    finish that speeds up a little rather than slows down.
    """
    m, vc = log["meta"], cfg["video"]
    if not m["escaped"]:
        return None
    dur = m["duration"]
    if not (vc["min_seconds"] <= dur <= vc["max_seconds"]):
        return None
    ts = [0.0] + [e["t"] for e in log["events"] if e["type"] == "escape"]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    mean = sum(gaps) / len(gaps)
    worst = max(gaps) / mean                  # 1.0 = perfectly even
    half = len(gaps) // 2
    accel = (sum(gaps[:half]) / half) / (sum(gaps[half:]) / (len(gaps) - half))
    s = 1.0 / worst                           # droughts dominate
    s *= min(1.0, 0.75 + 0.25 * accel)        # mild bonus for a faster finish
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
    return f"seed {m['seed']}: escaped in {m['duration']:.1f}s, {n} bounces"


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
        durs, fails, ok = [], 0, 0
        for seed in range(args.survey):
            log = simulate(seed, cfg)
            if log["meta"]["escaped"]:
                durs.append(log["meta"]["duration"])
                ok += score(log, cfg) is not None
            else:
                fails += 1
        durs.sort()
        if durs:
            q = lambda p: durs[min(len(durs) - 1, int(p * len(durs)))]
            print(f"{len(durs)}/{args.survey} escaped, {fails} timeouts; "
                  f"usable {ok}/{args.survey}")
            print(f"duration  min {durs[0]:.1f}  p25 {q(.25):.1f}  "
                  f"median {q(.5):.1f}  p75 {q(.75):.1f}  max {durs[-1]:.1f}")
        else:
            print(f"0/{args.survey} escaped")
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
