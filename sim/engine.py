"""
Step 2 of the pipeline: headless physics simulation.

Runs a full 1v1 fight as pure math and writes a timestamped event log.
Draws nothing. Deterministic for a given seed.

  python sim/engine.py --a tralalero --b boneca --seed 42
  python sim/engine.py --find 20        # search seeds for a good fight

The log it writes is the ONLY contract with the renderer:
  meta    -- seed, fighters, duration, winner
  frames  -- per-frame ball state sampled at FPS (renderer just draws these)
  events  -- discrete moments (hits, bombs, buffs, ko) with timestamps
"""
import argparse, json, math, os, random, sys

FPS = 60
PHYS_HZ = 240                      # physics substeps per second
ARENA_C = (540.0, 1010.0)          # centre, in 1080x1920 space
ARENA_R = 470.0
BALL_R = 78.0
BASE_SPEED = 380.0
MAX_SECONDS = 150.0
HIT_COOLDOWN = 0.18                # per-pair, stops one collision multi-hitting
BRANCH_COOLDOWN = 3.0              # branches tick on their own, much slower clock
BOOST_DECAY_PER_SEC = 0.15         # how fast a peeled ball settles back down

# Global escalation: damage is multiplied by exp(t / TAU). This is what makes
# early hits register as ~5 damage against a 90k pool and late hits hit for
# thousands. Tune TAU to move average fight length.
TAU = 13.5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------- helpers
def load_roster():
    cfg = json.load(open(os.path.join(ROOT, "characters.json")))
    return {c["id"]: c for c in cfg["characters"]}


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Ball:
    def __init__(self, cfg, x, y, angle, rng):
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg["name"]
        self.hp = float(cfg["hp"])
        self.max_hp = float(cfg["hp"])
        self.x, self.y = x, y
        self.speed = BASE_SPEED
        self.vx = math.cos(angle) * self.speed
        self.vy = math.sin(angle) * self.speed
        self.r = BALL_R
        self.rng = rng
        # per-ability state, surfaced to the renderer + stats panel
        self.st = {
            "speed_mult": 1.0, "spin": 0.0, "spin_rate": 1.0, "buff": 1.0,
            "mult": float(cfg.get("scaling", {}).get("mult_start", 1.0)),
            "branches": 0, "combo_len": int(cfg.get("scaling", {}).get("combo_hits_start", 0)),
            "hits": 0, "boost": 0.0, "burst": 0.0, "bombs": 0, "peels": 0,
        }
        self._next = {}   # ability timers

    def alive(self):
        return self.hp > 0

    def stats_panel(self):
        """The 2-3 numbers shown in this ball's corner, per character."""
        s, i = self.st, self.id
        if i == "tralalero":
            return [("Speed", f"x{s['speed_mult']:.2f}"), ("Hits", str(s["hits"]))]
        if i == "bombardiro":
            return [("Bombs", str(s["bombs"])), ("Hits", str(s["hits"]))]
        if i == "tungtung":
            return [("Spin", f"x{s['spin_rate']:.2f}"), ("Hits", str(s["hits"]))]
        if i == "ballerina":
            return [("Buff", f"x{s['buff']:.2f}"), ("Hits", str(s["hits"]))]
        if i == "boneca":
            return [("Multiplier", f"x{s['mult']:.2f}"), ("Hits", str(s["hits"]))]
        if i == "cappuccino":
            return [("Combo", str(s["combo_len"])), ("Hits", str(s["hits"]))]
        if i == "brrbrr":
            return [("Branches", str(s["branches"])), ("Hits", str(s["hits"]))]
        if i == "chimpanzini":
            return [("Peels", str(s["peels"])), ("Hits", str(s["hits"]))]
        if i == "lirili":
            return [("Time Warp", "ON" if s["burst"] > 0 else "off"), ("Hits", str(s["hits"]))]
        return [("Hits", str(s["hits"]))]


