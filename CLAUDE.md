# Brainrot Arena — project context

> **Worker (Mac session): read `CHANNEL.md` before anything else.** It holds
> open messages from the coordinator, which relays Yash's decisions. Reply
> there and push.

## CURRENT FOCUS (2026-10-23): ring escape, in `escape/`

The project pivoted to character-free "satisfying" videos: a ball escapes
concentric spinning rings, each with a gap; a ring breaks when the ball passes
through it. Same discipline as below (config -> headless sim -> renderer that
only draws -> synthesized audio), same 60s+ and royalty-free constraints.

- `escape/escape.json` holds all tuning; `engine.py` is the sim
  (`--survey N` shows the duration spread, `--find N` keeps the best seed);
  `render.py` draws (`--at`, `--preview`); `audio.py` synthesizes SFX;
  `make.py` does search -> render -> audio -> mux in one command.
- Kept deliberately plain. Variants (growing ball, endless rings, racing
  balls) come after the plain version looks right.

**Progress-bar shape (2026-10-26), committed on master as `cb1ee3d`.** Target
shape: a hook that breaks ~half the rings in ~10s, middle rings that hold
4-8s each and get slower, then the last rings as a long "boss" stall with
near-misses, then a 3s end, 62-90s total. That commit hit 25/100 usable
using staggered gaps (`spiral_deg` 150) and gravity 5000.
- `near_miss` events: a bounce within `near_miss_deg` (6) of the gap edge,
  or a ball that enters the gap and falls back (`kind: lip`).
- `score()` is shape-based (`shape_terms()`; targets in `escape.json` under
  `shape`). Hook, middle, growth and boss terms combine as a geometric mean;
  an early stall multiplies the result. Runs outside 62-90s get None.
- Renderer: `k/N` counter plus a segmented progress bar; near-misses flash
  the ring white and throw a spark. Audio: a rising detuned whistle.
- **Near-misses are capped by geometry.** Near-misses per escape attempt
  ~= edge-zone width / passable gap width, and the outer ring only gets
  ~1-2 bounces/s. A boss with 10+ near-misses needs a gap so narrow that
  stalls run to ~100s. Raising this needs more bounces per second.
- **Boss time is close to memoryless** (p90 ~3x median). Splitting the boss
  across 3-4 moderately hard rings cuts the variance; one very hard ring
  does not.

**Invariant: seeds are not portable across machines.** The same seed, on
the same commit with the same `escape.json`, runs a different trajectory on
macOS than on Windows. The sim is chaotic, and small libm differences
between the platforms grow until the run diverges. Seed 62 escapes at
79.2s with score 1.0 on the Mac but runs to 142.5s with score None on
Windows; the aggregate rate still matches (55/400 usable on the Mac, 59/400
on Windows). So only the machine that renders may pick the seed, and seed
numbers never travel between machines. Rendering is now on the Mac, so Mac
seeds are the ones that count.

### HANDOFF (2026-10-26) -- branch `escape-aligned-rings`

Gaps are still aligned at t=0 and ring i still spins at
`spin_deg_per_sec + spin_step * i`. Gravity is 1500.

**Target shape changed (Yash):** a burst up front, then a long mostly-dry
stretch with occasional pops, then the ball escapes at the end, 65-90s
total. The near-miss and 4-8s-middle requirements are gone. They fought
the physics, which is why everything scored ~3%.
- `score()`/`shape_terms()` combine four terms as a geometric mean:
  `burst` (rings broken by `burst_seconds`), `cadence` (mean gap between
  breaks after the burst, inside `middle_break_interval_lo..hi`),
  `silence` (no gap longer than `silence_max_seconds`) and `escape` (the
  last ring holds at least `finale_min_seconds`). All targets live in
  `escape.json` under `shape`.
- Window: escape >= `min_seconds` (65), and escape + `tail_seconds` <=
  `max_seconds` (90), so the whole video fits.
- Near-miss events are still logged and drawn, but they don't score.
- `boss_rings` moved to `rings` (it only drives `boss_push_px`).
- `tune.py` crash-safety: spawn pool, capped `--jobs` (`1` = serial),
  every result appended to `--out` jsonl as it lands.

**Where it stands:** a 17-ring config (spin -89.7, step 3.93, 1 boss ring,
no push) scores **13.8% usable on a 400-seed holdout** (55/400). Only about
30% of runs land in the window, and about 45% of those are usable. The
spin step is 3.93 deg/s per ring, not "very slightly" different: inner
-89.7 to outer -26.8. Smaller steps searched worse.

