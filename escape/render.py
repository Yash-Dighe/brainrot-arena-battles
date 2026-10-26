"""
Ring escape: renderer. Reads a log from escape/engine.py and draws it.

Like the arena renderer it never decides anything -- ball position, ring
angles, which rings are alive and when they break all come from the log.

    python escape/render.py out/escape_197.json --at 20      # one PNG
    python escape/render.py out/escape_197.json --preview 8  # first 8s
    python escape/render.py out/escape_197.json              # full mp4
"""
import argparse, colorsys, json, math, os, shutil, subprocess, sys, time
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------- look
BG_TOP, BG_BOT = (8, 9, 18), (20, 16, 38)
INK = (240, 242, 255)
DIM = (130, 136, 170)
BALL = (255, 255, 255)
HUE_IN, HUE_OUT = 0.52, 1.30   # inner ring cyan, sweeping round to magenta/red

SS = 2                 # ring layer supersample
BREAK_F = 26           # frames a broken ring takes to expand and fade
BREAK_GROW = 70        # px it expands by while fading
HIT_F = 9              # frames a ring glows after being bounced on
TRAIL_N = 9            # trail length in frames
BLOOM_DIV, BLOOM_T, BLOOM_GAIN = 4, 120, 1.6
NEAR_F = 22            # frames a near-miss spark lasts
NEAR_MERGE = 10        # near-misses closer than this many frames share one spark
NEAR_GROW = 90         # px the spark ring expands by
BAR_W, BAR_H, BAR_GAP = 640, 16, 8   # progress bar, segment spacing

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\ariblk.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
_fonts = {}


def font(size):
    f = _fonts.get(size)
    if f is None:
        f = ImageFont.truetype(_FONT_PATH, size) if _FONT_PATH else ImageFont.load_default()
        _fonts[size] = f
    return f


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def ease_out(t):
    return 1 - (1 - t) ** 3


