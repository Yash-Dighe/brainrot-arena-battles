"""
One command, one finished video: sim -> score -> render -> SFX -> mux.

    python tools/make_video.py --a tralalero --b boneca
    python tools/make_video.py                      # random legal matchup
    python tools/make_video.py --seeds 500 --keep   # wider search, keep parts

Every stage already works standalone; this just wires them together and fails
loudly at the first one that doesn't, so a bad fight never costs render time.
"""
import argparse, itertools, json, os, random, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
import engine, score  # noqa: E402

PY = sys.executable

# Two time-scalers escalate in lockstep, which pins the win probability near
# even with no swings -- those fights score ~0.3 and never clear the gate.
# See CLAUDE.md. Excluded from random matchmaking rather than gated later.
TIME_SCALERS = {"tralalero", "tungtung", "ballerina"}


def legal_pairs(ids):
    return [(a, b) for a, b in itertools.combinations(ids, 2)
            if not (a in TIME_SCALERS and b in TIME_SCALERS)]


def run(cmd):
    print(f"  $ {' '.join(os.path.basename(c) for c in cmd[:2])} ...", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"step failed: {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a"); ap.add_argument("--b")
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--keep", action="store_true",
                    help="keep the silent mp4 and the bare wav")
    args = ap.parse_args()

    roster = engine.load_roster()
    ids = list(roster)
    if args.a and args.b:
        a_id, b_id = args.a, args.b
        if a_id in TIME_SCALERS and b_id in TIME_SCALERS:
            print(f"  warning: {a_id} and {b_id} both scale on time; these "
                  f"fights stay flat and usually fail the shape gate")
    else:
        a_id, b_id = random.choice(legal_pairs(ids))
    for i in (a_id, b_id):
        if i not in roster:
            sys.exit(f"unknown character {i!r}; have {', '.join(ids)}")

    t0 = time.time()
    print(f"{roster[a_id]['name']} vs {roster[b_id]['name']}")
    print(f"searching {args.seeds} seeds for the best-shaped fight...")
    found = engine.find_best(roster, a_id, b_id, args.seeds)
    if found is None:
        sys.exit(f"no playable fight in {args.seeds} seeds for this matchup")
    sim, ev = found
    m = ev["metrics"]
    print(f"  seed {sim.seed}: score {ev['score']:.2f}, {ev['secs']:.1f}s, "
          f"winner kept {ev['final_frac']*100:.0f}%, "
          f"{m['changes']} lead changes, biggest swing {m['lurch']:.2f}")

    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)
    log = os.path.join(out_dir, f"{a_id}_vs_{b_id}_{sim.seed}.json")
    with open(log, "w") as fh:
        json.dump(sim.to_log(), fh)

    run([PY, os.path.join(ROOT, "sim", "render.py"), log])
    run([PY, os.path.join(ROOT, "sim", "audio.py"), log, "--mux"])

    base = os.path.splitext(log)[0]
    final = base + "_av.mp4"
    if not args.keep:
        for junk in (log, base + ".mp4", base + ".wav"):
            try:
                os.remove(junk)
            except OSError:
                pass
    mb = os.path.getsize(final) / 1048576
    print(f"\n  {final}  ({mb:.1f} MB, {time.time() - t0:.0f}s total)")


if __name__ == "__main__":
    main()