class Sim:
    def __init__(self, a_cfg, b_cfg, seed):
        self.rng = random.Random(seed)
        self.seed = seed
        cx, cy = ARENA_C
        off = ARENA_R * 0.45
        self.balls = [
            Ball(a_cfg, cx - off, cy, self.rng.uniform(-0.6, 0.6), self.rng),
            Ball(b_cfg, cx + off, cy, math.pi + self.rng.uniform(-0.6, 0.6), self.rng),
        ]
        self.t = 0.0
        self.events = []
        self.frames = []
        self.projectiles = []      # bombs / peels in flight
        self.branches = []         # static hazards
        self.hit_cd = 0.0
        self.branch_cd = 0.0       # separate from hit_cd: a branch touch must not
                                   # consume the opponent's collision cooldown
        self.winner = None

    # ----------------------------------------------------------------- damage
    def escalation(self):
        return math.exp(self.t / TAU)

    def log(self, **kw):
        self.events.append({"t": round(self.t, 3), **kw})

    def deal(self, src, dst, raw, kind="hit", **extra):
        dmg = max(1, int(round(raw * self.escalation())))
        dst.hp = max(0.0, dst.hp - dmg)
        src.st["hits"] += 1
        self.log(type=kind, by=self.balls.index(src), to=self.balls.index(dst),
                 dmg=dmg, x=round(dst.x, 1), y=round(dst.y, 1),
                 hp=[round(b.hp) for b in self.balls], **extra)
        if dst.hp <= 0 and self.winner is None:
            self.winner = self.balls.index(src)
            self.log(type="ko", by=self.winner, to=self.balls.index(dst),
                     hp=[round(b.hp) for b in self.balls])
        return dmg

    def contact_damage(self, src, dst):
        """Damage dealt when two balls collide, resolved per character."""
        sc = src.cfg.get("scaling", {})
        base = float(src.cfg.get("base_damage", 4))
        i, extra = src.id, {}

        if i == "tralalero":
            base *= src.st["speed_mult"]
        elif i == "tungtung":
            base *= src.st["spin_rate"]
        elif i == "ballerina":
            base *= src.st["buff"]
        elif i == "boneca":
            roll = self.rng.randint(int(src.cfg["roll_min"]), int(src.cfg["roll_max"]))
            src.st["mult"] += float(sc.get("mult_gain_per_hit", 0.02))
            base = roll * src.st["mult"]
            extra = {"roll": roll, "jackpot": roll >= int(src.cfg["roll_max"]) - 1}
        elif i == "brrbrr":
            base += src.st["branches"] * float(sc.get("damage_per_branch", 0.5))
        elif i == "lirili" and src.st["burst"] > 0:
            base *= float(sc.get("dmg_mult_in_burst", 2.0))
        elif i == "chimpanzini" and dst.st["boost"] > 0:
            base *= float(sc.get("bonus_dmg_on_slip", 1.3))

        self.deal(src, dst, base, **extra)

        # Cappuccino's combo fires on top of his normal hit
        if i == "cappuccino":
            every = int(sc.get("combo_every_hits", 5))
            if src.st["hits"] % every == 0:
                src.st["combo_len"] += int(sc.get("combo_hits_gain_per_trigger", 1))
                self.log(type="combo", by=self.balls.index(src), n=src.st["combo_len"])
                for _ in range(src.st["combo_len"]):
                    if dst.alive():
                        self.deal(src, dst, base * 0.6, kind="combo_hit")

    # -------------------------------------------------------------- abilities
    def tick_abilities(self, dt):
        for idx, b in enumerate(self.balls):
            if not b.alive():
                continue
            sc = b.cfg.get("scaling", {})
            i = b.id
            other = self.balls[1 - idx]

            if i == "tralalero":
                b.st["speed_mult"] += float(sc.get("speed_gain_per_sec", 0.04)) * dt
            elif i == "tungtung":
                b.st["spin_rate"] += float(sc.get("spin_speed_gain_per_sec", 0.05)) * dt
                b.st["spin"] += b.st["spin_rate"] * 360 * dt
            elif i == "ballerina":
                nxt = b._next.get("spin", float(sc.get("spin_interval_sec", 1.5)))
                if self.t >= nxt:
                    b.st["buff"] += float(sc.get("buff_per_spin", 0.03))
                    b._next["spin"] = self.t + float(sc.get("spin_interval_sec", 1.5))
                    self.log(type="pirouette", by=idx, buff=round(b.st["buff"], 3))
            elif i == "bombardiro":
                iv = b._next.get("iv", float(sc.get("bomb_interval_start_sec", 6.0)))
                nxt = b._next.get("bomb", iv)
                if self.t >= nxt:
                    b.st["bombs"] += 1
                    iv = max(float(sc.get("bomb_interval_min_sec", 1.0)),
                             iv * float(sc.get("interval_decay_per_drop", 0.9)))
                    b._next["iv"] = iv
                    b._next["bomb"] = self.t + iv
                    self.projectiles.append({
                        "kind": "bomb", "owner": idx, "x": other.x, "y": ARENA_C[1] - ARENA_R,
                        "tx": other.x, "ty": other.y, "vy": 900.0,
                        "dmg": float(sc.get("bomb_damage", 40)) *
                               (1 + float(sc.get("bomb_damage_gain", 0.06)) * b.st["bombs"])})
                    self.log(type="bomb_drop", by=idx, x=round(other.x, 1))
            elif i == "chimpanzini":
                nxt = b._next.get("peel", float(sc.get("peel_every_sec", 3.0)))
                if self.t >= nxt:
                    b.st["peels"] += 1
                    b._next["peel"] = self.t + float(sc.get("peel_every_sec", 3.0))
                    ang = self.rng.uniform(0, math.tau)
                    d = self.rng.uniform(0, ARENA_R - BALL_R)
                    self.projectiles.append({"kind": "peel", "owner": idx,
                                             "x": ARENA_C[0] + math.cos(ang) * d,
                                             "y": ARENA_C[1] + math.sin(ang) * d, "vy": 0.0})
                    self.log(type="peel_drop", by=idx)
            elif i == "brrbrr":
                iv = b._next.get("iv", float(sc.get("branch_every_sec", 4.0)))
                nxt = b._next.get("branch", iv)
                if self.t >= nxt:
                    b.st["branches"] += 1
                    iv = max(float(sc.get("branch_interval_min_sec", 1.5)),
                             iv * float(sc.get("interval_decay", 0.92)))
                    b._next["iv"] = iv
                    b._next["branch"] = self.t + iv
                    ang = self.rng.uniform(0, math.tau)
                    self.branches.append({"a": ang, "len": self.rng.uniform(0.5, 0.95)})
                    self.log(type="branch", by=idx, n=b.st["branches"])
            elif i == "lirili":
                iv = b._next.get("iv", float(sc.get("burst_interval_start_sec", 8.0)))
                nxt = b._next.get("burst", iv)
                if b.st["burst"] > 0:
                    b.st["burst"] = max(0.0, b.st["burst"] - dt)
                elif self.t >= nxt:
                    b.st["burst"] = float(sc.get("burst_duration_sec", 2.0))
                    iv = max(float(sc.get("burst_interval_min_sec", 3.0)),
                             iv * float(sc.get("interval_decay", 0.9)))
                    b._next["iv"] = iv
                    b._next["burst"] = self.t + iv + b.st["burst"]
                    self.log(type="time_warp", by=idx, dur=b.st["burst"])

            if b.st["boost"] > 0:
                b.st["boost"] = max(0.0, b.st["boost"] - dt * BOOST_DECAY_PER_SEC)

    # ---------------------------------------------------------------- physics
    def step(self, dt):
        cx, cy = ARENA_C
        for idx, b in enumerate(self.balls):
            if not b.alive():
                continue
            mult = b.st["speed_mult"] * (1.0 + b.st["boost"])
            # Lirili's warp slows everyone else down
            foe = self.balls[1 - idx]
            if foe.id == "lirili" and foe.st["burst"] > 0:
                mult *= 0.45
            b.x += b.vx * mult * dt
            b.y += b.vy * mult * dt

            # arena wall (circle)
            dx, dy = b.x - cx, b.y - cy
            d = math.hypot(dx, dy)
            if d > ARENA_R - b.r:
                nx, ny = dx / d, dy / d
                b.x, b.y = cx + nx * (ARENA_R - b.r), cy + ny * (ARENA_R - b.r)
                dot = b.vx * nx + b.vy * ny
                b.vx -= 2 * dot * nx
                b.vy -= 2 * dot * ny
                jitter = self.rng.uniform(-0.12, 0.12)
                c, s = math.cos(jitter), math.sin(jitter)
                b.vx, b.vy = b.vx * c - b.vy * s, b.vx * s + b.vy * c
                self.log(type="wall", by=idx, x=round(b.x, 1), y=round(b.y, 1))

        # ball vs ball
        a, bb = self.balls
        if a.alive() and bb.alive():
            dx, dy = bb.x - a.x, bb.y - a.y
            d = math.hypot(dx, dy) or 1e-6
            if d < a.r + bb.r:
                nx, ny = dx / d, dy / d
                overlap = a.r + bb.r - d
                a.x -= nx * overlap / 2; a.y -= ny * overlap / 2
                bb.x += nx * overlap / 2; bb.y += ny * overlap / 2
                avn = a.vx * nx + a.vy * ny
                bvn = bb.vx * nx + bb.vy * ny
                a.vx += (bvn - avn) * nx; a.vy += (bvn - avn) * ny
                bb.vx += (avn - bvn) * nx; bb.vy += (avn - bvn) * ny
                if self.t >= self.hit_cd:
                    self.hit_cd = self.t + HIT_COOLDOWN
                    self.contact_damage(a, bb)
                    if bb.alive():
                        self.contact_damage(bb, a)

        # projectiles
        for p in list(self.projectiles):
            if p["kind"] == "bomb":
                p["y"] += p["vy"] * dt
                tgt = self.balls[1 - p["owner"]]
                if math.hypot(p["x"] - tgt.x, p["y"] - tgt.y) < tgt.r + 20 or p["y"] > ARENA_C[1] + ARENA_R:
                    if tgt.alive() and math.hypot(p["x"] - tgt.x, p["y"] - tgt.y) < tgt.r + 90:
                        self.deal(self.balls[p["owner"]], tgt, p["dmg"], kind="bomb_hit")
                    self.log(type="explosion", x=round(p["x"], 1), y=round(p["y"], 1))
                    self.projectiles.remove(p)
            elif p["kind"] == "peel":
                tgt = self.balls[1 - p["owner"]]
                if tgt.alive() and math.hypot(p["x"] - tgt.x, p["y"] - tgt.y) < tgt.r:
                    sc = self.balls[p["owner"]].cfg.get("scaling", {})
                    tgt.st["boost"] = min(float(sc.get("boost_cap", 0.5)),
                                          tgt.st["boost"] + float(sc.get("boost_per_peel", 0.15)))
                    self.log(type="slip", to=1 - p["owner"], x=round(tgt.x, 1), y=round(tgt.y, 1),
                             boost=round(tgt.st["boost"], 3))
                    self.projectiles.remove(p)

        # branch hazards
        for idx, b in enumerate(self.balls):
            if not b.alive() or b.id == "brrbrr":
                continue
            for br in self.branches:
                ex = ARENA_C[0] + math.cos(br["a"]) * ARENA_R * br["len"]
                ey = ARENA_C[1] + math.sin(br["a"]) * ARENA_R * br["len"]
                if math.hypot(ex - b.x, ey - b.y) < b.r and self.t >= self.branch_cd:
                    self.branch_cd = self.t + BRANCH_COOLDOWN
                    owner = next((k for k, x in enumerate(self.balls) if x.id == "brrbrr"), None)
                    if owner is not None:
                        self.contact_damage(self.balls[owner], b)
                    break

    # ------------------------------------------------------------------- loop
    def run(self):
        dt = 1.0 / PHYS_HZ
        sub = PHYS_HZ // FPS
        n = 0
        while self.t < MAX_SECONDS and self.winner is None:
            self.tick_abilities(dt)
            self.step(dt)
            self.t += dt
            n += 1
            if n % sub == 0:
                self.frames.append({
                    "t": round(self.t, 3),
                    "balls": [{"x": round(b.x, 1), "y": round(b.y, 1), "hp": round(b.hp),
                               "spin": round(b.st["spin"] % 360, 1),
                               "speed": round(b.st["speed_mult"], 3),
                               # ability scalars the renderer drives FX from, so it
                               # never has to parse them back out of panel strings
                               "buff": round(b.st["buff"], 3),
                               "spin_rate": round(b.st["spin_rate"], 3),
                               "boost": round(b.st["boost"], 3),
                               "burst": round(b.st["burst"], 2),
                               "panel": b.stats_panel()} for b in self.balls],
                    "proj": [{"k": p["kind"], "x": round(p["x"], 1), "y": round(p["y"], 1)}
                             for p in self.projectiles],
                    "branches": [{"a": round(x["a"], 3), "len": round(x["len"], 3)} for x in self.branches],
                })
        if self.winner is None:   # timed out: most HP% wins
            self.winner = 0 if self.balls[0].hp / self.balls[0].max_hp >= \
                               self.balls[1].hp / self.balls[1].max_hp else 1
            self.log(type="ko", by=self.winner, to=1 - self.winner,
                     hp=[round(b.hp) for b in self.balls])
        return self

    def to_log(self):
        return {
            "meta": {
                "seed": self.seed,
                "fps": FPS,
                "arena": {"cx": ARENA_C[0], "cy": ARENA_C[1], "r": ARENA_R},
                "ball_r": BALL_R,
                "fighters": [{"id": b.id, "name": b.name, "max_hp": b.max_hp} for b in self.balls],
                "winner": self.winner,
                "fight_seconds": round(self.t, 2),
            },
            "frames": self.frames,
            "events": self.events,
        }


