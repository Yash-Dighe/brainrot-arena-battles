"""
Balance sweep: run every matchup over a range of seeds and report whether the
roster holds up. Draws nothing, writes nothing -- this is a tuning instrument
for characters.json, not part of the render pipeline.

  python sim/sweep.py                  # all 36 matchups, 12 seeds each
  python sim/sweep.py --seeds 20 -v    # per-matchup detail
  python sim/sweep.py --a bombardiro   # only matchups involving one character

A matchup PASSES if at least one seed satisfies engine.quality() -- that is the
same bar the renderer's seed search uses, so "passes within N seeds" means
"--find N will reliably produce a video for this pairing".
"""
import argparse, itertools, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine

BLOWOUT_HP_FRAC = 0.70      # winner keeping more than this = no drama
FLOOR_SEC = 62.0            # the 60s TikTok floor, with margin


def sweep_matchup(roster, a_id, b_id, seeds):
    """Run one pairing across seeds; return per-seed results + a verdict."""
    rows = []
    for s in range(seeds):
        sim = engine.run_fight(roster, a_id, b_id, s)
        ok, secs, frac = engine.quality(sim)
        rows.append({"seed": s, "ok": ok, "secs": secs, "frac": frac})
    passing = [r for r in rows if r["ok"]]
    return {
        "a": a_id, "b": b_id, "rows": rows,
        "passed": bool(passing),
        "first_pass": passing[0]["seed"] if passing else None,
        "pass_rate": len(passing) / len(rows),
        "min_secs": min(r["secs"] for r in rows),
        "med_secs": statistics.median(r["secs"] for r in rows),
        "med_frac": statistics.median(r["frac"] for r in rows),
        "blowouts": sum(1 for r in rows if r["frac"] > BLOWOUT_HP_FRAC),
        "short": sum(1 for r in rows if r["secs"] < FLOOR_SEC),
        "long": sum(1 for r in rows if r["secs"] > 105.0),
    }


def why_failed(m):
    bits = []
    if m["short"]:
        bits.append(f"{m['short']}/{len(m['rows'])} under {FLOOR_SEC:.0f}s")
    if m["long"]:
        bits.append(f"{m['long']}/{len(m['rows'])} over 105s")
    if m["blowouts"]:
        bits.append(f"{m['blowouts']}/{len(m['rows'])} blowouts")
    return ", ".join(bits) or "no seed cleared quality()"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=12, help="seeds per matchup")
    ap.add_argument("--a", default=None, help="only matchups involving this id")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every matchup")
    args = ap.parse_args()

    roster = engine.load_roster()
    ids = list(roster)
    pairs = [(a, b) for a, b in itertools.combinations(ids, 2)
             if args.a is None or args.a in (a, b)]
    if not pairs:
        sys.exit(f"unknown character {args.a!r}; options: {', '.join(ids)}")

    print(f"TAU={engine.TAU}  {len(pairs)} matchups x {args.seeds} seeds "
          f"= {len(pairs) * args.seeds} sims\n")

    results = []
    for a, b in pairs:
        m = sweep_matchup(roster, a, b, args.seeds)
        results.append(m)
        if args.verbose:
            tag = f"seed {m['first_pass']}" if m["passed"] else "FAIL"
            print(f"  {a:>12} vs {b:<12} {tag:>7}  "
                  f"pass {m['pass_rate']*100:3.0f}%  "
                  f"min {m['min_secs']:5.1f}s  med {m['med_secs']:5.1f}s  "
                  f"winHP {m['med_frac']*100:3.0f}%  blowouts {m['blowouts']}")

    failed = [m for m in results if not m["passed"]]
    all_rows = [r for m in results for r in m["rows"]]
    total_blowouts = sum(m["blowouts"] for m in results)

    print(f"\n{'-'*66}")
    print(f"matchups passing within {args.seeds} seeds : "
          f"{len(results)-len(failed)}/{len(results)}")
    print(f"seed pass rate (all sims)              : "
          f"{sum(1 for r in all_rows if r['ok'])}/{len(all_rows)} "
          f"({sum(1 for r in all_rows if r['ok'])/len(all_rows)*100:.0f}%)")
    print(f"blowout sims (winner kept >{BLOWOUT_HP_FRAC*100:.0f}% HP)     : "
          f"{total_blowouts}/{len(all_rows)} "
          f"({total_blowouts/len(all_rows)*100:.0f}%)")
    print(f"blowout-heavy matchups (>1/2 of seeds) : "
          f"{sum(1 for m in results if m['blowouts'] > args.seeds / 2)}/{len(results)}")
    print(f"duration  min {min(r['secs'] for r in all_rows):.1f}s   "
          f"median {statistics.median(r['secs'] for r in all_rows):.1f}s   "
          f"max {max(r['secs'] for r in all_rows):.1f}s")
    print(f"sims under the {FLOOR_SEC:.0f}s floor            : "
          f"{sum(1 for r in all_rows if r['secs'] < FLOOR_SEC)}/{len(all_rows)}")

    if failed:
        print(f"\nfailing matchups ({len(failed)}):")
        for m in sorted(failed, key=lambda m: -m["med_frac"]):
            print(f"  {m['a']:>12} vs {m['b']:<12} med {m['med_secs']:5.1f}s  "
                  f"winHP {m['med_frac']*100:3.0f}%   {why_failed(m)}")

    worst = sorted(results, key=lambda m: -m["blowouts"])[:6]
    if worst and worst[0]["blowouts"]:
        print("\nmost lopsided matchups:")
        for m in worst:
            print(f"  {m['a']:>12} vs {m['b']:<12} "
                  f"{m['blowouts']}/{args.seeds} blowouts  "
                  f"med winHP {m['med_frac']*100:3.0f}%")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
