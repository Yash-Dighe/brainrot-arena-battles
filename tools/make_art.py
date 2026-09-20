"""
Draws the nine character balls and writes them to assets/balls/<id>.png.

Why code and not a generator: the hard part of a nine-character set is style
CONSISTENCY, and in code that problem does not exist -- every character imports
the same INK, the same INK_W, the same palette, so they match by construction
instead of by rerolling prompts. It is also unambiguously human-authored, which
matters for the originality rules, and one constant change re-cuts all nine.

Design constraints, all forced by where the art actually ends up:

  * The renderer insets art to 78% of the ball and clips it to a circle, so at
    BALL_R=78 the art displays at ~121px. That is the entire detail budget.
    Four to six shapes, three to five flat fills, nothing thinner than ~3px
    at display size.
  * Silhouette carries recognition at that size, not surface detail. The two
    risky pairs are cappuccino/ballerina (both cup-headed) and brrbrr/boneca
    (both green lumps); they are separated by OUTLINE SHAPE, not by colour or
    detail. Run --check and look at the black sheet.
  * A single uniform dark ink outline is what makes nine drawings read as one
    set, and it is what keeps a light character legible against a light ball.
    It is applied globally by dilating the alpha, so no character function has
    to remember to do it.
  * No glow, no rim light, no drop shadow, no ball circle: the renderer owns
    all of those and baking them in doubles them up.

    python tools/make_art.py            # write all nine
    python tools/make_art.py --check    # also write contact + silhouette sheets
"""
import argparse, math, os, sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets", "balls")

SIZE = 512          # export size; ~4.2x the display size, the LANCZOS sweet spot
SS = 2              # supersample factor while drawing
N = SIZE * SS
INK = (18, 18, 26)  # near-black, not pure black: pure black reads as a hole
INK_W = 19          # final outline weight at 512 -- the single style anchor
SAFE = 0.90         # keep all content inside this fraction, the renderer clips

# Shared palette. Deliberately small and reused across characters -- a shared
# palette is half of what makes a set look like a set.
CREAM = (245, 236, 214)
WHITE = (255, 255, 255)
DARK = (58, 48, 46)
SKIN = (226, 176, 130)
GREEN = (126, 196, 82)
LEAF = (86, 158, 64)
BROWN = (150, 102, 58)
WOOD = (198, 146, 88)
GOLD = (245, 198, 62)
PINK = (244, 158, 196)
BLUE = (108, 176, 226)
GREY = (118, 124, 140)
RED = (206, 74, 68)
PURPLE = (160, 126, 212)


def c(v):
    """Fraction of the canvas -> pixels. Lets every character be written in
    0..1 space so the export size can change without touching the drawings."""
    return v * N


def new_layer():
    return Image.new("RGBA", (N, N), (0, 0, 0, 0))


def blob(d, cx, cy, w, h, fill):
    d.ellipse([c(cx - w / 2), c(cy - h / 2), c(cx + w / 2), c(cy + h / 2)], fill=fill)


def box(d, cx, cy, w, h, fill, r=0.0):
    xy = [c(cx - w / 2), c(cy - h / 2), c(cx + w / 2), c(cy + h / 2)]
    if r:
        d.rounded_rectangle(xy, radius=c(r), fill=fill)
    else:
        d.rectangle(xy, fill=fill)


def poly(d, pts, fill):
    d.polygon([(c(x), c(y)) for x, y in pts], fill=fill)


def limb(d, x0, y0, x1, y1, w, fill):
    d.line([c(x0), c(y0), c(x1), c(y1)], fill=fill, width=int(c(w)))
    blob(d, x0, y0, w, w, fill)
    blob(d, x1, y1, w, w, fill)


def eyes(d, cx, cy, spread, r, look=0.0):
    """Big geometric eyes. Anything subtler than this vanishes at 121px."""
    for s in (-1, 1):
        ex = cx + s * spread / 2
        blob(d, ex, cy, r * 2, r * 2, WHITE)
        blob(d, ex + look * r * 0.5, cy, r * 0.95, r * 0.95, INK)


# ------------------------------------------------------------------ characters
# Each is 4-6 shapes. The comment on each says what the SILHOUETTE is, because
# that is what actually identifies the character at display size.

def tralalero(d):
    """Shark: huge dorsal fin, pointed snout left, swept tail right.

    The first pass failed the silhouette test outright -- it read as a plain
    blob. Fin and snout now break the outline hard in two directions.
    """
    poly(d, [(0.34, 0.46), (0.52, 0.10), (0.66, 0.48)], BLUE)          # dorsal fin
    poly(d, [(0.72, 0.42), (0.94, 0.30), (0.88, 0.70), (0.70, 0.60)], BLUE)  # tail
    blob(d, 0.50, 0.54, 0.60, 0.34, BLUE)                              # body
    poly(d, [(0.10, 0.58), (0.42, 0.44), (0.42, 0.70)], BLUE)          # snout
    poly(d, [(0.18, 0.58), (0.40, 0.52), (0.40, 0.66)], WHITE)         # teeth
    box(d, 0.52, 0.80, 0.24, 0.11, WHITE, r=0.05)                      # sneaker
    eyes(d, 0.48, 0.48, 0.18, 0.050, look=-0.6)


