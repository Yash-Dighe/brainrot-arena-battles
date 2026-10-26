"""
Ring escape: config search and diagnostics. Never edits escape.json --
it prints candidates; copy the one you want in by hand.

    python escape/tune.py --why 100                  # why seeds 0-99 get rejected
    python escape/tune.py --search 600               # random config search
    python escape/tune.py --search 600 --step 4 12   # wider spin-step range

The search samples a ring count, a gap curve (hook -> middle -> boss), base
spin, spin step and boss push, scores each candidate by how many of seeds
0-59 are usable, then re-checks the top 8 on held-out seeds 1000-1099.
Trust the holdout number, not the train one. One sim is ~27ms, so 600
candidates take ~3 min on all cores.
"""
import argparse, collections, json, multiprocessing as mp, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402

BASE = engine.load_config()
R_IN, R_OUT = 120, 505      # inner ring radius; outer ring stays inside the frame


def build(c):
    cfg = json.loads(json.dumps(BASE))
    r = cfg["rings"]
    n = len(c["gaps"])
    cfg["shape"]["boss_rings"] = c["nb"]
    r["gap_deg"], r["spin_deg_per_sec"], r["spin_step"] = c["gaps"], c["spin"], c["step"]
    r["inner_radius"], r["boss_push_px"] = R_IN, c["push"]
    r["spacing"] = round((R_OUT - R_IN - c["push"]) / (n - 1), 2)
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


def candidate(rng, step_lo, step_hi):
    n = rng.randint(14, 24)
    nb = rng.choice([2, 3, 4])
    nh = round(n * rng.uniform(0.35, 0.55))
    nm = n - nb - nh
    a = rng.uniform(30, 90)
    b = min(a, rng.uniform(15, a))
    c = rng.uniform(15, b) if b > 15 else b
    d = rng.uniform(10, min(c, 30))
    e = rng.uniform(6, d)
    hook = [a + (b - a) * i / max(1, nh - 1) for i in range(nh)]
    mid = [b + (c - b) * (i + 1) / nm for i in range(nm)]
    boss = [d + (e - d) * i / (nb - 1) for i in range(nb)]
    return {"nb": nb, "gaps": [round(x, 1) for x in hook + mid + boss],
            "spin": round(rng.choice([-1, 1]) * rng.uniform(25, 110), 1),
            "step": round(rng.uniform(step_lo, step_hi), 2),
            "push": rng.choice([0, 0, 20, 40, 60])}


def search(n, seed, step_lo, step_hi):
    rng = random.Random(seed)
    cands = [candidate(rng, step_lo, step_hi) for _ in range(n)]
    with mp.Pool() as p:
        res = p.map(evaluate, [(c, range(60)) for c in cands])
        res.sort(key=lambda r: -r[0])
        top = res[:8]
        val = p.map(evaluate, [(c, range(1000, 1100)) for _, _, c in top])
    for (k, _, c), (v, w, _) in zip(top, val):
        print(f"train {k}/60  holdout {v}/100 (in window {w})  n={len(c['gaps'])}  "
              + json.dumps(c))


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
        elif m["duration"] < vc["min_seconds"]:
            out["short"] += 1
        elif m["duration"] > vc["max_seconds"]:
            out["long"] += 1
        elif engine.score(log, cfg) >= bar:
            out["usable"] += 1
        else:
            out["low score"] += 1
            t = engine.shape_terms(log, cfg)
            weak[min(("hook", "middle", "growth", "boss", "stall"), key=t.get)] += 1
    print(dict(out), " weakest term among low scores:", dict(weak))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", type=int, default=0, help="candidates to try")
    ap.add_argument("--seed", type=int, default=1, help="search RNG seed")
    ap.add_argument("--step", type=float, nargs=2, default=[0.5, 4.0],
                    help="spin_step range, deg/s")
    ap.add_argument("--why", type=int, default=0, help="seeds to diagnose")
    args = ap.parse_args()
    if args.why:
        why(args.why)
    if args.search:
        search(args.search, args.seed, *args.step)


if __name__ == "__main__":
    main()