**Blocker: duration spread, not shape.** The burst swallows 5-15 rings
depending on seed, and each later ring's hold time is close to memoryless,
so durations range from 1s to 145s (median 70). About 48% of runs are too
short. Every config family tried (1800+ candidates, hill-climb, a sharp
burst-to-middle gap drop) plateaus at 14-17% holdout. Hill-climbing on 200
train seeds overfits (27% train -> 14% holdout).
- A likely next lever needs Yash's OK first: a new mechanic that bounds
  each ring's hold time, e.g. the trapped ring's gap slowly widens. That
  needs a per-frame gap in the log and renderer support.
- There's no ffmpeg on the Mac, so `make.py` can't encode. Frames via
  `render.py --at` are fine. Seed 62 is the current best (score 1.0,
  escape 79.2s, 82.2s video).

**Everything below describes the brainrot battles, which are parked, not
abandoned.** Their code (`sim/`, `tools/`, `characters.json`) stays untouched
on `master` and is also on the `brainrot` branch. Narration is parked too.

Automated 1v1 physics ball-battle videos for TikTok (later YouTube Shorts).
Format reference: `@kinetiq.studios` on Instagram Reels.

## The format

Two balls bounce around an enclosed arena under real physics. Each ball is a
brainrot character whose signature ability is themed to who that character
actually is, and **every ability scales over the round** so the fight builds to
a climax. Huge HP pools (~90,000) mean early hits barely register and the drama
comes from damage ramping.

