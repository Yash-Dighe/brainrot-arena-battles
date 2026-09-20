"""
Step 4 of the pipeline: SFX, synthesized from the same event log.

Everything here is generated from scratch with the standard library -- no
samples, no loops, no downloaded audio. That is deliberate: royalty-free is a
hard constraint, and "we synthesized it" is the only claim that is
unambiguously safe.

The important design choice is that hits are TONAL, not just pitched thumps.
The genre this format comes from retains people by revealing a melody as
intensity rises -- bounce sounds that climb a scale and resolve. A rising
pitch with no tonal target gives the ear nothing to anticipate; a scale that
climbs and lands does. Consecutive hits walk up a pentatonic scale and resolve
at the octave, so a flurry builds a phrase.

The genre convention is to use a FAMOUS melody for this. We don't: that would
break the royalty-free constraint and is the most likely way this project gets
flagged. An original motif gets the same anticipation-and-resolution effect
with no exposure.

    python sim/audio.py out/fight.json                 # -> out/fight.wav
    python sim/audio.py out/fight.json --mux           # + mux into the mp4

Reads the renderer's *_timeline.json when present, because the cold open means
video time is no longer sim time.
"""
import argparse, array, json, math, os, shutil, subprocess, sys, wave

SR = 44100
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Minor pentatonic, in semitones, spanning two octaves. Minor because the
# format wants menace rather than cheer, pentatonic because every subset of it
# sounds intentional -- which matters when the rhythm is decided by physics.
SCALE = [0, 3, 5, 7, 10, 12, 15, 17, 19, 22, 24]
ROOT_HZ = 196.0          # G3
PHRASE = 7               # hits before the motif resets to the bottom

MASTER = 0.82            # headroom before the limiter


def hz(semitones):
    return ROOT_HZ * (2.0 ** (semitones / 12.0))


class Track:
    """A mono float buffer we mix into, rendered to 16-bit at the end."""

    def __init__(self, seconds):
        self.n = int(seconds * SR) + SR
        self.buf = [0.0] * self.n

    def add(self, t, samples, gain=1.0):
        i = int(t * SR)
        if i < 0:
            samples = samples[-i:]
            i = 0
        b, n = self.buf, self.n
        for k, v in enumerate(samples):
            j = i + k
            if j >= n:
                break
            b[j] += v * gain

    def render(self, path):
        # soft limiter: tanh rather than a hard clip, so stacked hits compress
        # instead of crackling
        out = array.array("h", bytes(2 * self.n))
        peak = max(1e-9, max(abs(v) for v in self.buf))
        norm = MASTER / max(1.0, peak)
        for i, v in enumerate(self.buf):
            out[i] = int(32767 * math.tanh(v * norm * 1.25))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(out.tobytes())


# ------------------------------------------------------------------ voices
def pluck(freq, dur, bright=0.5):
    """Struck-bar tone: fundamental plus a couple of partials, fast decay.

    Reads as percussive rather than musical on its own, but because the pitch
    is a scale degree, a sequence of them reads as a phrase.
    """
    n = int(dur * SR)
    out = [0.0] * n
    w0 = 2 * math.pi * freq / SR
    for i in range(n):
        e = math.exp(-4.5 * i / n)
        s = math.sin(w0 * i)
        s += bright * 0.45 * math.sin(2 * w0 * i) * math.exp(-9.0 * i / n)
        s += bright * 0.25 * math.sin(3.01 * w0 * i) * math.exp(-15.0 * i / n)
        out[i] = s * e
    return out


def thump(freq, dur):
    """Low body for a heavy hit: a sine that drops in pitch as it decays."""
    n = int(dur * SR)
    out, ph = [0.0] * n, 0.0
    for i in range(n):
        f = freq * (1.0 - 0.45 * i / n)
        ph += 2 * math.pi * f / SR
        out[i] = math.sin(ph) * math.exp(-5.0 * i / n)
    return out


def noise(dur, decay=8.0, lp=0.35):
    """Lowpassed pseudo-noise for explosions. LCG so it stays deterministic."""
    n = int(dur * SR)
    out, state, y = [0.0] * n, 0x2545F491, 0.0
    for i in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        x = (state / 0x3FFFFFFF) - 1.0
        y += lp * (x - y)
        out[i] = y * math.exp(-decay * i / n)
    return out


def tick(dur=0.035):
    """Wall bounce. Quiet and short -- there are hundreds of these."""
    n = int(dur * SR)
    return [math.sin(2 * math.pi * 900 * i / SR) * math.exp(-40.0 * i / n)
            for i in range(n)]


