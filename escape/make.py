"""
Ring escape, one command: best-of-N seed search -> render -> audio -> mux.

    python escape/make.py                  # search 200 seeds, render the best
    python escape/make.py --seed 197       # render a specific seed
    python escape/make.py --preview 10     # first 10s only, for a quick look
    python escape/make.py --song "fur elise"   # pick the bounce melody (default: by seed)
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine, audio  # noqa: E402

ROOT = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--seeds", type=int, default=200, help="seeds to search")
    ap.add_argument("--start", type=int, default=0, help="first seed of the search")
    ap.add_argument("--song", default=None,
                    help="bounce melody by name (songs.json); default rotates by seed")
    ap.add_argument("--preview", type=float, default=0)
    ap.add_argument("--keep", action="store_true", help="keep the silent mp4 and wav")
    args = ap.parse_args()

    cfg = engine.load_config()
    if args.seed is not None:
        log = engine.simulate(args.seed, cfg)
        print(engine.summarize(log), f"score {engine.score(log, cfg)}")
    else:
        print(f"searching {args.seeds} seeds...")
        best = engine.find_best(args.seeds, cfg, args.start)
        if best is None:
            sys.exit("no usable run; widen the search or retune escape.json")
        log = best[2]
        print(f"  score {best[0]}  " + engine.summarize(log))
    song = audio.pick_song(log["meta"]["seed"], args.song)
    log["meta"]["song"] = song["name"]
    print(f"  song: {song['name']} ({song['composer']}, {song['year']})")

    base = os.path.join(ROOT, "out", f"escape_{log['meta']['seed']}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".json", "w") as f:
        json.dump(log, f, separators=(",", ":"))

    cmd = [sys.executable, os.path.join(HERE, "render.py"), base + ".json"]
    if args.preview:
        cmd += ["--preview", str(args.preview)]
    if subprocess.run(cmd).returncode:
        sys.exit("render failed")

    audio.build(log).render(base + ".wav")
    audio.mux(base + ".mp4", base + ".wav", base + "_av.mp4")
    if not args.keep:
        os.remove(base + ".mp4")
        os.remove(base + ".wav")
    print(f"done -> {base}_av.mp4  [{song['name']}]")


if __name__ == "__main__":
    main()
