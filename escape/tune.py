"""
Ring escape: config search and diagnostics. Never edits escape.json --
it prints candidates; copy the one you want in by hand.

    python escape/tune.py --why 100                  # why seeds 0-99 get rejected
    python escape/tune.py --search 600               # random config search
    python escape/tune.py --search 600 --jobs 1      # same, serial
    python escape/tune.py --sweep 10 14 17 --search 120 --jobs 1   # per ring count

The search fixes a ring count, fits the geometry to it (fit()), sets the
dry-middle cadence for that count (shape_for()), then samples a gap curve
(burst -> middle -> final wall), base spin, spin step and push. Each
candidate is scored by how many of the train seeds are usable, then the top
few are re-checked on held-out seeds from 1000. Trust the holdout number,
not the train one. Every result is appended to --out as it lands, so a
crash loses nothing already scored.
"""
import argparse, collections, json, multiprocessing as mp, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402

BASE = engine.load_config()
R_OUT = 520                 # outer ring stays inside the 1080-wide frame
TERMS = ("burst", "cadence", "silence", "escape")


def fit(n):
    """Geometry for n rings between the inner radius and R_OUT: the ball
    (diameter) plus one ring width must fit in the spacing, so the ball
    never looks wider than the lane it bounces in. Small counts keep the
    roomier 120px core; past 17 rings it shrinks to 70 to buy spacing."""
    r_in = 120 if (R_OUT - 120) / (n - 1) >= 24 else 70
    sp = (R_OUT - r_in) / (n - 1)
    width = max(4, min(8, round(sp / 4)))
    br = min(15, int((sp - width) / 2))
    if br < 4:
        raise ValueError(f"{n} rings do not fit: spacing {sp:.1f}px")
    return {"inner_radius": r_in, "spacing": round(sp, 2), "width": width, "radius": br}


def shape_for(n):
    """Dry-middle cadence for n rings. The escape lands at 59-67s (a 62-70s
    video minus the tail) and the slam ends by ~4s, so ~55-66s is spread
    over the rings left after the burst: at least the 5-ring slam, at most
    60% of the rings, so some are always left for the middle."""
    sc = BASE["shape"]
    nb_lo, nb_hi = sc["burst_min_rings"], max(sc["burst_min_rings"] + 1, round(0.6 * n))
    return {"middle_break_interval_lo": round(55 / (n - nb_lo), 1),
            "middle_break_interval_hi": round(66 / (n - nb_hi), 1)}


def build(c):
    cfg = json.loads(json.dumps(BASE))
    r = cfg["rings"]
    n = len(c["gaps"])
    g = fit(n)
    r["boss_rings"] = c["nb"]
    r["gap_deg"], r["spin_deg_per_sec"], r["spin_step"] = c["gaps"], c["spin"], c["step"]
    r["inner_radius"], r["boss_push_px"], r["width"] = g["inner_radius"], c["push"], g["width"]
    r["spacing"] = round((R_OUT - g["inner_radius"] - c["push"]) / (n - 1), 2)
    cfg["ball"]["radius"] = g["radius"]
    cfg["shape"].update(shape_for(n))
    return cfg


def evaluate(args):
    c, seeds = args
    cfg = build(c)
    ok = win = 0
    for s in seeds:
        sc = engine.score(engine.simulate(s, cfg), cfg)
        if sc is not None:
            win += 1
            ok += sc >= cfg["shape"]["usable_score"]
    return ok, win, c


def candidate(rng, n, step_lo, step_hi):
    nb = rng.choice([1, 2, 3])
    # the slam needs at least burst_min_rings wide, aligned gaps
    nh = max(BASE["shape"]["burst_min_rings"], round(n * rng.uniform(0.35, 0.6)))
    nm = n - nb - nh
    a = rng.uniform(40, 95)
    b = min(a, rng.uniform(25, a))
    c = rng.uniform(12, b)
    d = rng.uniform(8, min(c, 35))
    e = rng.uniform(6, d)
    burst = [a + (b - a) * i / max(1, nh - 1) for i in range(nh)]
    mid = [b + (c - b) * (i + 1) / nm for i in range(nm)]
    wall = [d + (e - d) * i / max(1, nb - 1) for i in range(nb)]
    return {"nb": nb, "gaps": [round(x, 1) for x in burst + mid + wall],
            "spin": round(rng.choice([-1, 1]) * rng.uniform(25, 110), 1),
            "step": round(rng.uniform(step_lo, step_hi), 2),
            # push squeezes the spacing; fit() leaves no slack for it
            "push": 0}


