# Brainrot Arena

Automated 1v1 physics ball-battle videos for TikTok / YouTube Shorts.

## Setup

Requires Python 3.10+ and ffmpeg.

    # macOS
    brew install ffmpeg
    # Ubuntu/Debian
    sudo apt install ffmpeg

    pip install -r requirements.txt

## Run a fight

    python sim/engine.py --a tralalero --b boneca --seed 0
    python sim/engine.py --find 20        # search seeds for a competitive fight
    python sim/engine.py                  # random matchup

Writes an event log to `out/<a>_vs_<b>_<seed>.json`. Nothing is drawn yet —
the renderer (step 3) is the next thing to build.

Character ids: `tralalero`, `bombardiro`, `tungtung`, `ballerina`, `boneca`,
`cappuccino`, `brrbrr`, `chimpanzini`, `lirili`

## Layout

    characters.json    all balance numbers + signature FX (edit this, not code)
    sim/engine.py      step 2 - headless physics, emits event log
    sim/render.py      step 3 - NOT BUILT YET
    assets/balls/      drop <id>.png here for character art
    out/               generated logs and videos (gitignored)

`CLAUDE.md` holds the full project context and is read automatically by
Claude Code.
