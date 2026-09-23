#!/usr/bin/env python3
"""A background bed, synthesised here, so the channel owns it outright.

    python3 music.py bed --seconds 60 -o bed.wav --mood grief

Nothing under these videos may be licensed footage's soundtrack or a track
lifted from a library the channel has not paid for: a Content ID claim on the
audio takes the revenue off a video whose pictures are already CC-BY, and the
whole point of the footage rules is not to be in that position. So the bed is
generated from scratch -- a drone, a slow pad and a little air -- and its
licence is "original composition", the same phrase the narration carries.

It is meant to sit under a voice, not to be listened to. Nothing percussive,
nothing with a melody to follow, and everything above the vocal range rolled
off so it makes room rather than competing.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SAMPLE_RATE = 48000

# Semitone offsets from the root. Minor for anything with a loss in it, dorian
# for something with more air, and a suspended fifth when the picture should
# carry the mood on its own.
MOODS = {
    "grief":  {"scale": (0, 3, 7, 10, 12, 15), "root": 55.0, "swell": 11.0},
    "wonder": {"scale": (0, 2, 7, 9, 12, 14), "root": 65.4, "swell": 9.0},
    "tension": {"scale": (0, 1, 7, 8, 12, 13), "root": 49.0, "swell": 7.0},
    "still":  {"scale": (0, 7, 12, 19), "root": 58.3, "swell": 14.0},
}


class MusicError(RuntimeError):
    """The bed could not be made."""


def _hz(root, semitones):
    return root * (2.0 ** (semitones / 12.0))


def bed(seconds, mood="grief", seed=7):
    """A mono float array: drone, pad swells and a little air.

    Where the energy sits matters more than what the notes are. A first pass
    put 94% of it below 300 Hz, which is mud on a laptop and silence on a
    phone -- most phone speakers give up somewhere around 200 Hz, so a bed
    written down there is a bed nobody hears. The pad therefore sits in the
    200-700 Hz range where a small speaker still works, the drone underneath is
    quiet enough not to smear it, and the 1-4 kHz band where speech is actually
    made out is deliberately left thin.
    """
    import numpy as np
    if seconds <= 0:
        raise MusicError("a bed needs a length above zero")
    if mood not in MOODS:
        raise MusicError("no such mood: %r. Try %s"
                         % (mood, ", ".join(sorted(MOODS))))
    spec = MOODS[mood]
    rng = np.random.default_rng(seed)
    n = int(round(seconds * SAMPLE_RATE))
    t = np.arange(n) / float(SAMPLE_RATE)
    out = np.zeros(n)

    # 1. the drone, kept quiet: it is there to be felt, not heard
    for semis, level in ((0, 0.22), (7, 0.14), (12, 0.10)):
        base = _hz(spec["root"], semis)
        for detune in (-0.12, 0.12):
            out += level * np.sin(2 * np.pi * (base + detune) * t)

    # 2. the pad, two octaves up so its notes land where a phone can play them.
    #    One at a time, each fading in and out over a long cycle, so the
    #    harmony moves without giving the ear a tune to follow.
    swell = float(spec["swell"])
    for k, semis in enumerate(spec["scale"]):
        phase = (k / float(len(spec["scale"]))) * swell
        env = 0.5 - 0.5 * np.cos(2 * np.pi * (t + phase) / swell)
        env = env ** 3                        # long silences, short blooms
        freq = _hz(spec["root"] * 4, semis)
        out += 0.26 * env * (np.sin(2 * np.pi * freq * t)
                             + 0.22 * np.sin(2 * np.pi * freq * 3 * t))

    # 3. air, so it is not purely synthetic
    noise = rng.standard_normal(n)
    out += 0.010 * _onepole(_onepole(noise, 2400.0), 300.0, low=False)

    # 4. shape it around the voice: nothing subsonic, nothing shrill, and a dip
    #    through the band that carries consonants
    out = _onepole(out, 5200.0)
    out = _onepole(out, 45.0, low=False)
    carve = _onepole(_onepole(out, 3600.0), 1200.0, low=False)
    # measured on the finished bed: 31% of the energy under 150 Hz, 25% in
    # 150-400 where a phone speaker still works, and under 10% across 1-4 kHz,
    # which is the band consonants are made out in
    out = out - 0.70 * carve

    # 5. ease in and out so it does not start or stop with a click
    edge = min(int(1.5 * SAMPLE_RATE), n // 6)
    if edge > 1:
        ramp = np.linspace(0.0, 1.0, edge) ** 2
        out[:edge] *= ramp
        out[-edge:] *= ramp[::-1]

    peak = float(np.max(np.abs(out))) or 1.0
    return out / peak * 0.85


def _onepole(signal, cutoff, low=True):
    """A gentle 6 dB/octave filter. Not a brick wall -- the point is to make room.

    Done as a convolution with the filter's own impulse response rather than as
    the obvious recurrence. There is no scipy here; the recurrence written as a
    Python loop took most of a minute for a one-minute bed, and the cumulative
    -product trick that replaces it underflows -- at a 5 kHz cutoff the decay
    per sample is about 0.5, so a few thousand samples in, the rescaling factor
    is zero and every sample after it is a division by it. The impulse response
    is (1-a)*a^n, which is short enough to truncate and fast enough to apply by
    FFT at any cutoff.
    """
    import numpy as np
    x = np.asarray(signal, dtype=np.float64)
    a = math.exp(-2.0 * math.pi * float(cutoff) / SAMPLE_RATE)
    if a <= 0.0 or a >= 1.0:
        return x if low else np.zeros_like(x)
    taps = int(min(len(x), max(4, math.ceil(math.log(1e-6) / math.log(a)))))
    h = (1.0 - a) * (a ** np.arange(taps))
    size = 1 << int(len(x) + taps - 1).bit_length()
    out = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size),
                       size)[:len(x)]
    return out if low else x - out


def write(path, samples):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    return path


def make(seconds, out_path, mood="grief", seed=7):
    return write(out_path, bed(seconds, mood, seed))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="music.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_bed = sub.add_parser("bed", help="write a background bed")
    p_bed.add_argument("--seconds", type=float, required=True)
    p_bed.add_argument("-o", "--out", required=True)
    p_bed.add_argument("--mood", default="grief", choices=sorted(MOODS))
    p_bed.add_argument("--seed", type=int, default=7)
    sub.add_parser("moods", help="list the moods")
    args = parser.parse_args(argv)
    try:
        if args.command == "moods":
            for name, spec in sorted(MOODS.items()):
                print("  %-8s root %.1f Hz, %d notes, %.0fs swell"
                      % (name, spec["root"], len(spec["scale"]), spec["swell"]))
            return 0
        made = make(args.seconds, args.out, args.mood, args.seed)
        print("wrote %s — %.1fs of %s" % (made, args.seconds, args.mood))
        return 0
    except MusicError as exc:
        print("music.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