def blip(dur=0.22):
    """Banana slip: a comedy upward bend."""
    n = int(dur * SR)
    out, ph = [0.0] * n, 0.0
    for i in range(n):
        f = 420 + 520 * (i / n) ** 2
        ph += 2 * math.pi * f / SR
        out[i] = math.sin(ph) * math.exp(-5.0 * i / n) * 0.5
    return out


def drone(seconds, f0=49.0):
    """Sub that swells over the round. This is what makes the fight feel like
    it is going somewhere during the stretches with no hits."""
    n = int(seconds * SR)
    out, ph = [0.0] * n, 0.0
    for i in range(n):
        p = i / n
        f = f0 * (1.0 + 0.5 * p)
        ph += 2 * math.pi * f / SR
        swell = 0.05 + 0.30 * p ** 2
        out[i] = (math.sin(ph) + 0.3 * math.sin(2 * ph)) * swell
    return out


# ------------------------------------------------------------------- build
def build(log, offset=0.0):
    events = log["events"]
    dmgs = [e["dmg"] for e in events if "dmg" in e] or [1]
    hi = math.log1p(max(dmgs))
    end = log["meta"]["fight_seconds"] + offset + 3.0
    tr = Track(end)

    tr.add(offset, drone(log["meta"]["fight_seconds"]), 0.9)

    step = 0
    for e in events:
        t = e["t"] + offset
        kind = e["type"]
        if kind in ("hit", "combo_hit", "bomb_hit"):
            rel = math.log1p(e["dmg"]) / hi
            deg = SCALE[min(step % PHRASE, len(SCALE) - 1)]
            # each completed phrase starts an octave conversation higher, so
            # the whole fight climbs, not just each burst
            oct_up = 12 * min(step // (PHRASE * 4), 2)
            f = hz(deg + oct_up)
            dur = 0.16 + 0.5 * rel
            tr.add(t, pluck(f, dur, bright=0.35 + 0.6 * rel), 0.28 + 0.5 * rel)
            if rel > 0.45:
                tr.add(t, thump(90 + 60 * rel, 0.22 + 0.3 * rel), 0.35 * rel)
            step += 1
        elif kind == "wall":
            tr.add(t, tick(), 0.05)
        elif kind == "explosion":
            tr.add(t, noise(0.55, decay=7.0), 0.30)
            tr.add(t, thump(70, 0.45), 0.30)
        elif kind == "slip":
            tr.add(t, blip(), 0.22)
        elif kind == "ko":
            tr.add(t, noise(1.2, decay=3.0, lp=0.22), 0.45)
            tr.add(t, thump(60, 1.4), 0.55)
            # resolve the motif on the root, two octaves down: the phrase the
            # whole fight has been climbing finally lands
            tr.add(t + 0.04, pluck(hz(0), 1.6, bright=0.8), 0.5)
    return tr


def mux(video, wav, out):
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("ffmpeg not found; set $FFMPEG or put it on PATH")
    # Normalize on the way in. The raw synth master sits near -32 LUFS with
    # an 18 LU range; platforms target about -14 and will otherwise gain it up
    # themselves, dragging the quiet parts and the noise floor with it. LRA 11
    # also tightens the dynamics for phone speakers, where the quiet stretches
    # would otherwise be inaudible.
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video,
           "-i", wav, "-c:v", "copy",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
           # loudnorm runs at 192k internally and will otherwise leave the
           # output at 96kHz; pin delivery to 48k stereo-safe AAC.
           "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="event log json from sim/engine.py")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mux", action="store_true",
                    help="mux the wav into the rendered mp4")
    args = ap.parse_args()

    log = json.load(open(args.log))
    base = os.path.splitext(args.log)[0]

    offset = 0.0
    tl_path = base + "_timeline.json"
    if os.path.exists(tl_path):
        offset = json.load(open(tl_path))["fight_offset_sec"]
        print(f"  timeline found: fight starts {offset:.2f}s into the video")
    else:
        print("  no timeline sidecar; assuming video time == sim time")

    out = args.out or base + ".wav"
    build(log, offset).render(out)
    print(f"  wrote {out}")

    if args.mux:
        video = base + ".mp4"
        if not os.path.exists(video):
            sys.exit(f"no video at {video}; run sim/render.py first")
        final = base + "_av.mp4"
        mux(video, out, final)
        print(f"  wrote {final}")


if __name__ == "__main__":
    main()