def scale(c, f):
    return tuple(int(clamp(v * f, 0, 255)) for v in c)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ring_color(i, n):
    h = HUE_IN + (HUE_OUT - HUE_IN) * (i / max(1, n - 1))
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 0.72, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def disc(size, color, alpha=255):
    """Antialiased disc sprite: drawn at 4x and downsampled."""
    n = max(2, int(round(size)))
    big = Image.new("RGBA", (n * 4, n * 4), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse([0, 0, n * 4 - 1, n * 4 - 1], fill=color + (alpha,))
    return big.resize((n, n), Image.LANCZOS)


class Renderer:
    def __init__(self, log):
        self.log = log
        m = log["meta"]
        self.frames = log["frames"]
        self.fps = m["fps"]
        self.cx, self.cy = m["center"]
        self.rings = m["rings"]
        self.br = m["ball_radius"]
        n = len(self.rings)
        self.colors = [ring_color(i, n) for i in range(n)]

        # ring layer covers the arena plus room for a breaking ring to grow
        reach = int(self.rings[-1]["r"] + self.rings[-1]["width"] + BREAK_GROW + 20)
        self.box = (0, int(self.cy - reach), W, int(self.cy + reach))

        self.bg = self._background()
        self.ball = disc(self.br * 2, BALL)
        self.trail = [disc(self.br * 2 * (0.35 + 0.65 * k / TRAIL_N), (200, 210, 255))
                      for k in range(TRAIL_N)]

        # frame index -> events, for the effects that key off them
        self.breaks = {}     # ring -> frame it broke
        self.hits = {}       # ring -> sorted frames it was bounced on
        self.nears = []      # (frame, x, y, ring), one per burst of near-misses
        for e in log["events"]:
            f = int(e["t"] * self.fps)
            if e["type"] == "escape":
                self.breaks[e["ring"]] = f
            elif e["type"] == "bounce":
                self.hits.setdefault(e["ring"], []).append(f)
            elif e["type"] == "near_miss":
                if not self.nears or f - self.nears[-1][0] >= NEAR_MERGE:
                    self.nears.append((f, e["x"], e["y"], e["ring"]))
        self.final = max(self.breaks.values()) if len(self.breaks) == n else None

    def _background(self):
        col = Image.new("RGB", (1, H))
        for y in range(H):
            col.putpixel((0, y), mix(BG_TOP, BG_BOT, y / (H - 1)))
        return col.resize((W, H))

    # ---------------------------------------------------------------- rings
    def _arc(self, d, r, width, gap_at, gap, color):
        """One ring with its gap, rounded ends, into the supersampled layer."""
        ox, oy = self.cx * SS, (self.cy - self.box[1]) * SS
        R, w = r * SS, width * SS
        start, end = gap_at + gap / 2, gap_at - gap / 2 + 360
        d.arc([ox - R - w / 2, oy - R - w / 2, ox + R + w / 2, oy + R + w / 2],
              start, end, fill=color, width=int(w))
        for a in (start, end):          # round caps
            ex = ox + R * math.cos(math.radians(a))
            ey = oy + R * math.sin(math.radians(a))
            d.ellipse([ex - w / 2, ey - w / 2, ex + w / 2, ey + w / 2], fill=color)

    def _hit_glow(self, i, fi):
        fs = self.hits.get(i)
        if not fs:
            return 0.0
        # latest hit at or before fi (lists are short per ring; linear is fine)
        last = None
        for f in fs:
            if f > fi:
                break
            last = f
        if last is None or fi - last >= HIT_F:
            return 0.0
        return 1 - (fi - last) / HIT_F

    def _near_glow(self, i, fi):
        for f0, _, _, ring in self.nears:
            if f0 > fi:
                break
            if ring == i and fi - f0 < NEAR_F:
                return (1 - (fi - f0) / NEAR_F) ** 2
        return 0.0

    def ring_layer(self, fi):
        bw, bh = self.box[2] - self.box[0], self.box[3] - self.box[1]
        layer = Image.new("RGB", (bw * SS, bh * SS))
        d = ImageDraw.Draw(layer)
        fr = self.frames[fi]
        for i, ring in enumerate(self.rings):
            ang = fr["a"][i]
            col = self.colors[i]
            if ang is not None:
                k = max(self._hit_glow(i, fi), self._near_glow(i, fi))
                self._arc(d, ring["r"], ring["width"] * (1 + 0.5 * k), ang, ring["gap"],
                          mix(col, (255, 255, 255), 0.6 * k))
                continue
            bf = self.breaks.get(i)
            if bf is None or fi - bf >= BREAK_F:
                continue
            # breaking: flash white, expand, fade. The gap keeps turning so
            # the ring reads as the same object.
            p = (fi - bf) / BREAK_F
            ang = (ring["phase"] + ring["spin"] * fr["t"]) % 360
            c = mix((255, 255, 255), col, min(1.0, p * 3))
            c = scale(c, (1 - p) ** 1.5)
            self._arc(d, ring["r"] + BREAK_GROW * ease_out(p),
                      ring["width"] * (1 + 1.5 * (1 - p)), ang, ring["gap"], c)
        return layer.reduce(SS)

    # ---------------------------------------------------------------- frame
    def frame_image(self, fi):
        im = self.bg.copy()
        region = im.crop(self.box)
        region = ImageChops.add(region, self.ring_layer(fi))
        im.paste(region, self.box[:2])

        # trail, oldest first
        for k in range(TRAIL_N):
            j = fi - (TRAIL_N - k)
            if j < 0:
                continue
            f = self.frames[j]
            s = self.trail[k]
            a = int(90 * (k + 1) / TRAIL_N)
            m = s.getchannel("A").point(lambda v, a=a: v * a // 255)
            im.paste(s, (int(f["x"] - s.width / 2), int(f["y"] - s.height / 2)), m)
        f = self.frames[fi]
        im.paste(self.ball, (int(round(f["x"] - self.br)), int(round(f["y"] - self.br))),
                 self.ball)

        self.draw_nears(im, fi)
        im = self.bloom(im)
        self.draw_hud(im, fi)
        return im

    def draw_nears(self, im, fi):
        """A near-miss throws a white spark where the ball clipped the gap
        edge: an expanding ring that fades, over a bright core. Bloom then
        turns it into a flash."""
        d = ImageDraw.Draw(im)
        for f0, x, y, ring in self.nears:
            if f0 > fi:
                break
            if fi - f0 >= NEAR_F:
                continue
            p = (fi - f0) / NEAR_F
            col = scale(mix((255, 255, 255), self.colors[ring], p), (1 - p) ** 0.8)
            r = 14 + NEAR_GROW * ease_out(p)
            d.ellipse([x - r, y - r, x + r, y + r], outline=col,
                      width=max(1, int(12 * (1 - p))))
            c = 26 * (1 - p) ** 2
            if c >= 1:
                d.ellipse([x - c, y - c, x + c, y + c], fill=col)

    def bloom(self, im):
        w, h = W // BLOOM_DIV, H // BLOOM_DIV
        bp = im.resize((w, h), Image.BILINEAR).point(
            lambda v: int(clamp((v - BLOOM_T) * BLOOM_GAIN, 0, 255)))
        bp = bp.filter(ImageFilter.GaussianBlur(5))
        return ImageChops.add(im, bp.resize((W, H), Image.BILINEAR))

    def draw_hud(self, im, fi):
        d = ImageDraw.Draw(im)
        n = len(self.rings)
        broken = sum(1 for a in self.frames[fi]["a"] if a is None)
        y = 200
        self.draw_bar(d, fi, broken, y + 165)
        if self.final is not None and fi >= self.final:
            p = clamp((fi - self.final) / 20, 0, 1)
            col = mix(BG_TOP, INK, ease_out(p))
            d.text((W / 2, y - 10), "ESCAPED", font=font(120), anchor="mm", fill=col)
            d.text((W / 2, y + 100), f"{self.log['meta']['duration']:.1f}s",
                   font=font(56), anchor="mm", fill=mix(BG_TOP, DIM, ease_out(p)))
            return
        # pop the counter for a few frames each time a ring breaks
        last = max((b for b in self.breaks.values() if b <= fi), default=-99)
        pop = 1 - clamp((fi - last) / 12, 0, 1)
        d.text((W / 2, y), f"{broken}/{n}", font=font(int(130 + 36 * pop)),
               anchor="mm", fill=INK)
        d.text((W / 2, y + 100), "rings broken", font=font(40), anchor="mm", fill=DIM)

    def draw_bar(self, d, fi, broken, y):
        """One segment per ring, filled in that ring's colour as it breaks.
        The newest segment flashes white as it fills."""
        n = len(self.rings)
        seg = (BAR_W - BAR_GAP * (n - 1)) / n
        x0 = (W - BAR_W) / 2
        for i in range(n):
            x = x0 + i * (seg + BAR_GAP)
            box = [x, y - BAR_H / 2, x + seg, y + BAR_H / 2]
            if i < broken:
                age = clamp((fi - self.breaks.get(i, -99)) / 15, 0, 1)
                col = mix((255, 255, 255), self.colors[i], ease_out(age))
            else:
                col = mix(BG_BOT, DIM, 0.3)
            d.rounded_rectangle(box, radius=BAR_H / 2, fill=col)


# ----------------------------------------------------------------- ffmpeg io
def find_ffmpeg(explicit=None):
    p = explicit or os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not p:
        sys.exit("ffmpeg not found. Put it on PATH, set $FFMPEG, or pass --ffmpeg.")
    return p


def open_encoder(ffmpeg, out_path, fps):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-profile:v", "high", "-level", "4.2",
           "-pix_fmt", "yuv420p", "-color_range", "tv",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-g", "120", "-bf", "3", "-x264-params", "aq-mode=3:aq-strength=1.1",
           "-movflags", "+faststart", out_path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main():
    ap = argparse.ArgumentParser(description="Render a ring-escape log to mp4.")
    ap.add_argument("log")
    ap.add_argument("--out", default=None)
    ap.add_argument("--preview", type=float, default=0, help="only the first N seconds")
    ap.add_argument("--at", type=float, default=None, help="dump one PNG at this time")
    ap.add_argument("--ffmpeg", default=None)
    args = ap.parse_args()

    with open(args.log) as f:
        log = json.load(f)
    r = Renderer(log)
    base = os.path.splitext(args.log)[0]

    if args.at is not None:
        fi = int(clamp(round(args.at * r.fps), 0, len(r.frames) - 1))
        t0 = time.time()
        out = f"{base}_f{fi}.png"
        r.frame_image(fi).save(out)
        print(f"frame {fi} (t={fi / r.fps:.2f}s) -> {out}  [{time.time() - t0:.2f}s]")
        return

    n = len(r.frames)
    if args.preview:
        n = min(n, int(args.preview * r.fps))
    out = args.out or base + ".mp4"
    enc = open_encoder(find_ffmpeg(args.ffmpeg), out, r.fps)
    t0 = time.time()
    for fi in range(n):
        enc.stdin.write(r.frame_image(fi).tobytes())
        if fi % 300 == 0 and fi:
            el = time.time() - t0
            print(f"  {fi}/{n} frames  ({fi / el:.1f} fps, ~{(n - fi) / (fi / el):.0f}s left)",
                  flush=True)
    enc.stdin.close()
    enc.wait()
    el = time.time() - t0
    print(f"  wrote {out}\n  {el:.1f}s render for {n / r.fps:.1f}s of video "
          f"({n / el:.1f} fps)")


if __name__ == "__main__":
    main()