def run_all(jobs, tasks, sink):
    """evaluate() over tasks, each result passed to sink as it lands.

    - jobs 1 runs in-process, no pool
    - pool workers recycle every 20 tasks
    """
    if jobs == 1:
        for t in tasks:
            sink(evaluate(t))
        return
    with mp.get_context("spawn").Pool(jobs, maxtasksperchild=20) as p:
        for r in p.imap_unordered(evaluate, tasks, chunksize=1):
            sink(r)


def search(n, rings, seed, step_lo, step_hi, jobs, out, train=60, holdout=100, top=8):
    """Best configs for a fixed ring count; returns [(holdout_ok, c)], best first."""
    rng = random.Random(seed)
    cands = [candidate(rng, rings, step_lo, step_hi) for _ in range(n)]
    res = []
    with open(out, "a") as f:
        def keep(r):
            res.append(r)
            f.write(json.dumps({"train": r[0], "win": r[1], "c": r[2]}) + "\n")
            f.flush()
        run_all(jobs, [(c, range(train)) for c in cands], keep)
    res.sort(key=lambda r: -r[0])
    best = [c for _, _, c in res[:top]]
    tr = {json.dumps(c): k for k, _, c in res[:top]}
    val = []
    run_all(jobs, [(c, range(1000, 1000 + holdout)) for c in best], val.append)
    val.sort(key=lambda r: -r[0])
    for v, w, c in val:
        print(f"train {tr[json.dumps(c)]}/{train}  holdout {v}/{holdout} (in window {w})  "
              f"n={len(c['gaps'])}  " + json.dumps(c), flush=True)
    return [(v, c) for v, _, c in val]


def sweep(counts, n, seed, step, jobs, out, train, holdout):
    """Search each ring count, then report the winner's rates on fresh
    seeds (2000+), which neither the train nor the pick saw."""
    rows = []
    for rings in counts:
        print(f"--- {rings} rings: {fit(rings)}  {shape_for(rings)}", flush=True)
        best = search(n, rings, seed, *step, jobs, out, train, top=4)
        c = best[0][1]
        st = engine.stats(build(c), range(2000, 2000 + holdout))
        rows.append((rings, st, c))
        with open(out, "a") as f:
            f.write(json.dumps({"sweep": rings, "stats": st, "c": c}) + "\n")
        print(f"=== {rings} rings holdout {holdout}: " + json.dumps(st), flush=True)
    print("\nrings  median  window  slam  usable  escaped")
    for rings, st, _ in rows:
        print(f"{rings:5d}  {st['median'] or 0:6.1f}  {st['window']:6.0%}  {st['slam']:4.0%}"
              f"  {st['usable']:6.0%}  {st['escaped']:7.0%}")


def why(n):
    """Tally why each seed is rejected, and the weakest shape term."""
    cfg = BASE
    vc, bar = cfg["video"], cfg["shape"]["usable_score"]
    out, weak = collections.Counter(), collections.Counter()
    for s in range(n):
        log = engine.simulate(s, cfg)
        m = log["meta"]
        if not m["escaped"]:
            out["timeout"] += 1
        elif m["duration"] + vc["tail_seconds"] < vc["min_seconds"]:
            out["short"] += 1
        elif m["duration"] + vc["tail_seconds"] > vc["max_seconds"]:
            out["long"] += 1
        elif engine.score(log, cfg) >= bar:
            out["usable"] += 1
        else:
            out["low score"] += 1
            t = engine.shape_terms(log, cfg)
            weak[min(TERMS, key=t.get)] += 1
    print(dict(out), " weakest term among low scores:", dict(weak))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", type=int, default=0, help="candidates to try")
    ap.add_argument("--seed", type=int, default=1, help="search RNG seed")
    ap.add_argument("--step", type=float, nargs=2, default=[0.5, 4.0],
                    help="spin_step range, deg/s")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)),
                    help="worker processes; 1 = serial")
    ap.add_argument("--out", default=os.path.join(engine.ROOT, "out", "tune", "search.jsonl"),
                    help="append every train result here")
    ap.add_argument("--rings", type=int, default=17, help="ring count for --search")
    ap.add_argument("--sweep", type=int, nargs="+", default=None,
                    help="ring counts to search one after another")
    ap.add_argument("--train", type=int, default=60, help="train seeds per candidate")
    ap.add_argument("--holdout", type=int, default=200, help="sweep holdout seeds")
    ap.add_argument("--why", type=int, default=0, help="seeds to diagnose")
    args = ap.parse_args()
    if args.why:
        why(args.why)
    if args.search:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        if args.sweep:
            sweep(args.sweep, args.search, args.seed, args.step, args.jobs, args.out,
                  args.train, args.holdout)
        else:
            search(args.search, args.rings, args.seed, *args.step, args.jobs, args.out,
                   args.train)


if __name__ == "__main__":
    main()
