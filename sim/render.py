"""
Step 3 of the pipeline: the renderer.

Reads an event log written by sim/engine.py and draws every frame at
1080x1920 / 60fps, piping raw rgb24 bytes straight into ffmpeg.

  python sim/render.py out/tralalero_vs_boneca_0.json
  python sim/render.py out/fight.json --out out/fight.mp4
  python sim/render.py out/fight.json --preview 6      # first 6 seconds only
  python sim/render.py out/fight.json --frame 900      # dump one PNG and stop

The renderer NEVER decides anything. Every position, hp, stat and damage
number on screen is read out of the log; this file only turns those numbers
into pixels. If a value isn't in the log, it isn't drawn.
"""
import argparse, json, math, os, shutil, subprocess, sys, time
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1920
FPS = 60
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART_DIR = os.path.join(ROOT, "assets", "balls")

# ------------------------------------------------------------------- palette
# One ball colour per character, themed to who they are. This is the only
# place colours live -- retune here, nowhere else.
# Spread on hue AND luma. Four of the old nine sat in the orange/gold wedge --
# tungtung vs cappuccino were 5 degrees of hue and 7 luma apart, i.e. the same
# ball twice. Only two are ever on screen, so what matters is all 36 PAIRS.
COLORS = {
    "tralalero":   "#2FA6FF",   # shark blue
    "bombardiro":  "#E01E3C",   # ordnance red (was olive-gold: collided x3)
    "tungtung":    "#C8743C",   # drum wood -- now the only orange
    "ballerina":   "#FF4FA8",   # ballet magenta
    "boneca":      "#A6F03A",   # frog lime
    "cappuccino":  "#F5E3C8",   # crema cream (foam IS cream; frees the orange)
    "brrbrr":      "#17D9C4",   # cold teal (was a second green)
    "chimpanzini": "#FFC61E",   # banana yellow
    "lirili":      "#9B6BFF",   # clock violet
}
# When a matchup's two primaries are too close to tell apart, fighter 1 swaps
# to its alt, which sits on the far side of the luma split from its primary.
ALT_COLORS = {
    "tralalero":   "#7FD4FF",
    "bombardiro":  "#FF6B5A",
    "tungtung":    "#F0A45C",
    "ballerina":   "#FF77D6",
    "boneca":      "#4FA81E",
    "cappuccino":  "#B89878",
    "brrbrr":      "#0B8F84",
    "chimpanzini": "#C98A00",
    "lirili":      "#C9A6FF",
}
DEFAULT_COLOR = "#DDDDDD"
DEFAULT_ALT = "#9AA0B5"

BG_TOP, BG_BOT = (10, 12, 24), (34, 28, 62)
ARENA_FILL = (8, 9, 18)
ARENA_EDGE = (120, 140, 200)
INK = (236, 240, 255)
DIM = (150, 158, 190)

# ---------------------------------------------------------- effect durations
# All in frames at 60fps. These are look knobs, tune freely.
FLASH_F = 5          # white flash on the ball that got hit
SQUASH_F = 11        # squash-and-stretch deform
DMG_F = 44           # damage number pop + float + fade
SHAKE_F = 13         # screen shake decay
BOOM_F = 30          # explosion shockwave ring
SLIP_F = 34          # banana slip burst
JACKPOT_F = 40
COMBO_F = 42
WALL_F = 9
BRANCH_F = 26
PIROU_F = 22
OUTRO_SEC = 2.4      # winner card held after the KO

# Platform-chrome safe areas, as the union of TikTok's and Shorts' worst case.
# Anything persistent and load-bearing lives inside x[HUD_X0,HUD_X1],
# y[DEAD_TOP,DEAD_BOT]. The old HUD broke both ends: the first fighter's name
# sat at y=92 under the status bar, and the stats panels at y=1636 were behind
# the caption block, i.e. invisible to every viewer.
DEAD_TOP = 210       # status bar / notch / For-You tabs
DEAD_BOT = 1500      # username + caption + sound ticker + CTA
HUD_X0, HUD_X1 = 60, 960   # 960 keeps clear of the right action rail
CAP_BAND = (1180, 1420)    # reserved for burned-in captions in step 5


# --------------------------------------------------------------------- utils
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    t = clamp(t, 0.0, 1.0)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def shade(c, f):
    return tuple(int(clamp(v * f, 0, 255)) for v in c)


def ease_out(t):
    return 1.0 - (1.0 - t) ** 3


def luma(c):
    """Rec.709 luma, 0-255. Grayscale separation is the standard proxy for
    'still readable under any colour-blindness', so we gate on it."""
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def hue_deg(c):
    mx, mn = max(c), min(c)
    if mx == mn:
        return 0.0
    r, g, b = c
    if mx == r:
        h = (g - b) / (mx - mn) % 6
    elif mx == g:
        h = (b - r) / (mx - mn) + 2
    else:
        h = (r - g) / (mx - mn) + 4
    return h * 60.0


def hue_gap(a, b):
    d = abs(hue_deg(a) - hue_deg(b))
    return min(d, 360.0 - d)


def pick_colors(ids):
    """Two balls that look alike make an unwatchable fight. Fighter 0 keeps its
    primary; fighter 1 swaps to its alt if the pair is too close to separate."""
    c0 = rgb(COLORS.get(ids[0], DEFAULT_COLOR))
    c1 = rgb(COLORS.get(ids[1], DEFAULT_COLOR))
    if abs(luma(c0) - luma(c1)) < 45 and hue_gap(c0, c1) < 30:
        c1 = rgb(ALT_COLORS.get(ids[1], DEFAULT_ALT))
    return [c0, c1]


# --------------------------------------------------------------------- fonts
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\ariblk.ttf",      # Arial Black -- the good one
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
_fonts = {}