On screen: character names up top in each ball's color, live HP, floating damage
numbers on hits, and a per-ball stats panel in the bottom corners (in that
ball's color) showing its scaling numbers — e.g. `Damage: 3 / Multiplier: x1.43`.

## Non-negotiable constraints

1. **Every video must exceed 60 seconds.** TikTok Creator Rewards only pays on
   videos one minute or longer. The sim must target ~65–90s and reject fights
   outside that window before rendering.
2. **Render quality is the #1 priority.** This is the whole product. Smooth 60fps
   motion, squash-and-stretch on impact, glow, trails, clean bold typography,
   punchy hit feedback. Never trade look for speed — make one gorgeous video
   first, optimize throughput second.
3. **Originality.** TikTok demotes and demonetizes content it reads as unoriginal
   or minimally edited. Our edge is that every fight is genuinely unique and the
   render looks premium. Use our own renditions of characters, not reposted
   viral images.
4. **Royalty-free audio only.** Copyrighted audio gets videos flagged and
   disqualified from rewards.

## Content guidance

- Narration is unhinged brainrot commentary — absurd, fried, funny.
  e.g. "x8.8 body multiplier?? that's not a ball anymore that's a LIFESTYLE"
- **No explicit drug references.** The vibe should just be enjoyable to someone
  who's high; it never needs to mention it.
- **Do not target kids specifically.** YouTube's "made for kids" flag disables
  comments and cuts ad revenue, which would kill the "comment the next matchup"
  engagement loop. The brainrot audience already skews young without aiming at it.

## Architecture — the six-step pipeline

The core discipline: **the sim decides everything, the renderer only draws.**
The sim never touches pixels; the renderer never rolls damage. This means a
boring fight is thrown away instantly before any render time is spent, and
visuals can be reworked without changing how fights play out.

1. **Config** (`characters.json`) — every character's HP, damage and scaling
   numbers plus their signature FX. All balance lives here, never in code, so a
   character can be tuned without touching the program.
2. **Sim** (`sim/engine.py`) — headless physics. Runs in a fraction of a second
   and emits a timestamped event log (JSON). Deterministic by seed.
3. **Render** (`sim/render.py`) — reads the event log, draws every frame, pipes
   to ffmpeg. Where the visual quality work happens.
4. **Audio** — SFX timed to the same event log (hit pitch rises with damage).
   Music is an **optional toggle, not a default** — it competes with the
   narrator. Keep it low, duck under the voice, A/B test with and without.
5. **Narration** — feed the event log to an LLM for the script, then TTS for the
   voice + word timings for burned-in captions. Lines are written to land on the
   moment they describe.
6. **Assembly & posting** — ffmpeg muxes video + SFX + narration, burns captions.
   **Post manually at first** so each video gets eyeballed and we learn what
   works. Automate later via TikTok's *official* Content Posting API only —
   never third-party bots, which risk the account and its eligibility.

## Where things stand (updated 2026-09-18)

**Steps 1-3 are built and verified. Steps 4-6 are not started.**
**Tier 1 and Tier 2 of `docs/design-plan.md` are done** (commits from
2026-09-18/20): HUD safe areas, percentage + chip HP bars, palette separation,
the Tung Tung spin fix, shape-aware fight scoring, best-of-300 seed search,
TAU 9.25, and the cold open. Tier 3 (additive bloom, hit-stop, impact star,
trail polyline, elastic squash, alpha audit) is the next render work.

- **Balance is done.** All 36 matchups pass `quality()` within 12 seeds (worst
  case seed 5). Blowouts 11%, min duration 78.5s, median 97.9s, no timeouts.
  Gate: `python sim/sweep.py` -- run it after any `characters.json` edit.
- **Fight selection searches 300 seeds** and renders the best-SHAPED one
  (`engine.find_best` + `sim/score.py`), rather than the first acceptable
  fight. ~20s of CPU; it was the cheapest large quality win available.
- **Videos now open on a cold open** -- a flash-forward to the biggest
  non-finishing hit -- then a matchup card over live motion, then t=0.
- **Renderer works end to end.** ~2s of render per second of video, so a
  fight is ~2.5 min, single-threaded. `--at <seconds>` dumps one PNG in under a
  second and is the way to iterate on the look; `--preview N` for a short clip.
- **Repo**: `github.com/Yash-Dighe/brainrot-arena-battles`, **private**.
  `master` and `balance-pass` are identical; work continues on `master`.
- **Commit identity must be `yashdighe@gmail.com`** (no dot). The dotted
  variant `yash.dighe@gmail.com` is NOT verified on the GitHub account and
  silently fails to link, leaving commits attributed to nobody. Also: never add
  Claude co-author trailers -- Yash is the only contributor.
- **Environment**: ffmpeg 9.0.1 at `C:/Users/yashd/tools/ffmpeg/bin`, on the
  user PATH, built with `libass` (needed for caption burn-in in step 6) plus
  `libx264`/`libmp3lame`/`libopus`. venv at `.venv`; the only Python dep is
  Pillow. The renderer finds ffmpeg via `--ffmpeg`, then `$FFMPEG`, then PATH.

**Research is done.** `docs/design-plan.md` holds the synthesized plan from three
research passes (retention, visual design, character art + IP), ordered by
priority with verified-vs-hypothesis clearly marked. Read it before doing visual
or format work -- it supersedes guesswork on layout, palette, pacing and audio.

Decisions taken 2026-09-18:
- **Ship all nine characters as-is.** The IP risk was reviewed and knowingly
  accepted. There is active federal litigation over these characters and the
  live claim is **trademark, on the names** -- which our "own renditions"
  strategy does NOT mitigate. See `docs/design-plan.md` for the full position.
- **Character art is drawn in code** (`tools/make_art.py`), not AI-generated:
  consistent by construction, unambiguously human-authored, $0.

### Pick up here, in this order

1. **Tier 1 of `docs/design-plan.md`** -- structural defects, not preferences.
   The bottom stats panels render at y=1636 and are invisible behind platform
   chrome; the HP bar conveys nothing for the first ~40s; four colour pairs are
   indistinguishable; Tung Tung's sprite rotation would destroy any art we give
   him. All display-side, no balance risk.
2. **Character art** -- DONE. `tools/make_art.py` draws all nine in code
   (flat vector, one shared ink outline, consistent by construction). Run
   `--check` and read the silhouette sheet before accepting any redraw.
3. **Step 4, audio/SFX** -- DONE. `sim/audio.py` synthesizes everything from
   the stdlib (no samples, no loops, so royalty-free is unarguable). Hits walk
   a minor-pentatonic scale and resolve on the KO; a sub drone swells across
   the round. Muxing applies `loudnorm` to about -14 LUFS, because the raw
   synth master sits near -32 and platforms would gain it up themselves.
   **Nobody has listened to it yet** -- it is verified only structurally
   (alignment, peak, LUFS). Needs a human ear before it ships.
4. **Tier 4 of `docs/design-plan.md`: the frame is still ~97% empty.** Balls
   are 2.8% of arena area, and a circle inscribed in 1080 wastes half a 9:16
   frame. Needs a decision: a render-side virtual camera touches every draw
   call, while bigger balls or a non-circular arena are simpler edits but move
   collision frequency and need a full `sweep.py` re-run. Left undone
   deliberately -- it is the riskiest item on the list.
5. **Step 5, narration** -- event log to an LLM for the script, TTS for voice
   and word timings, captions burned in.
6. **Step 6, assembly.** `tools/make_video.py` already does
   sim -> render -> SFX -> mux in one command. What remains is
   captions (needs step 5) and posting, which stays manual.

### Known rough edges

- `cappuccino` and `brrbrr` still have `"signature_fx": "TBD"` and
  `"status": "open"`. `render.py` draws provisional placeholders for them
  (steam wisps, a bark ring), marked `# (FX: TBD)` in the code -- decide the
  real FX, then update both `characters.json` and the renderer.
- Two matchups stay lopsided but still pass: `tungtung vs lirili` and
  `tungtung vs boneca` (~8/12 and 7/12 blowout seeds). Tung Tung scales purely
  on time, which is the strongest curve in the roster.
- Rendering is single-threaded. Fine for one gorgeous video; revisit when
  throughput matters.

## Roster status

9 characters in `characters.json`. All abilities are locked. Signature FX are
locked for 7; still `"status": "open"` and needing FX ideas:

- **Cappuccino Assassino** — caffeinated combos that get longer/faster
- **Brr Brr Patapim** — spreading branches that raise contact damage

## Conventions

- Vertical 1080x1920, 60fps.
- Balls are plain colored circles with the character's image inside. Drop art at
  `assets/balls/<id>.png` (transparent, square); a colored placeholder is drawn
  if missing.
- `sim/sweep.py` runs every matchup over many seeds and is the balance gate.
- `frames[].balls[]` carries the ability scalars the renderer drives FX from
  (`speed`, `spin_rate`, `buff`, `boost`, `burst`). Add a field here rather
  than making the renderer parse numbers back out of the `panel` strings --
  those are display text the sim owns and are drawn verbatim.
- Event log schema is the contract between sim and render — change it in both.
- `out/` holds generated logs and videos and is gitignored.

## Balance status

All 36 matchups pass `quality()` within 12 seeds (worst case: seed 5), verified
by `python sim/sweep.py`. Blowouts are down from 58% of sims to 13%, no sim
falls under the 62s floor, and the median fight is ~99s. Re-run the sweep after
any balance edit -- it exits non-zero if a matchup regresses.

Two engine invariants came out of that work and are worth knowing before tuning:

- **Cooldowns are per-source.** `hit_cd` covers ball-vs-ball; `BRANCH_COOLDOWN`
  covers Brr Brr's branches. They were shared, which let 11 static branches
  monopolise the cooldown and starve the opponent of their own hits (135 hits
  to 22). If another non-collision hazard is added, give it its own cooldown.
- **Two time-scalers make an unwatchable fight.** Tralalero, Tung Tung and
  Ballerina all ramp purely on elapsed time, so when two of them meet they
  escalate in lockstep: the win probability sits pinned near even with no
  swings, which scores 0.26-0.34 against a 0.60 bar. All three of those
  pairings fail `sim/score.py` even at 300 seeds, and they are the ONLY three
  that do. Avoid them in matchmaking, or give one fighter a bursty,
  discrete element. Do not relax the gate to admit them -- a flat coin-flip
  is as boring as a blowout, just for the opposite reason.
- **`boost` decays at `BOOST_DECAY_PER_SEC` (0.15/sec)**, so a stack has to be
  large enough to outlive that or it is a no-op. Chimpanzini's peels originally
  applied a 0.04 *slow* that evaporated in 0.27s -- his ability did effectively
  nothing, and `bonus_dmg_on_slip` almost never fired. Same trap applies to any
  new stacking effect.

**Never slow the opponent as a mechanic.** Fewer collisions means fewer hits for
*both* balls, which hands the fight to whoever scales on **time** (Tralalero,
Tungtung, Ballerina) over whoever scales on **contact**. Chimpanzini's peels now
make the enemy skid *faster* rather than slower: more collisions, which feeds
his contact scaling, and he hits harder while they're skidding. It also reads
better -- a banana peel should send you flying. Lirili's time-warp is the one
remaining slow, and it is balanced by a large in-burst damage multiplier
rather than by the slow itself.

## Open decisions

- Whether music ships at all (see step 4).
- FX for the two open characters above (renderer draws placeholders for now).
- Whether to commission character art or generate it ourselves.
- Stretch goals, not now: arenas with obstacles, more than 2 balls at once.
  Abilities are written as self-contained modules reacting to events so these
  stay extensions rather than rewrites.