# ------------------------------------------------------------------------ cli
def run_fight(roster, a_id, b_id, seed):
    return Sim(roster[a_id], roster[b_id], seed).run()


def quality(sim):
    """Is this fight worth rendering? Needs to clear 60s and stay competitive."""
    secs = sim.t
    loser = sim.balls[1 - sim.winner]
    win_hp_frac = sim.balls[sim.winner].hp / sim.balls[sim.winner].max_hp
    ok = 62.0 <= secs <= 105.0 and win_hp_frac <= 0.70
    return ok, secs, win_hp_frac


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a"); ap.add_argument("--b")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--find", type=int, default=0, help="try N seeds, keep the best fight")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    roster = load_roster()
    ids = list(roster)
    rng = random.Random()
    a_id = args.a or rng.choice(ids)
    b_id = args.b or rng.choice([i for i in ids if i != a_id])
    if a_id not in roster or b_id not in roster:
        sys.exit(f"unknown character; options: {', '.join(ids)}")

    if args.find:
        best = None
        for s in range(args.find):
            sim = run_fight(roster, a_id, b_id, s)
            ok, secs, frac = quality(sim)
            if ok:
                best = sim
                print(f"seed {s}: {secs:.1f}s, winner kept {frac*100:.0f}% HP  <-- keeping")
                break
            print(f"seed {s}: {secs:.1f}s, winner kept {frac*100:.0f}% HP")
        if best is None:
            sys.exit("no good fight found; widen the search or retune TAU")
        sim = best
    else:
        sim = run_fight(roster, a_id, b_id, args.seed if args.seed is not None else 0)

    out = args.out or os.path.join(ROOT, "out", f"{a_id}_vs_{b_id}_{sim.seed}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(sim.to_log(), open(out, "w"))
    w = sim.balls[sim.winner]
    print(f"{sim.balls[0].name} vs {sim.balls[1].name}")
    print(f"  winner: {w.name}   {sim.t:.1f}s   {len(sim.events)} events")
    print(f"  log: {out}")


if __name__ == "__main__":
    main()
