"""
Ring escape: audio, synthesized from the event log with the stdlib only.

Every bounce plays the NEXT note of a melody, so the run slowly reveals a
tune -- that is the genre's hook. The melody is our own (an arpeggiated
I-IV-V-I in C major), not a famous one: royalty-free is a hard constraint.
A ring breaking plays a bright chime on top; the final ring plays a chord.

    python escape/audio.py out/escape_197.json          # -> out/escape_197.wav
    python escape/audio.py out/escape_197.json --mux    # + mux into the mp4
"""
import argparse, array, json, math, os, shutil, subprocess, sys, wave

SR = 44100
ROOT_HZ = 261.63          # C4
MASTER = 0.8

# semitones above C4. Two 16-note phrases; the second answers the first and
# lands on the tonic so a full loop resolves.
MELODY = [
    0, 4, 7, 12, 11, 7, 4, 7,     5, 9, 12, 9, 7, 4, 2, 4,
    0, 4, 7, 12, 14, 12, 11, 7,   9, 7, 5, 4, 2, 7, -1, 0,
]
FINAL_CHORD = [0, 4, 7, 12, 16, 19, 24]


def hz(semi):
    return ROOT_HZ * 2.0 ** (semi / 12.0)


class Track:
    def __init__(self, seconds):
        self.n = int(seconds * SR) + SR
        self.buf = [0.0] * self.n

    def add(self, t, samples, gain=1.0):
        i = int(t * SR)
        b, n = self.buf, self.n
        for k, v in enumerate(samples):
            j = i + k
            if j >= n:
                break
            b[j] += v * gain

    def render(self, path):
        peak = max(1e-9, max(abs(v) for v in self.buf))
        norm = MASTER / max(1.0, peak)
        out = array.array("h", bytes(2 * self.n))
        for i, v in enumerate(self.buf):
            out[i] = int(32767 * math.tanh(v * norm * 1.2))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(out.tobytes())


def bell(freq, dur, bright=0.5):
    """Struck-bar tone: fundamental plus quickly-dying partials."""
    n = int(dur * SR)
    w0 = 2 * math.pi * freq / SR
    out = [0.0] * n
    for i in range(n):
        e = math.exp(-5.0 * i / n)
        s = math.sin(w0 * i)
        s += bright * 0.5 * math.sin(2 * w0 * i) * math.exp(-10.0 * i / n)
        s += bright * 0.25 * math.sin(3.01 * w0 * i) * math.exp(-16.0 * i / n)
        out[i] = s * e * min(1.0, i / 40)       # 1ms attack: no click
    return out


def shimmer(dur=0.5):
    """Soft highpassed noise swell for a ring breaking. LCG: deterministic."""
    n = int(dur * SR)
    out, state, prev = [0.0] * n, 0x2545F491, 0.0
    for i in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        x = state / 0x3FFFFFFF - 1.0
        out[i] = (x - prev) * math.exp(-7.0 * i / n) * 0.5
        prev = x
    return out


def build(log):
    m = log["meta"]
    tr = Track(m["video_seconds"])
    _cache = {}

    def note(semi, dur, bright):
        k = (semi, dur, bright)
        if k not in _cache:
            _cache[k] = bell(hz(semi), dur, bright)
        return _cache[k]

    nring = len(m["rings"])
    step = 0
    for e in log["events"]:
        if e["type"] == "bounce":
            semi = MELODY[step % len(MELODY)]
            step += 1
            # soft taps are quieter, but never inaudible
            g = 0.35 + 0.45 * min(1.0, e["speed"] / 1400)
            tr.add(e["t"], note(semi, 0.6, 0.5), g)
        elif e["type"] == "escape":
            last = e["ring"] == nring - 1
            if last:
                for k, s in enumerate(FINAL_CHORD):
                    tr.add(e["t"] + 0.03 * k, note(s, 2.4, 0.4), 0.32)
            else:
                # rising with each ring broken: progress you can hear
                base = [12, 14, 16, 19, 21, 24, 26, 28][e["ring"] % 8]
                tr.add(e["t"], note(base, 0.9, 0.7), 0.5)
                tr.add(e["t"] + 0.06, note(base + 7, 0.8, 0.5), 0.35)
            tr.add(e["t"], shimmer(), 0.35)
    return tr


def mux(video, wav, out):
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("ffmpeg not found; set $FFMPEG or put it on PATH")
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", video, "-i", wav, "-c:v", "copy",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", "-movflags", "+faststart", out], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mux", action="store_true", help="also mux into <log>.mp4")
    args = ap.parse_args()
    with open(args.log) as f:
        log = json.load(f)
    base = os.path.splitext(args.log)[0]
    wav = args.out or base + ".wav"
    build(log).render(wav)
    print(f"  wrote {wav}")
    if args.mux:
        out = base + "_av.mp4"
        mux(base + ".mp4", wav, out)
        print(f"  wrote {out}")


if __name__ == "__main__":
    main()
