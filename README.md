# Brainrot Arena

Automated 1v1 physics ball-battle videos for TikTok / YouTube Shorts.

## Setup

Requires Python 3.10+ and ffmpeg.

    # macOS
    brew install ffmpeg
    # Ubuntu/Debian
    sudo apt install ffmpeg
    # Windows (no admin needed): unzip a static build somewhere and put its
    # bin/ on your PATH -- https://www.gyan.dev/ffmpeg/builds/

    pip install -r requirements.txt

The renderer finds ffmpeg via `--ffmpeg`, then `$FFMPEG`, then your PATH.

## Run a fight

    python sim/engine.py --a tralalero --b boneca --seed 0
    python sim/engine.py --find 20        # search seeds for a competitive fight
    python sim/engine.py                  # random matchup

Writes an event log to `out/<a>_vs_<b>_<seed>.json`. The log is the whole
contract with the renderer: the sim decides everything, the renderer only
draws what the log says.

Character ids: `tralalero`, `bombardiro`, `tungtung`, `ballerina`, `boneca`,
`cappuccino`, `brrbrr`, `chimpanzini`, `lirili`

## Render it

    python sim/render.py out/tralalero_vs_boneca_0.json
    python sim/render.py <log> --out out/fight.mp4
    python sim/render.py <log> --preview 8      # first 8s only, fast iteration
    python sim/render.py <log> --at 74.5        # dump one PNG, no ffmpeg needed

1080x1920 at 60fps. Budget roughly 2s of render per second of video, so a
fight takes a few minutes. Use `--at` while tuning the look -- it renders a
single frame in under a second.

Drop character art at `assets/balls/<id>.png` (transparent square). Missing
art falls back to a coloured sphere with the character's initials.

## Check balance

    python sim/sweep.py                   # all 36 matchups x 12 seeds
    python sim/sweep.py --a brrbrr -v     # one character, per-matchup detail

Exits non-zero if any matchup can't produce a good fight within the seed
budget. Run it after editing `characters.json`.

## Layout

    characters.json    all balance numbers + signature FX (edit this, not code)
    sim/engine.py      step 2 - headless physics, emits event log
    sim/sweep.py       balance gate - sweeps every matchup, no rendering
    sim/render.py      step 3 - draws every frame, pipes to ffmpeg
    assets/balls/      drop <id>.png here for character art
    out/               generated logs and videos (gitignored)

`CLAUDE.md` holds the full project context and is read automatically by
Claude Code.