def bombardiro(d):
    """Croc bomber: wide horizontal wings, long snout. Wings dominate."""
    poly(d, [(0.10, 0.52), (0.50, 0.44), (0.90, 0.52), (0.50, 0.58)], GREY)
    blob(d, 0.50, 0.50, 0.40, 0.34, LEAF)                              # body
    poly(d, [(0.30, 0.58), (0.62, 0.52), (0.62, 0.68)], LEAF)          # snout
    poly(d, [(0.34, 0.60), (0.60, 0.56), (0.60, 0.64)], WHITE)         # teeth
    blob(d, 0.50, 0.76, 0.16, 0.16, DARK)                              # bomb
    eyes(d, 0.52, 0.42, 0.20, 0.050)


def tungtung(d):
    """Wooden club: tall narrow body, bat held out. A vertical bar + bat."""
    box(d, 0.44, 0.52, 0.30, 0.62, WOOD, r=0.10)                       # body
    limb(d, 0.60, 0.42, 0.80, 0.28, 0.055, WOOD)                       # arm
    box(d, 0.82, 0.26, 0.10, 0.26, BROWN, r=0.05)                      # bat
    limb(d, 0.38, 0.84, 0.34, 0.92, 0.05, DARK)                        # legs
    limb(d, 0.50, 0.84, 0.54, 0.92, 0.05, DARK)
    eyes(d, 0.44, 0.44, 0.17, 0.050)


def ballerina(d):
    """Round cup head on a wide tutu wedge. Circle-on-triangle, arms DOWN.

    Arms were raised in the first pass, which made her silhouette a near
    duplicate of brrbrr. The tutu is now the whole lower half and the arms
    stay inside it.
    """
    poly(d, [(0.10, 0.90), (0.50, 0.52), (0.90, 0.90)], PINK)          # tutu
    box(d, 0.50, 0.58, 0.15, 0.18, CREAM, r=0.04)                      # torso
    limb(d, 0.44, 0.56, 0.30, 0.72, 0.040, CREAM)                      # arms down
    limb(d, 0.56, 0.56, 0.70, 0.72, 0.040, CREAM)
    blob(d, 0.50, 0.32, 0.44, 0.42, CREAM)                             # cup head
    blob(d, 0.50, 0.21, 0.36, 0.14, BROWN)                             # crema
    eyes(d, 0.50, 0.36, 0.19, 0.050)


def boneca(d):
    """Frog head sat in a fat tire. The wide dark ring is the whole read."""
    blob(d, 0.50, 0.68, 0.72, 0.48, DARK)                              # tire
    blob(d, 0.50, 0.68, 0.40, 0.24, (32, 32, 40))                      # hub
    # LEAF not GREEN: his ball is lime, and green-on-lime had no contrast.
    blob(d, 0.50, 0.38, 0.46, 0.34, LEAF)                              # frog head
    blob(d, 0.36, 0.24, 0.20, 0.20, LEAF)                              # eye bumps
    blob(d, 0.64, 0.24, 0.20, 0.20, LEAF)
    eyes(d, 0.50, 0.24, 0.28, 0.070)


def cappuccino(d):
    """Angular cup + crossed blades. Hard trapezoid with an X through it."""
    # Dark cup, cream foam: his ball is cream, so a cream cup disappeared.
    poly(d, [(0.16, 0.24), (0.84, 0.24), (0.68, 0.84), (0.32, 0.84)], DARK)
    poly(d, [(0.18, 0.26), (0.82, 0.26), (0.79, 0.38), (0.21, 0.38)], CREAM)
    limb(d, 0.16, 0.74, 0.50, 0.44, 0.042, GREY)                       # blades
    limb(d, 0.84, 0.74, 0.50, 0.44, 0.042, GREY)
    eyes(d, 0.50, 0.56, 0.22, 0.055)


def brrbrr(d):
    """Trunk with two forked branch arms and a long nose. Branching shape."""
    # Asymmetric on purpose: symmetric raised arms made this read as
    # ballerina in silhouette. One branch high and forked, one low and short.
    box(d, 0.52, 0.66, 0.26, 0.48, BROWN, r=0.06)                      # trunk
    limb(d, 0.42, 0.50, 0.18, 0.26, 0.042, BROWN)                      # high branch
    limb(d, 0.18, 0.26, 0.26, 0.12, 0.030, BROWN)                      # fork
    limb(d, 0.18, 0.26, 0.06, 0.16, 0.030, BROWN)
    limb(d, 0.64, 0.62, 0.88, 0.56, 0.038, BROWN)                      # low branch
    blob(d, 0.50, 0.34, 0.40, 0.30, SKIN)                              # face
    poly(d, [(0.50, 0.34), (0.66, 0.44), (0.50, 0.46)], (196, 142, 100))  # nose
    limb(d, 0.42, 0.88, 0.36, 0.95, 0.045, BROWN)                      # roots
    limb(d, 0.58, 0.88, 0.64, 0.95, 0.045, BROWN)
    eyes(d, 0.50, 0.30, 0.19, 0.050)


