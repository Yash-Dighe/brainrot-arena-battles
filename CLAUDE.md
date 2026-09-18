# Brainrot Arena â€” project context

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
ball's color) showing its scaling numbers â€” e.g. `Damage: 3 / Multiplier: x1.43`.

## Non-negotiable constraints

1. **Every video must exceed 60 seconds.** TikTok Creator Rewards only pays on
   videos one minute or longer. The sim must target ~65â€“90s and reject fights
   outside that window before rendering.
2. **Render quality is the #1 priority.** This is the whole product. Smooth 60fps
   motion, squash-and-stretch on impact, glow, trails, clean bold typography,
   punchy hit feedback. Never trade look for speed â€” make one gorgeous video
   first, optimize throughput second.
3. **Originality.** TikTok demotes and demonetizes content it reads as unoriginal
   or minimally edited. Our edge is that every fight is genuinely unique and the
   render looks premium. Use our own renditions of characters, not reposted
   viral images.
4. **Royalty-free audio only.** Copyrighted audio gets videos flagged and
   disqualified from rewards.

## Content guidance

- Narration is unhinged brainrot commentary â€” absurd, fried, funny.
  e.g. "x8.8 body multiplier?? that's not a ball anymore that's a LIFESTYLE"
- **No explicit drug references.** The vibe should just be enjoyable to someone
  who's high; it never needs to mention it.
- **Do not target kids specifically.** YouTube's "made for kids" flag disables
  comments and cuts ad revenue, which would kill the "comment the next matchup"
  engagement loop. The brainrot audience already skews young without aiming at it.

## Architecture â€” the six-step pipeline

The core discipline: **the sim decides everything, the renderer only draws.**
The sim never touches pixels; the renderer never rolls damage. This means a
boring fight is thrown away instantly before any render time is spent, and
visuals can be reworked without changing how fights play out.

1. **Config** (`characters.json`) â€” every character's HP, damage and scaling
   numbers plus their signature FX. All balance lives here, never in code, so a
   character can be tuned without touching the program.
2. **Sim** (`sim/engine.py`) â€” headless physics. Runs in a fraction of a second
   and emits a timestamped event log (JSON). Deterministic by seed.
3. **Render** (`sim/render.py`) â€” reads the event log, draws every frame, pipes
   to ffmpeg. Where the visual quality work happens.
4. **Audio** â€” SFX timed to the same event log (hit pitch rises with damage).
   Music is an **optional toggle, not a default** â€” it competes with the
   narrator. Keep it low, duck under the voice, A/B test with and without.
5. **Narration** â€” feed the event log to an LLM for the script, then TTS for the
   voice + word timings for burned-in captions. Lines are written to land on the
   moment they describe.
6. **Assembly & posting** â€” ffmpeg muxes video + SFX + narration, burns captions.
   **Post manually at first** so each video gets eyeballed and we learn what
   works. Automate later via TikTok's *official* Content Posting API only â€”
   never third-party bots, which risk the account and its eligibility.

## Roster status

9 characters in `characters.json`. All abilities are locked. Signature FX are
locked for 7; still `"status": "open"` and needing FX ideas:

- **Cappuccino Assassino** â€” caffeinated combos that get longer/faster
- **Brr Brr Patapim** â€” spreading branches that raise contact damage

## Conventions

- Vertical 1080x1920, 60fps.
- Balls are plain colored circles with the character's image inside. Drop art at
  `assets/balls/<id>.png` (transparent, square); a colored placeholder is drawn
  if missing.
- `sim/sweep.py` runs every matchup over many seeds and is the balance gate.
- Event log schema is the contract between sim and render â€” change it in both.
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
- FX for the two open characters above.
- Stretch goals, not now: arenas with obstacles, more than 2 balls at once.
  Abilities are written as self-contained modules reacting to events so these
  stay extensions rather than rewrites.
