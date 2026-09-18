# Design plan — synthesized research, 2026-09-18

Three research passes (retention/format, visual design, character art + IP) folded
into one ordered plan. Claims marked **[verified]** were independently checked
against sources or against our own code; everything else is the researcher's
judgement and should be treated as a hypothesis.

## Decisions taken

- **Roster: ship all nine characters as-is.** The IP risk below was reviewed and
  knowingly accepted.
- **Character art: drawn in code** (`tools/make_art.py`), not AI-generated.
- **v1 has no narration.** Visuals + SFX carry it; captions come later.

## The IP position, recorded

There is **active federal litigation over these exact characters** [verified —
[NPR, Aug 2026](https://www.npr.org/2026/08/19/nx-s1-5867638/artificial-intelligence-brainrot-memes-copyright-spyder-tung-tung-sahur)].
Do Big / Spyder Games sued in N.D. Cal. arguing AI-generated characters are
unprotectable; Mementum Lab **countersued on trademark**. Epic licensed
Ballerina Cappuccina for Fortnite in April 2026, establishing a licensing market.

**The key point, which inverts this project's original assumption:** our
"own renditions, not reposted images" strategy defends against *copyright*. The
live claim is *trademark*, which attaches to **the name, not the drawing** — and
we put those names in large type, in titles, and in hashtags. Redrawing does not
reduce that exposure.

Four of nine have an organized claimant: `tungtung`, `boneca`, `brrbrr`,
`ballerina`. The realistic risk is not a lawsuit but **platform IP complaints**,
which would threaten Creator Rewards eligibility — the whole business case.

Unresolved and accepted: **Bombardiro Crocodilo's originating meme audio
describes bombing civilians in Gaza.** We don't use the audio, but the name is a
searchable handle to it. Brand-safety exposure that redrawing does not fix.

Watch `Spyder Games / Do Big Studios v. Mementum Lab`, N.D. Cal.

---

## Tier 1 — structural defects (do first)

These are bugs, not preferences.

1. **The bottom stats panels are invisible.** They render at **y=1636→1868**
   [verified in `render.py:689`]; everything below y≈1500 is covered by
   TikTok/Shorts chrome. Also the fighter-0 name/bar at y≈50–138 sits in the top
   dead zone. **Rewrite `draw_hud()`**: both fighter rows into `y 210–430`, stats
   inlined as a chip next to the name (`panel` rows joined with `·`), clock
   demoted to 40px at `(540, 200)`. Safe design rect is `x 60→900, y 200→1440`.
   Reserve `y 1180→1420` for captions later.

2. **The HP bar conveys nothing for the first ~40 seconds.** A hit for 5 against
   90,000 moves it 0.006% — sub-pixel. Two fixes, both display-only, no balance
   risk: show **HP as a percentage**, and add a **ghost/chip bar** (a second bar
   in red behind the real one, lagging ~0.4s) so every one of ~150 hits produces
   visible motion. Make the **multiplier** the headline early stat, since it is
   the number actually moving.

3. **Four colour pairs are indistinguishable** [verified by computing Rec.709
   luma + hue over all 36 pairs]: `tungtung`/`cappuccino` (Δhue 4.6°, Δluma 7.1)
   is two identical-looking balls; also `bombardiro`/`tungtung`,
   `bombardiro`/`cappuccino`, `bombardiro`/`chimpanzini`. Re-spec the palette for
   hue *and* luma spread, and add a per-matchup rule swapping fighter 1 to an alt
   colour when `Δluma < 45 and Δhue < 30`. A Δluma ≥ 45 floor is the standard
   proxy for colour-blind safety.

4. **The arena has no contrast.** `ARENA_FILL` vs `BG_BOT` is a luma delta of ~3
   — the interior does not visually exist. Deepen the floor, lift the surround,
   delete the three inner rings (they quantize to nothing), add a vignette over
   the dead zones, and bake in static noise to stop gradient banding (static
   noise costs bits only in the I-frame).

5. **Tung Tung's art will be an unreadable smear** [verified: only he accumulates
   `spin` at `engine.py:184`, and `render.py:285` rotates the whole ball sprite;
   his `spin_rate` reaches ~6 rev/sec]. Rotate a bat/arc FX layer around a
   **static** sprite instead. Do this before making art, or his is wasted.

6. **Encoder is missing colour tags** — untagged H.264 lets the platform guess
   BT.709 vs BT.601, shifting our saturated colours. Add
   `-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv`,
   plus `aq-mode=3` (dark-region adaptive quantization — exactly our failure mode)
   and `-preset slow` (x264 is not our bottleneck).

7. **Damage numbers: drop red from the ramp.** `(255,70,55)` carries its meaning
   in the half-resolution chroma plane, which is the worst case for 4:2:0 + a
   ~1–2 Mbps platform re-encode. Express magnitude via **size and glow**, keep
   fill luma above ~170. Size range 54→150 (26px is below every legibility
   minimum), stroke `size//9`, add a hard offset shadow.

## Tier 2 — selection and pacing (highest leverage, sim-side)

8. **Stop satisficing on fight selection.** We take the first seed passing within
   ~12 tries. The sim runs in **0.07s**; a render costs **~150s**. Searching
   300+ seeds and rendering `argmax(score)` costs ~20s of CPU and upgrades a
   median fight to a 99th-percentile one. Highest impact-per-effort item found.

9. **Replace `quality()` with a shape-aware score.** The current gate only tests
   final margin, so it cannot distinguish a nail-biter from a fight decided at
   20s that ground out. Build a win-probability series from time-to-death
   (`ttd = hp / EWMA(dps)`), then score suspense/comeback/late-swing — grounded
   in sports-analytics literature (Ely–Frankel–Kamenica; Game Excitement Index).
   Target shape: **plausibly even early, one real scare where the eventual winner
   drops below ~0.40, resolution in the final seconds.** A flat coin-flip is as
   boring as a blowout — maximum suspense but zero surprise.

10. **Shorten the fights.** TikTok pays on **qualified views = ≥5s watched**, and
    videos must be **≥1 minute** [verified against TikTok's legal terms]. Rewards
    are calculated on *"number of video views, video engagement and
    authenticity"* — engagement is named, but no formula is published, so
    "longer earns more" is unsupported while longer definitely costs completion.
    Our renderable fights are currently **78.5–104.8s, median 96.5s** [verified
    over 340 passing sims] — we cannot currently produce a short fight at all,
    because with `TAU = 13.5` the damage curve can't resolve faster. Target
    ~65–75s, which needs a **`TAU` retune plus a full re-sweep**.
    Also reconcile the three disagreeing numbers: CLAUDE.md says 65–90s, the gate
    says 62–105s, reality is 78–105s.

11. **Cold-open on the climax.** Open on ~1.2s of the fight's peak-damage moment,
    then cut to the matchup card and t=0. Our payoff is structurally at the end
    and our opening is structurally the weakest moment — and the first 5 seconds
    are the *payment gate*, not just a retention problem. Ending on that same
    frame makes the loop seamless.

## Tier 3 — the look upgrade

12. **Additive blending + bloom.** `paste()` is source-over, so the "glow" is a
    haze that *dulls* what it overlaps. Composite glows/trails/sparks into an
    additive layer, then downsample→blur→upsample (at 270×480, ~5ms/frame; never
    blur at full res). Single biggest change to perceived production value.
13. **Local hit-stop.** Freeze only the two involved balls for 1–6 frames by
    `rel²` (attacker 2 fewer — the Capcom asymmetry), everything else keeps
    running. Do **not** repeat output frames: with ~150 hits that adds seconds of
    video and desyncs event timestamps from video time, which would break SFX,
    narration and captions downstream.
14. **Impact star** on hits above `rel > 0.3` — one cached sprite, scale-in over
    2 frames. Highest impact-per-line in the visual set.
15. **Trail as a tapered polyline**, alpha 0.34→0.05. The current discrete discs
    run at alpha 0.035, roughly +5 code values over the floor — inside the
    quantization dead zone, i.e. encodes to nothing.
16. **Elastic squash** (damped cosine, overshoot into stretch, amplitude to 0.50)
    and **BICUBIC** in `deformed()` — it currently does four BILINEAR resamples
    on a high-contrast rim.
17. **Alpha audit.** Everything drawn below ~0.10 alpha on the dark floor
    vanishes at delivery bitrate: arena rings, clock ticks, HP bar outline and
    highlight, trail tails, steam wisps, Brr Brr's ring. Raise 2–3× or delete.

## Tier 4 — needs a balance re-sweep

18. **The frame is mostly empty.** Arena is 940px across, balls 156px — together
    **2.8% of arena area**. A circle inscribed in 1080 also throws away 51% of a
    9:16 frame by construction. Cheapest fix with no balance risk is a
    **render-side virtual camera** at zoom ~1.18 plus a punch-in on big hits.
    Bigger fixes — `BALL_R 78→96`, or a capsule/elliptical arena — change
    collision frequency and require `sim/sweep.py` to pass again.
19. **No follow-cam.** A panning camera displaces every macroblock every frame,
    starving the balls and numbers of bits at TikTok's ~1–2 Mbps delivery. Our
    static-background + world-offset shake is already the right architecture.

## Step 4 (audio) — build it as a tonal system

20. **Map hits onto ascending scale steps**, not just rising pitch, so a streak
    builds a phrase that resolves on the big hit. This is the documented
    retention engine of the whole "ball bouncing" genre and is the strongest
    genre-native answer to holding a voiceless middle.
21. **Synthesize an original motif.** The genre convention is to use *famous*
    melodies — that would violate our royalty-free constraint and is the most
    likely way this project gets flagged. An original ascending motif gets the
    psychoacoustic effect (anticipation of resolution) with zero exposure.
22. **Ship music on by default in v1.** The "music competes with the narrator"
    reasoning doesn't apply while there is no narrator.

## Character art spec

- **Style:** flat vector, chunky, uniform near-black ink outline. Rendered/painted
  and 3D both reject — soft edges plus downscale plus glow plus h264 = mush.
- **Size reality:** art displays at **121px**, not 156px [verified:
  `inner = int(n * 0.78)` in `_load_art`], and the square's corners are clipped
  by the ball mask. That is a hard detail budget.
- **Export:** 512×512 RGBA PNG, straight alpha, sRGB, content inside a centred
  circle of 0.90 × side, centre of visual mass at geometric centre.
- **Bake in** the dark outline (17–21px at 512). **Do not bake** glow, rim light,
  drop shadow, or the ball circle — the renderer owns those and they will double.
- **Differentiate by silhouette, not detail.** Highest collision risks:
  `cappuccino`/`ballerina` (both cup-headed) and `brrbrr`/`boneca`.
- **Acceptance test:** render a frame, view at 25% zoom, name both characters.
  Repeat with the art filled 100% black.

## Deliberately not doing

- **No follow-cam** (item 19).
- **No recognisable melodies**, however genre-standard (item 21).
- **Not extending fights past ~75s** chasing watch time (item 10).
- **Not killing the round clock outright** — the argument that a countdown invites
  viewers to leave is plausible but unevidenced. Demote it, and A/B from one log
  rather than removing a working element on a hunch.
