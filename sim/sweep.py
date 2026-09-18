"""
Balance sweep: run every matchup over a range of seeds and report whether the
roster holds up. Draws nothing and writes nothing -- this is a tuning
instrument for characters.json, not part of the render pipeline.

  python sim/sweep.py                  # all 36 matchups, 40 seeds each
  python sim/sweep.py --seeds 80 -v    # per-matchup detail
  python sim/sweep.py --a bombardiro   # only matchups involving one character

A matchup PASSES if at least one seed clears every gate in sim/score.py. That
is the same bar `engine.find_best` uses, so "passes within N seeds" means
"--find N will reliably produce a video for this pairing".

Since we now render the best-scoring fight rather than the first acceptable
one, the useful signal is not just whether a matchup passes but how GOOD its
best fight is -- a matchup whose ceiling is 0.62 makes worse videos than one
that reaches 0.95, even though both "pass".
"""
import argparse, itertools, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
import score as score_mod

BLOWOUT_HP_FRAC = 0.70      # winner keeping more than this = no drama


def sweep_matchup(roster, a_id, b_id, seeds):
    """Run one pairing across seeds; return per-seed results + a verdict."""
    rows = []
    for s in range(seeds):
        sim = engine.run_fight(roster, a_id, b_id, s)
        ev = score_mod.evaluate(sim)
        rows.append({"seed": s, "ok": ev["ok"], "secs": ev["secs"],
                     "frac": ev.get("final_frac", 1.0), "score": ev["score"],
                     "why": ev["why"]})
    passing = [r for r in rows if r["ok"]]
    return {
        "a": a_id, "b": b_id, "rows": rows,
        "passed": bool(passing),
        "first_pass": passing[0]["seed"] if passing else None,
        "best": max((r["score"] for r in passing), default=0.0),
        "pass_rate": len(passing) / len(rows),
        "min_secs": min(r["secs"] for r in rows),
        "med_secs": statistics.median(r["secs"] for r in rows),
        "med_frac": statistics.median(r["frac"] for r in rows),
        "blowouts": sum(1 for r in rows if r["frac"] > BLOWOUT_HP_FRAC),
        "short": sum(1 for r in rows if r["secs"] < score_mod.MIN_SEC),
        "long": sum(1 for r in rows if r["secs"] > score_mod.MAX_SEC),
    }


def common_failures(m, k=3):
    """Which gates this matchup trips most often, so tuning has a target."""
    tally = {}
    for r in m["rows"]:
        for w in filter(None, r["why"].split(", ")):
            # keep two words: "no late drama" and "no real swing" are very
            # different diagnoses and both start with "no"
            key = " ".join(w.split()[:2])
            tally[key] = tally.get(key, 0) + 1
    top = sorted(tally.items(), key=lambda kv: -kv[1])[:k]
    return ", ".join(f"{name} x{n}" for name, n in top) or "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40, help="seeds per matchup")
    ap.add_argument("--a", default=None, help="only matchups involving this id")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every matchup")
    args = ap.parse_args()

    roster = engine.load_roster()
    ids = list(roster)
    pairs = [(a, b) for a, b in itertools.combinations(ids, 2)
             if args.a is None or args.a in (a, b)]
    if not pairs:
        sys.exit(f"unknown character {args.a!r}; options: {', '.join(ids)}")

    print(f"TAU={engine.TAU}  window {score_mod.MIN_SEC:.0f}-{score_mod.MAX_SEC:.0f}s"
          f"  accept>={score_mod.ACCEPT:.2f}")
    print(f"{len(pairs)} matchups x {args.seeds} seeds "
          f"= {len(pairs) * args.seeds} sims\n")

    results = []
    for a, b in pairs:
        m = sweep_matchup(roster, a, b, args.seeds)
        results.append(m)
        if args.verbose:
            tag = f"seed {m['first_pass']}" if m["passed"] else "FAIL"
            print(f"  {a:>12} vs {b:<12} {tag:>7}  best {m['best']:.2f}  "
                  f"pass {m['pass_rate']*100:3.0f}%  "
                  f"med {m['med_secs']:5.1f}s  "
                  f"winHP {m['med_frac']*100:3.0f}%  |  {common_failures(m)}")

    failed = [m for m in results if not m["passed"]]
    all_rows = [r for m in results for r in m["rows"]]
    passing = [r for r in all_rows if r["ok"]]

    print(f"\n{'-'*70}")
    print(f"matchups passing within {args.seeds} seeds : "
          f"{len(results)-len(failed)}/{len(results)}")
    print(f"seed pass rate (all sims)              : "
          f"{len(passing)}/{len(all_rows)} "
          f"({len(passing)/len(all_rows)*100:.0f}%)")
    if passing:
        bests = [m["best"] for m in results if m["passed"]]
        print(f"best-fight score per matchup           : "
              f"min {min(bests):.2f}  median {statistics.median(bests):.2f}  "
              f"max {max(bests):.2f}")
        print(f"duration of PASSING fights             : "
              f"min {min(r['secs'] for r in passing):.1f}s  "
              f"median {statistics.median(r['secs'] for r in passing):.1f}s  "
              f"max {max(r['secs'] for r in passing):.1f}s")
    print(f"sims under the {score_mod.MIN_SEC:.0f}s floor            : "
          f"{sum(1 for r in all_rows if r['secs'] < score_mod.MIN_SEC)}/{len(all_rows)}")

    if failed:
        print(f"\nfailing matchups ({len(failed)}):")
        for m in sorted(failed, key=lambda m: -m["med_frac"]):
            print(f"  {m['a']:>12} vs {m['b']:<12} med {m['med_secs']:5.1f}s  "
                  f"winHP {m['med_frac']*100:3.0f}%   {common_failures(m)}")

    weak = sorted((m for m in results if m["passed"]), key=lambda m: m["best"])[:6]
    if weak:
        print("\nlowest ceilings (these make the worst videos even when they pass):")
        for m in weak:
            print(f"  {m['a']:>12} vs {m['b']:<12} best {m['best']:.2f}  "
                  f"pass {m['pass_rate']*100:3.0f}%")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