def font(size):
    f = _fonts.get(size)
    if f is None:
        if _FONT_PATH:
            f = ImageFont.truetype(_FONT_PATH, size)
        else:
            f = ImageFont.load_default()   # ugly, but never crash
        _fonts[size] = f
    return f


def fit_font(text, max_w, size, min_size=24):
    """Largest font at or below `size` that keeps `text` inside max_w."""
    while size > min_size and font(size).getlength(text) > max_w:
        size -= 2
    return font(size)


# ------------------------------------------------------------- sprite caches
# Pillow has no antialiasing on shape drawing, so anything round is rendered
# once at 4x and downsampled, then cached by (colour, rounded size).
_cache = {}
SS = 4


def _key(*a):
    return a


def disc(color, size, alpha=255):
    size = max(2, int(round(size / 2)) * 2)
    k = _key("disc", color, size, alpha)
    s = _cache.get(k)
    if s is None:
        big = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        ImageDraw.Draw(big).ellipse([0, 0, size * SS - 1, size * SS - 1],
                                    fill=color + (alpha,))
        s = big.resize((size, size), Image.LANCZOS)
        _cache[k] = s
    return s


def ring(color, size, width, alpha=255):
    size = max(4, int(round(size / 2)) * 2)
    width = max(1, int(round(width)))
    k = _key("ring", color, size, width, alpha)
    s = _cache.get(k)
    if s is None:
        big = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
        ImageDraw.Draw(big).ellipse([width * SS // 2, width * SS // 2,
                                     size * SS - 1 - width * SS // 2,
                                     size * SS - 1 - width * SS // 2],
                                    outline=color + (alpha,), width=width * SS)
        s = big.resize((size, size), Image.LANCZOS)
        _cache[k] = s
    return s


def glow(color, size, strength=150):
    """Soft radial falloff, used behind balls / projectiles / sparks."""
    size = max(8, int(round(size / 8)) * 8)
    k = _key("glow", color, size, strength)
    s = _cache.get(k)
    if s is None:
        n = 64
        g = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(g)
        steps = 20
        for i in range(steps):
            t = i / steps
            r = n / 2 * (1.0 - t)
            a = int(strength * (t ** 2.2) / steps * 6)
            d.ellipse([n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r],
                      fill=color + (min(255, a),))
        g = g.filter(ImageFilter.GaussianBlur(3))
        s = g.resize((size, size), Image.BILINEAR)
        _cache[k] = s
    return s


def arc_sprite(color, size, width, sweep):
    """Open arc used for Tung Tung's bat blur and Ballerina's rings."""
    size = max(8, int(round(size / 4)) * 4)
    width = max(2, int(round(width)))
    k = _key("arc", color, size, width, sweep)
    s = _cache.get(k)
    if s is None:
        n = size * 2
        big = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        pad = width  # keep the stroke inside the box
        ImageDraw.Draw(big).arc([pad, pad, n - 1 - pad, n - 1 - pad],
                                -sweep / 2, sweep / 2,
                                fill=color + (255,), width=width * 2)
        s = big.resize((size, size), Image.LANCZOS)
        _cache[k] = s
    return s


def paste(dst, src, cx, cy, alpha=1.0):
    """Alpha-paste src centred on (cx, cy), optionally faded."""
    if alpha <= 0.01:
        return
    x, y = int(cx - src.width / 2), int(cy - src.height / 2)
    if x > W or y > H or x + src.width < 0 or y + src.height < 0:
        return
    if alpha < 0.99:
        m = src.getchannel("A").point(lambda a, f=alpha: int(a * f))
        dst.paste(src, (x, y), m)
    else:
        dst.paste(src, (x, y), src)


# ------------------------------------------------------------------ ball art
class BallArt:
    """The ball itself: colour disc, character art (or placeholder), rim.

    Built once at startup at 4x and cached; per frame we only rotate/resize
    the finished sprite, which is what keeps 5,400 frames tractable.
    """

    def __init__(self, cid, name, radius, color):
        self.color = color
        n = int(radius) * 2
        big = Image.new("RGBA", (n * SS, n * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        e = n * SS - 1
        # solid colour body with a top-lit vertical gradient
        top = mix(self.color, (255, 255, 255), 0.30)
        bot = shade(self.color, 0.55)
        for yy in range(0, n * SS, SS):
            d.rectangle([0, yy, n * SS, yy + SS],
                        fill=mix(top, bot, yy / (n * SS)) + (255,))
        mask = Image.new("L", (n * SS, n * SS), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, e, e], fill=255)
        big.putalpha(mask)
        d.ellipse([0, 0, e, e], outline=mix(self.color, (255, 255, 255), 0.55) + (255,),
                  width=int(8 * SS))
        d.ellipse([int(4 * SS), int(4 * SS), e - int(4 * SS), e - int(4 * SS)],
                  outline=shade(self.color, 0.45) + (200,), width=int(3 * SS))
        # top-left sheen
        d.ellipse([int(n * SS * 0.18), int(n * SS * 0.11),
                   int(n * SS * 0.60), int(n * SS * 0.40)],
                  fill=(255, 255, 255, 46))
        sprite = big.resize((n, n), Image.LANCZOS)

        art = self._load_art(cid, n)
        if art is None:
            art = self._placeholder(name, n)
        sprite.paste(art, (0, 0), art)

        self.sprite = sprite
        # pure-white silhouette of the same ball, for the on-hit flash
        self.flash = Image.new("RGBA", sprite.size, (255, 255, 255, 255))
        self.flash.putalpha(sprite.getchannel("A"))

    def _load_art(self, cid, n):
        p = os.path.join(ART_DIR, cid + ".png")
        if not os.path.exists(p):
            return None
        im = Image.open(p).convert("RGBA")
        inner = int(n * 0.78)
        im = im.resize((inner, inner), Image.LANCZOS)
        out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        out.paste(im, ((n - inner) // 2, (n - inner) // 2), im)
        mask = Image.new("L", (n * SS, n * SS), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, n * SS - 1, n * SS - 1], fill=255)
        out.putalpha(Image.composite(out.getchannel("A"),
                                     Image.new("L", (n, n), 0),
                                     mask.resize((n, n), Image.LANCZOS)))
        return out

    def _placeholder(self, name, n):
        """No art on disk yet -- draw the character's initials, big and bold."""
        initials = "".join(w[0] for w in name.split()[:3]).upper()[:3]
        layer = Image.new("RGBA", (n * SS, n * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        size = int(n * SS * (0.46 if len(initials) < 3 else 0.34))
        f = font(size)
        d.text((n * SS / 2, n * SS / 2), initials, font=f, anchor="mm",
               fill=(255, 255, 255, 240),
               stroke_width=int(6 * SS), stroke_fill=shade(self.color, 0.15) + (255,))
        return layer.resize((n, n), Image.LANCZOS)

    def deformed(self, k, ang, flash=False):
        """Squash along `ang`, stretch across it. k=0 returns the base sprite.

        BICUBIC, not BILINEAR: this is a 3-resample chain on a disc with a
        high-contrast rim, and bilinear visibly softens the very edge that
        makes the ball read as solid.
        """
        s = self.flash if flash else self.sprite
        if k < 0.015:
            return s
        deg = math.degrees(ang)
        s = s.rotate(-deg, Image.BICUBIC, expand=True)
        w = max(4, int(s.width * (1 + k)))
        h = max(4, int(s.height * (1 - k * 0.85)))
        s = s.resize((w, h), Image.BICUBIC)
        return s.rotate(deg, Image.BICUBIC, expand=True)


# ------------------------------------------------------- event -> effect prep
class Effects:
    """Turns the event list into per-frame effect spawns + a shake curve.

    Events carry float timestamps; frames are a 60fps grid. Every event is
    snapped to the nearest frame and then lives for a fixed decay window.
    """

    def __init__(self, log, nframes):
        self.spawn = {}
        self.shake = [0.0] * (nframes + 8)
        events = self._merge_hits(log["events"])
        dmgs = [e["dmg"] for e in events if "dmg" in e]
        self.dmg_hi = max(dmgs) if dmgs else 1

        for e in events:
            f = int(round(e["t"] * FPS))
            if f >= nframes + 8:
                f = nframes + 7
            t = e["type"]
            if t in ("hit", "combo_hit", "bomb_hit"):
                rel = math.log1p(e["dmg"]) / math.log1p(self.dmg_hi)
                self._add(f, {"k": "hit", "dur": DMG_F, "to": e["to"], "by": e["by"],
                              "dmg": e["dmg"], "x": e["x"], "y": e["y"], "rel": rel,
                              "kind": t, "roll": e.get("roll"),
                              "jackpot": bool(e.get("jackpot"))})
                if e.get("jackpot"):
                    self._add(f, {"k": "jackpot", "dur": JACKPOT_F, "by": e["by"]})
                # No floor. A constant 4px jitter on every one of ~150 hits
                # reads as a shaky camera, not as impact -- shake has to be
                # absent most of the time for it to mean anything.
                if rel >= 0.20:
                    amp = rel * rel * 46
                    dur = 10 + int(round(rel * 8))
                    for i in range(dur):
                        j = f + i
                        if j < len(self.shake):
                            self.shake[j] = max(self.shake[j],
                                                amp * (1 - i / dur) ** 2)
            elif t == "explosion":
                self._add(f, {"k": "boom", "dur": BOOM_F, "x": e["x"], "y": e["y"]})
                for i in range(SHAKE_F):
                    j = f + i
                    if j < len(self.shake):
                        self.shake[j] = max(self.shake[j], 26 * (1 - i / SHAKE_F) ** 2)
            elif t == "slip":
                self._add(f, {"k": "slip", "dur": SLIP_F, "x": e["x"], "y": e["y"]})
            elif t == "combo":
                self._add(f, {"k": "combo", "dur": COMBO_F, "by": e["by"], "n": e["n"]})
            elif t == "pirouette":
                self._add(f, {"k": "pirou", "dur": PIROU_F, "by": e["by"]})
            elif t == "branch":
                self._add(f, {"k": "branch", "dur": BRANCH_F})
            elif t == "wall":
                self._add(f, {"k": "wall", "dur": WALL_F, "x": e["x"], "y": e["y"],
                              "by": e["by"]})
            elif t == "ko":
                for i in range(30):
                    j = f + i
                    if j < len(self.shake):
                        self.shake[j] = max(self.shake[j], 54 * (1 - i / 36) ** 2)

    @staticmethod
    def _merge_hits(events):
        """One damage number per victim per frame.

        Cappuccino's combo fires up to a dozen `combo_hit`s on the same
        timestamp; drawn one-per-event they stack into an unreadable column.
        Same frame + same victim collapses into a single summed number, which
        is still only what the log says -- just added up.
        """
        HITS = ("hit", "combo_hit", "bomb_hit")
        out, groups = [], {}
        for e in events:
            if e["type"] not in HITS:
                out.append(e)
                continue
            key = (int(round(e["t"] * FPS)), e["to"])
            g = groups.get(key)
            if g is None:
                g = dict(e)
                groups[key] = g
                out.append(g)
            else:
                g["dmg"] += e["dmg"]
                # a bomb or a combo in the group names the whole number
                if e["type"] != "hit":
                    g["type"] = e["type"]
                if e.get("roll") is not None:
                    g["roll"] = e["roll"]
                g["jackpot"] = g.get("jackpot") or e.get("jackpot")
        return out

    def _add(self, f, fx):
        fx["f0"] = f
        self.spawn.setdefault(f, []).append(fx)

    def active(self, f):
        """Every effect alive on frame f (effects are short, this stays cheap)."""
        out = []
        for f0 in range(max(0, f - DMG_F), f + 1):
            for fx in self.spawn.get(f0, ()):
                if f - f0 < fx["dur"]:
                    out.append(fx)
        return out


# ------------------------------------------------------------------ renderer
class Renderer:
    def __init__(self, log):
        self.log = log
        self.meta = log["meta"]
        self.frames = log["frames"]
        self.arena = self.meta["arena"]
        self.ball_r = self.meta["ball_r"]
        self.fighters = self.meta["fighters"]
        self.ids = [f["id"] for f in self.fighters]
        self.colors = pick_colors(self.ids)
        self.art = [BallArt(f["id"], f["name"], self.ball_r, self.colors[i])
                    for i, f in enumerate(self.fighters)]
        self.fx = Effects(log, len(self.frames))
        self.bg = self._background()
        self.shake_dirs = [(math.cos(i * 2.399), math.sin(i * 2.399)) for i in range(64)]
        self.ghost = self._ghost_series()

    def _ghost_series(self):
        """Lagging HP fraction per frame, for the chip bar.

        With a 90k pool an early hit moves the real bar by 0.006% -- sub-pixel,
        so for the first ~40s the bar tells the viewer nothing. A second bar
        that trails the real one by ~0.4s turns every one of ~150 hits into a
        visible red sliver draining away. Precomputed rather than accumulated
        per call, so --at can render any frame in isolation.
        """
        out, g = [], [1.0, 1.0]
        for f in self.frames:
            row = []
            for i in range(2):
                frac = clamp(f["balls"][i]["hp"] / self.fighters[i]["max_hp"], 0, 1)
                g[i] = frac if frac > g[i] else g[i] + (frac - g[i]) * 0.06
                row.append(max(g[i], frac))
            out.append(row)
        return out

    # -- static layers -------------------------------------------------------
    def _background(self):
        bg = Image.new("RGB", (W, H), BG_TOP)
        d = ImageDraw.Draw(bg)
        for y in range(0, H, 4):                      # vertical gradient
            d.rectangle([0, y, W, y + 4], fill=mix(BG_TOP, BG_BOT, y / H))
        cx, cy, r = self.arena["cx"], self.arena["cy"], self.arena["r"]
        # The arena reads as a PIT: lifted at the rim, falling to near-black at
        # the centre. The old flat fill was ~3 luma off the background, so the
        # floor did not visually exist. The old inner rings were ~5 code values
        # and would be quantized away by the platform re-encode -- dropped.
        paste(bg, glow((70, 90, 190), int(r * 2.9), 110), cx, cy)
        paste(bg, disc((0, 0, 0), int(r * 2) + 20, 255), cx, cy, 0.55)
        rim, steps = (30, 32, 60), 48
        for k in range(steps):
            f = 1.0 - k / steps
            paste(bg, disc(mix(rim, ARENA_FILL, k / steps),
                           max(2, int(r * 2 * f))), cx, cy)

        # Vignette the platform-chrome dead zones: confirms nothing lives there
        # and hands the encoder flat, cheap regions.
        vg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dv = ImageDraw.Draw(vg)
        for y in range(0, DEAD_TOP, 4):
            dv.rectangle([0, y, W, y + 4],
                         fill=(0, 0, 0, int(115 * (1 - y / DEAD_TOP))))
        for y in range(DEAD_BOT, H, 4):
            dv.rectangle([0, y, W, y + 4],
                         fill=(0, 0, 0, int(115 * (y - DEAD_BOT) / (H - DEAD_BOT))))
        bg = Image.alpha_composite(bg.convert("RGBA"), vg).convert("RGB")

        # Static noise kills gradient banding at delivery bitrate, and because
        # it is identical every frame it costs bits only in the I-frame.
        nz = Image.effect_noise((W, H), 6).convert("RGB")
        bg = Image.blend(bg, ImageChops.add(bg, nz, scale=1, offset=-128), 0.06)
        return bg

    # -- world helpers -------------------------------------------------------
    def ball_xy(self, i, b, sx, sy):
        return b["x"] + sx, b["y"] + sy

    def draw_branches(self, im, frame, sx, sy, pop):
        cx, cy = self.arena["cx"] + sx, self.arena["cy"] + sy
        r = self.arena["r"]
        d = ImageDraw.Draw(im, "RGBA")
        bark, leaf = (92, 62, 38), rgb(COLORS["brrbrr"])
        for k, br in enumerate(frame["branches"]):
            a, ln = br["a"], br["len"]
            ex, ey = cx + math.cos(a) * r * ln, cy + math.sin(a) * r * ln
            d.line([cx, cy, ex, ey], fill=bark + (190,), width=11)
            d.line([cx, cy, ex, ey], fill=shade(bark, 1.5) + (120,), width=4)
            for s in (-0.42, 0.42):                    # two little forks
                fa = a + s
                fx_ = ex - math.cos(a) * 60 + math.cos(fa) * 52
                fy_ = ey - math.sin(a) * 60 + math.sin(fa) * 52
                d.line([ex - math.cos(a) * 60, ey - math.sin(a) * 60, fx_, fy_],
                       fill=bark + (170,), width=6)
                paste(im, disc(leaf, 20), fx_, fy_, 0.85)
            grow = 1.0 + (0.9 * pop if k == len(frame["branches"]) - 1 else 0.0)
            paste(im, glow(leaf, int(70 * grow), 130), ex, ey)
            paste(im, disc(leaf, int(24 * grow)), ex, ey)

    def draw_trail(self, im, i, fi, sx, sy):
        """Motion trail. Tralalero's grows with his speed multiplier."""
        col = self.colors[i]
        n = 9
        if self.ids[i] == "tralalero":
            sp = self.frames[fi]["balls"][i].get("speed", 1.0)
            n = int(clamp(8 + (sp - 1.0) * 26, 8, 30))
        boost = self.frames[fi]["balls"][i].get("boost", 0.0)
        if boost > 0.01:
            n = max(n, int(10 + boost * 24))
        for k in range(n, 0, -1):
            j = fi - k
            if j < 0:
                continue
            b = self.frames[j]["balls"][i]
            t = 1.0 - k / n
            paste(im, disc(col, int(self.ball_r * 2 * (0.25 + 0.62 * t))),
                  b["x"] + sx, b["y"] + sy, 0.035 + 0.075 * t)

    def draw_proj(self, im, frame, sx, sy):
        d = ImageDraw.Draw(im, "RGBA")
        for p in frame["proj"]:
            x, y = p["x"] + sx, p["y"] + sy
            if p["k"] == "bomb":
                paste(im, glow((255, 140, 60), 110, 150), x, y)
                paste(im, disc((28, 28, 34), 46), x, y)
                paste(im, disc((70, 72, 84), 18), x - 9, y - 9)
                d.line([x, y - 26, x, y - 44], fill=(210, 210, 220, 230), width=5)
                d.line([x - 16, y - 38, x + 16, y - 38], fill=(210, 210, 220, 200), width=4)
                paste(im, disc((255, 220, 120), 16), x, y - 50)   # lit fuse
            else:   # banana peel
                paste(im, glow(rgb(COLORS["chimpanzini"]), 90, 120), x, y)
                for a in (0.4, 2.5, 4.4):
                    d.line([x, y, x + math.cos(a) * 30, y + math.sin(a) * 30],
                           fill=rgb(COLORS["chimpanzini"]) + (240,), width=13)
                paste(im, disc((190, 150, 30), 16), x, y)

    def draw_signature(self, im, fi, i, x, y, frame):
        """Per-character always-on signature FX, from characters.json."""
        cid, col = self.ids[i], self.colors[i]
        b = frame["balls"][i]
        t = frame["t"]
        if cid == "tungtung":
            rate = b["spin_rate"]
            sweep = clamp(60 + (rate - 1) * 220, 60, 300)
            size = int(self.ball_r * 2 * clamp(1.25 + (rate - 1) * 0.5, 1.25, 2.1))
            a = arc_sprite(shade(col, 1.25), size, 9, int(sweep / 10) * 10)
            paste(im, a.rotate(-b["spin"], Image.BILINEAR), x, y, 0.75)
            paste(im, a.rotate(-b["spin"] + 26, Image.BILINEAR), x, y, 0.3)
        elif cid == "ballerina":
            buff = b["buff"]
            rings = int(clamp(1 + (buff - 1) / 0.12, 1, 6))
            for k in range(rings):
                sz = int(self.ball_r * 2 * (1.16 + 0.17 * k))
                ang = t * (70 + 40 * k) * (1 if k % 2 else -1)
                a = arc_sprite(mix(col, (255, 255, 255), 0.25 + 0.1 * k), sz, 8, 250)
                paste(im, a.rotate(ang, Image.BILINEAR), x, y,
                      clamp(0.55 + 0.1 * buff, 0.5, 1.0))
            for k in range(6):                          # coffee sparkles
                ang = t * 2.2 + k * math.pi / 3
                rr = self.ball_r * 1.5
                paste(im, disc(mix(col, (255, 245, 225), 0.6), 16),
                      x + math.cos(ang) * rr, y + math.sin(ang) * rr, 0.7)
        elif cid == "cappuccino":
            for k in range(3):                          # steam wisps (FX: TBD)
                ph = t * 1.5 + k * 1.7
                dy = -self.ball_r - 18 - (ph % 2.0) * 44
                dx = math.sin(ph * 2.4) * 16 + (k - 1) * 22
                paste(im, disc((240, 226, 205), int(16 + (ph % 2.0) * 14)),
                      x + dx, y + dy, 0.16 * (1 - (ph % 2.0) / 2.0))
        elif cid == "brrbrr":
            paste(im, ring(shade(col, 0.8), int(self.ball_r * 2 * 1.14), 4), x, y, 0.5)
        elif cid == "lirili" and b.get("burst", 0) > 0:
            paste(im, ring((160, 210, 255), int(self.ball_r * 2 * 1.3), 6), x, y, 0.9)
            d = ImageDraw.Draw(im, "RGBA")
            for hand, ln in ((t * 1.2, 0.8), (t * 0.2, 0.55)):
                d.line([x, y, x + math.cos(hand - 1.57) * self.ball_r * ln,
                        y + math.sin(hand - 1.57) * self.ball_r * ln],
                       fill=(220, 240, 255, 220), width=6)
        if b.get("boost", 0) > 0.01:                    # skidding on a peel
            d = ImageDraw.Draw(im, "RGBA")
            prev = self.frames[max(0, fi - 3)]["balls"][i]
            vx, vy = x - prev["x"], y - prev["y"]
            n = math.hypot(vx, vy) or 1.0
            vx, vy = vx / n, vy / n
            for k in range(4):
                off = (k - 1.5) * 26
                px, py = x - vy * off, y + vx * off
                d.line([px - vx * 40, py - vy * 40, px - vx * 150, py - vy * 150],
                       fill=rgb(COLORS["chimpanzini"]) + (110,), width=6)

    # -- effect drawing ------------------------------------------------------
    def draw_effects(self, im, fi, frame, sx, sy, fx_list):
        d = ImageDraw.Draw(im, "RGBA")
        for fx in fx_list:
            age = fi - fx["f0"]
            p = age / fx["dur"]
            k = fx["k"]
            if k == "wall":
                paste(im, glow(self.colors[fx["by"]], 120, 140),
                      fx["x"] + sx, fx["y"] + sy, (1 - p) * 0.55)
            elif k == "boom":
                e = ease_out(p)
                paste(im, ring((255, 190, 90), int(80 + e * 620), int(22 * (1 - p)) + 3),
                      fx["x"] + sx, fx["y"] + sy, (1 - p) ** 1.6)
                paste(im, glow((255, 160, 60), int(200 + e * 260), 220),
                      fx["x"] + sx, fx["y"] + sy, (1 - p) ** 2)
                if p < 0.25:
                    paste(im, disc((255, 245, 220), int(150 * (1 - p * 4) + 40)),
                          fx["x"] + sx, fx["y"] + sy, 1 - p * 4)
            elif k == "slip":
                for j in range(8):
                    a = j * math.pi / 4 + p * 2
                    rr = ease_out(p) * 150
                    paste(im, disc(rgb(COLORS["chimpanzini"]), int(26 * (1 - p))),
                          fx["x"] + sx + math.cos(a) * rr,
                          fx["y"] + sy + math.sin(a) * rr, 1 - p)
            elif k == "pirou":
                paste(im, ring(self.colors[fx["by"]],
                               int(self.ball_r * 2 * (1.1 + ease_out(p) * 1.4)),
                               int(10 * (1 - p)) + 2),
                      *self.ball_xy(fx["by"], frame["balls"][fx["by"]], sx, sy),
                      alpha=(1 - p) * 0.9)
            elif k == "jackpot":
                b = frame["balls"][fx["by"]]
                x, y = b["x"] + sx, b["y"] + sy
                for j in range(12):
                    a = j * math.pi / 6 + p
                    rr = ease_out(p) * 260
                    d.line([x + math.cos(a) * rr * 0.4, y + math.sin(a) * rr * 0.4,
                            x + math.cos(a) * rr, y + math.sin(a) * rr],
                           fill=(255, 215, 80, int(220 * (1 - p))), width=9)
                paste(im, glow((255, 210, 70), int(300 + ease_out(p) * 260), 220),
                      x, y, (1 - p) ** 1.5)
                if p < 0.55:
                    d.text((x, y - self.ball_r - 150), "JACKPOT", font=font(66),
                           anchor="mm", fill=(255, 225, 110, 255),
                           stroke_width=8, stroke_fill=(60, 30, 0, 255))
            elif k == "combo":
                b = frame["balls"][fx["by"]]
                x, y = b["x"] + sx, b["y"] + sy
                for j in range(5):                       # blade slashes
                    a = j * 1.1 + p * 3.0
                    rr = 70 + ease_out(p) * 170
                    d.line([x + math.cos(a) * rr * 0.5, y + math.sin(a) * rr * 0.5,
                            x + math.cos(a) * rr, y + math.sin(a) * rr],
                           fill=self.colors[fx["by"]] + (int(220 * (1 - p)),), width=7)
                sc = 1.0 + 0.25 * (1 - ease_out(min(1, p * 4)))
                d.text((x, max(560, y - self.ball_r - 310 - p * 40)),
                       f"COMBO x{fx['n']}", font=font(int(54 * sc)), anchor="mm",
                       fill=(255, 245, 230, int(255 * (1 - p ** 2))),
                       stroke_width=8, stroke_fill=(40, 20, 10, int(255 * (1 - p ** 2))))

        # damage numbers last so they sit on top of everything in the arena
        for fx in fx_list:
            if fx["k"] != "hit":
                continue
            age = fi - fx["f0"]
            p = age / fx["dur"]
            rel = fx["rel"]
            pop = 1.0 + 0.28 * (1 - ease_out(min(1.0, age / 5.0)))
            # 26px was below every mobile legibility floor; early numbers were
            # simply not read. Magnitude now reads through SIZE, not through a
            # dark red fill -- saturated red carries its meaning in the
            # half-resolution chroma plane and mushes at platform bitrate, so
            # the ramp stays high-luma all the way to the top.
            size = int((48 + (rel ** 1.4) * 68) * pop)
            col = mix(mix((255, 255, 255), (255, 227, 107), rel * 1.6),
                      (255, 162, 61), max(0.0, rel * 1.9 - 0.9))
            a = int(255 * clamp((1 - p) * 2.2, 0, 1))
            # ball 0's damage always drifts left, ball 1's right, so the two
            # numbers from a single collision never sit on top of each other
            side = -1 if fx["to"] == 0 else 1
            yy = (fx["y"] + sy - 70 - ease_out(p) * 120
                  - (0 if fx["to"] == 0 else 158))
            yy = clamp(yy, 560, 1400)              # stay inside the arena band
            txt = f"-{fx['dmg']:,}"
            f = font(size)
            stroke = max(5, size // 9)
            # Shrink to fit before clamping. A late-game number can be wider
            # than the frame, and clamping to [half, W-half] is meaningless
            # once half > W/2 -- it used to shove the text off the left edge.
            while size > 40 and d.textlength(txt, font=f) + 2 * stroke > W - 48:
                size -= 4
                f = font(size)
                stroke = max(5, size // 9)
            half = d.textlength(txt, font=f) / 2 + stroke + 28
            lo, hi = half, W - half
            xx = clamp(fx["x"] + sx + side * 165, lo, hi) if lo <= hi else W / 2
            # Hard offset shadow under a pure-black stroke: depth, and it
            # survives compression far better than a soft glow would.
            d.text((xx, yy + 5), txt, font=f, anchor="mm",
                   fill=(0, 0, 0, int(a * 0.7)))
            d.text((xx, yy), txt, font=f, anchor="mm",
                   fill=col + (a,), stroke_width=stroke,
                   stroke_fill=(0, 0, 0, a))
            if fx["kind"] == "bomb_hit" and p < 0.5:
                d.text((xx, yy - size * 0.85), "BOOM", font=font(40),
                       anchor="mm", fill=(255, 170, 60, a), stroke_width=6,
                       stroke_fill=(30, 10, 0, a))
            if fx.get("roll") is not None and p < 0.6:   # Boneca's dice
                self.draw_dice(im, xx, yy - size * 0.95, fx["roll"],
                               clamp((0.6 - p) * 3, 0, 1))

    def draw_dice(self, im, x, y, roll, alpha):
        d = ImageDraw.Draw(im, "RGBA")
        s = 74
        d.rounded_rectangle([x - s / 2, y - s / 2, x + s / 2, y + s / 2], radius=12,
                            fill=(250, 250, 255, int(230 * alpha)),
                            outline=(40, 30, 60, int(230 * alpha)), width=4)
        d.text((x, y + 2), str(roll), font=font(48), anchor="mm",
               fill=(40, 30, 60, int(255 * alpha)))

    # -- hud -----------------------------------------------------------------
    def draw_hud(self, im, frame, fi):
        d = ImageDraw.Draw(im, "RGBA")
        for i, f in enumerate(self.fighters):
            col = self.colors[i]
            b = frame["balls"][i]
            top = 216 + i * 118
            base = top + 42
            name = f["name"].upper()
            d.text((HUD_X0, base), name, font=fit_font(name, 600, 52), anchor="ls",
                   fill=col + (255,), stroke_width=5, stroke_fill=(8, 8, 16, 220))

            # Percentage, not the raw 90,000 pool. "12%" is a number a viewer
            # can feel; "10,842" against 90,000 is arithmetic homework.
            frac = clamp(b["hp"] / f["max_hp"], 0, 1)
            d.text((HUD_X1, base), f"{frac * 100:.0f}%", font=font(48), anchor="rs",
                   fill=INK + (255,), stroke_width=5, stroke_fill=(8, 8, 16, 220))

            by0, by1 = top + 56, top + 84
            d.rounded_rectangle([HUD_X0, by0, HUD_X1, by1], radius=14,
                                fill=(255, 255, 255, 40),
                                outline=(255, 255, 255, 110), width=2)
            span = HUD_X1 - HUD_X0
            gf = self.ghost[fi][i] if fi < len(self.ghost) else frac
            if gf > 0.002:                         # chip bar, drains behind
                d.rounded_rectangle([HUD_X0, by0, HUD_X0 + span * gf, by1],
                                    radius=14, fill=(226, 64, 72, 255))
            if frac > 0.002:
                w = HUD_X0 + span * frac
                d.rounded_rectangle([HUD_X0, by0, w, by1], radius=14, fill=col + (255,))
                d.rounded_rectangle([HUD_X0 + 4, by0 + 4, max(HUD_X0 + 10, w - 4),
                                     by0 + 12], radius=5,
                                    fill=mix(col, (255, 255, 255), 0.45) + (200,))

            # Scaling numbers inline, not in an occluded bottom corner. The
            # sim owns these strings; we only join them.
            chip = "  ".join(f"{lbl.upper()} {val}" for lbl, val in b["panel"])
            d.text((HUD_X0, top + 112), chip, font=font(32), anchor="ls",
                   fill=mix(col, (255, 255, 255), 0.25) + (235,),
                   stroke_width=4, stroke_fill=(8, 8, 16, 210))

        # Clock demoted: nobody watches for it, and it was 72px in the most
        # valuable slot on the frame. Parked between the HUD and the arena.
        secs = frame["t"]
        d.text((W / 2, 495), f"{int(secs) // 60}:{int(secs) % 60:02d}",
               font=font(40), anchor="mm", fill=DIM + (170,),
               stroke_width=4, stroke_fill=(8, 8, 16, 190))

    def draw_winner(self, im, p):
        """Winner card, faded in over the outro tail."""
        d = ImageDraw.Draw(im, "RGBA")
        w = self.meta["winner"]
        a = int(255 * clamp(p * 2.2, 0, 1))
        d.rectangle([0, 0, W, H], fill=(6, 6, 14, int(150 * clamp(p * 2, 0, 1))))
        col = self.colors[w]
        cy = self.arena["cy"]
        paste(im, glow(col, 900, 150), W / 2, cy, clamp(p * 1.5, 0, 1))
        d.text((W / 2, cy - 110), "WINNER", font=font(86), anchor="mm",
               fill=INK + (a,), stroke_width=8, stroke_fill=(8, 8, 16, a))
        name = self.fighters[w]["name"].upper()
        d.text((W / 2, cy + 20), name, font=fit_font(name, W - 120, 112, 44),
               anchor="mm",
               fill=col + (a,), stroke_width=10, stroke_fill=(8, 8, 16, a))
        d.text((W / 2, cy + 150), f"{self.meta['fight_seconds']:.1f}s",
               font=font(52), anchor="mm", fill=DIM + (a,),
               stroke_width=6, stroke_fill=(8, 8, 16, a))

    # -- one frame -----------------------------------------------------------
    def frame_image(self, fi, outro_p=None):
        frame = self.frames[min(fi, len(self.frames) - 1)]
        im = self.bg.copy()

        amp = self.fx.shake[fi] if fi < len(self.fx.shake) else 0.0
        if amp > 0.3:
            dx, dy = self.shake_dirs[fi % 64]
            sx, sy = dx * amp, dy * amp
        else:
            sx = sy = 0.0

        fx_list = self.fx.active(fi)
        branch_pop = max([1 - (fi - f["f0"]) / f["dur"] for f in fx_list
                          if f["k"] == "branch"], default=0.0)

        # arena ring (tints blue while Lirili's time warp is up)
        warp = max(b.get("burst", 0) for b in frame["balls"])
        edge = mix(ARENA_EDGE, (120, 200, 255), 1.0 if warp > 0 else 0.0)
        r2 = int(self.arena["r"] * 2)
        paste(im, ring(edge, r2 + 12, 7), self.arena["cx"] + sx, self.arena["cy"] + sy,
              0.95)
        paste(im, ring(shade(edge, 1.3), r2 + 30, 3), self.arena["cx"] + sx,
              self.arena["cy"] + sy, 0.35)

        if frame["branches"]:
            self.draw_branches(im, frame, sx, sy, branch_pop)
        self.draw_proj(im, frame, sx, sy)

        # balls: trail -> glow -> signature fx -> sprite -> flash
        flash = {}
        squash = {}
        for fx in fx_list:
            if fx["k"] == "hit":
                age = fi - fx["f0"]
                if age < FLASH_F:
                    flash[fx["to"]] = max(flash.get(fx["to"], 0), 1 - age / FLASH_F)
                if age < SQUASH_F:
                    src = frame["balls"][fx["by"]]
                    dst = frame["balls"][fx["to"]]
                    ang = math.atan2(dst["y"] - src["y"], dst["x"] - src["x"])
                    kk = 0.34 * (1 - age / SQUASH_F) * (0.5 + 0.5 * fx["rel"])
                    if kk > squash.get(fx["to"], (0, 0))[0]:
                        squash[fx["to"]] = (kk, ang)

        for i in range(2):
            b = frame["balls"][i]
            x, y = b["x"] + sx, b["y"] + sy
            if b["hp"] <= 0 and fi > 0:
                continue
            self.draw_trail(im, i, fi, sx, sy)
            paste(im, glow(self.colors[i], int(self.ball_r * 3.6), 230), x, y, 0.95)
            self.draw_signature(im, fi, i, x, y, frame)
            kk, ang = squash.get(i, (0.0, 0.0))
            # Deliberately NOT passing spin: tungtung is the only ball that
            # accumulates it, and he reaches ~6 rev/sec, which would blend any
            # character art into a smear. His bat arc spins instead.
            spr = self.art[i].deformed(kk, ang)
            paste(im, spr, x, y)
            fl = flash.get(i, 0.0)
            if fl > 0:
                paste(im, self.art[i].deformed(kk, ang, True),
                      x, y, fl)

        self.draw_effects(im, fi, frame, sx, sy, fx_list)

        if warp > 0:   # blue time-warp wash over the whole arena
            ImageDraw.Draw(im, "RGBA").rectangle(
                [0, 0, W, H], fill=(60, 130, 255, 46))

        self.draw_hud(im, frame, fi)
        if outro_p is not None:
            self.draw_winner(im, outro_p)
        return im


# ----------------------------------------------------------------- ffmpeg io
def find_ffmpeg(explicit=None):
    if explicit:
        return explicit
    p = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if p:
        return p
    try:                                   # optional fallback: bundled binary
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    sys.exit("ffmpeg not found. Install it and put it on PATH, set $FFMPEG, "
             "pass --ffmpeg <path>, or `pip install imageio-ffmpeg`.")


def open_encoder(ffmpeg, out_path, fps):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-an",
           # preset slow is free here: Pillow is the bottleneck, not x264.
           "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-profile:v", "high", "-level", "4.2",
           "-pix_fmt", "yuv420p", "-color_range", "tv",
           # Tag the colour space. Left untagged, the platform transcoder
           # guesses, and a BT.601 guess visibly shifts our ball colours.
           "-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709",
           # aq-mode 3 biases quantization toward dark regions, which is
           # exactly our case: a near-black arena carrying all the detail.
           "-g", "120", "-bf", "3",
           "-x264-params", "aq-mode=3:aq-strength=1.1",
           "-movflags", "+faststart", out_path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


# ------------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser(description="Render a fight log to mp4.")
    ap.add_argument("log", help="event log json from sim/engine.py")
    ap.add_argument("--out", default=None, help="output mp4 (default: next to the log)")
    ap.add_argument("--preview", type=float, default=0,
                    help="render only the first N seconds")
    ap.add_argument("--frame", type=int, default=None,
                    help="dump a single frame as PNG and exit")
    ap.add_argument("--at", type=float, default=None,
                    help="like --frame but in seconds")
    ap.add_argument("--no-outro", action="store_true", help="skip the winner card")
    ap.add_argument("--ffmpeg", default=None, help="path to the ffmpeg binary")
    args = ap.parse_args()

    log = json.load(open(args.log))
    r = Renderer(log)
    total = len(r.frames)

    # single-frame dump: the fast iteration path, no ffmpeg needed
    if args.frame is not None or args.at is not None:
        fi = args.frame if args.frame is not None else int(args.at * FPS)
        fi = int(clamp(fi, 0, total - 1))
        out = args.out or os.path.splitext(args.log)[0] + f"_f{fi}.png"
        t0 = time.time()
        r.frame_image(fi).save(out)
        print(f"frame {fi} (t={r.frames[fi]['t']:.2f}s) -> {out}  "
              f"[{time.time() - t0:.2f}s]")
        return

    ffmpeg = find_ffmpeg(args.ffmpeg)
    out = args.out or os.path.splitext(args.log)[0] + ".mp4"
    n = min(total, int(args.preview * FPS)) if args.preview else total
    outro = 0 if (args.no_outro or args.preview) else int(OUTRO_SEC * FPS)

    print(f"{r.fighters[0]['name']} vs {r.fighters[1]['name']}  "
          f"({log['meta']['fight_seconds']:.1f}s, {n + outro} frames)")
    proc = open_encoder(ffmpeg, out, FPS)
    t0 = time.time()
    try:
        for fi in range(n):
            proc.stdin.write(r.frame_image(fi).tobytes())
            if fi % 120 == 0 and fi:
                el = time.time() - t0
                print(f"  {fi}/{n + outro}  {el:.0f}s elapsed  "
                      f"eta {el / fi * (n + outro - fi):.0f}s", flush=True)
        for k in range(outro):
            proc.stdin.write(r.frame_image(n - 1, outro_p=k / max(1, outro - 1))
                             .tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        sys.exit("ffmpeg died mid-render; run it without -loglevel error to see why")
    proc.wait()
    el = time.time() - t0
    secs = (n + outro) / FPS
    print(f"  wrote {out}")
    print(f"  {el:.1f}s render for {secs:.1f}s of video "
          f"({el / secs:.2f}s per video-second, {(n + outro) / el:.1f} fps)")


if __name__ == "__main__":
    main()