def chimpanzini(d):
    """Monkey in a banana: a fat crescent. The curve is the identity."""
    # Pale, not GOLD: his ball is #FFC61E, and gold-on-gold vanished.
    poly(d, [(0.16, 0.22), (0.30, 0.16), (0.84, 0.66), (0.78, 0.84),
             (0.60, 0.86), (0.18, 0.40)], (255, 234, 158))             # banana
    blob(d, 0.46, 0.50, 0.34, 0.32, SKIN)                              # face
    blob(d, 0.30, 0.44, 0.13, 0.13, SKIN)                              # ears
    blob(d, 0.62, 0.56, 0.13, 0.13, SKIN)
    box(d, 0.20, 0.17, 0.09, 0.09, DARK, r=0.03)                       # stem
    eyes(d, 0.46, 0.48, 0.17, 0.048)


def lirili(d):
    """Cactus with two upright arms and a clock face. Candelabra shape."""
    box(d, 0.50, 0.62, 0.28, 0.56, LEAF, r=0.12)                       # trunk
    box(d, 0.24, 0.56, 0.13, 0.34, LEAF, r=0.06)                       # arms
    box(d, 0.76, 0.56, 0.13, 0.34, LEAF, r=0.06)
    box(d, 0.24, 0.44, 0.13, 0.16, LEAF, r=0.06)
    box(d, 0.76, 0.44, 0.13, 0.16, LEAF, r=0.06)
    blob(d, 0.50, 0.56, 0.22, 0.22, CREAM)                             # clock
    limb(d, 0.50, 0.56, 0.50, 0.48, 0.016, INK)
    limb(d, 0.50, 0.56, 0.57, 0.59, 0.016, INK)
    eyes(d, 0.50, 0.30, 0.17, 0.046)


CHARACTERS = {
    "tralalero": tralalero, "bombardiro": bombardiro, "tungtung": tungtung,
    "ballerina": ballerina, "boneca": boneca, "cappuccino": cappuccino,
    "brrbrr": brrbrr, "chimpanzini": chimpanzini, "lirili": lirili,
}


# --------------------------------------------------------------------- build
def add_outline(layer, weight_px):
    """One uniform ink outline for every character, applied globally.

    Dilating the alpha mask and compositing ink underneath means no character
    function has to draw its own outline, so the line weight cannot drift
    between characters -- which is exactly how nine drawings stop looking like
    one set.
    """
    r = max(1, int(round(weight_px * SS)))
    a = layer.split()[3]
    grown = a
    step = 4                                  # MaxFilter(9) grows ~4px a pass
    for _ in range(max(1, r // step)):
        grown = grown.filter(ImageFilter.MaxFilter(9))
    ink = Image.new("RGBA", layer.size, INK + (0,))
    ink.putalpha(grown)
    return Image.alpha_composite(ink, layer)


def clip_safe(layer):
    """Keep everything inside the circle the renderer will clip to."""
    mask = Image.new("L", layer.size, 0)
    m = int(N * (1 - SAFE) / 2)
    ImageDraw.Draw(mask).ellipse([m, m, N - m, N - m], fill=255)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(layer, (0, 0), mask)
    return out


def build(cid):
    layer = new_layer()
    CHARACTERS[cid](ImageDraw.Draw(layer))
    layer = add_outline(layer, INK_W)
    layer = clip_safe(layer)
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


def sheet(images, cell, silhouette=False):
    cols = 3
    rows = (len(images) + cols - 1) // cols
    out = Image.new("RGB", (cols * cell, rows * cell), (20, 22, 36))
    for k, (cid, im) in enumerate(images):
        thumb = im.resize((cell - 16, cell - 16), Image.LANCZOS)
        if silhouette:
            black = Image.new("RGBA", thumb.size, (0, 0, 0, 255))
            black.putalpha(thumb.split()[3])
            flat = Image.new("RGB", thumb.size, (255, 255, 255))
            flat.paste(black, (0, 0), black)
            thumb = flat
        out.paste(thumb, ((k % cols) * cell + 8, (k // cols) * cell + 8),
                  None if silhouette else thumb)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="also write contact + silhouette sheets to out/")
    ap.add_argument("--only", default=None, help="build one character")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    ids = [args.only] if args.only else list(CHARACTERS)
    if args.only and args.only not in CHARACTERS:
        sys.exit(f"unknown id {args.only!r}; have {', '.join(CHARACTERS)}")

    built = []
    for cid in ids:
        im = build(cid)
        im.save(os.path.join(OUT_DIR, cid + ".png"))
        built.append((cid, im))
        print(f"  wrote assets/balls/{cid}.png")

    if args.check:
        out = os.path.join(ROOT, "out")
        os.makedirs(out, exist_ok=True)
        # 132px cells: close to the ~121px the art actually renders at, so the
        # sheet is an honest legibility test rather than a flattering one
        sheet(built, 132).save(os.path.join(out, "art_contact.png"))
        sheet(built, 132, silhouette=True).save(os.path.join(out, "art_silhouette.png"))
        print("  wrote out/art_contact.png and out/art_silhouette.png")


if __name__ == "__main__":
    main()
