#!/usr/bin/env python3
"""Narrate a script, and cut the video to the voice.

The point of a voiceover here is not decoration: in a commentary video the
narration *is* the original contribution. The clips illustrate what is being
said; the saying is the work.

So this does the thing most pipelines get backwards. It synthesises each beat,
measures how long that beat actually takes to say, and rewrites the render
plan's beat durations to match. The video is cut to the voice rather than the
voice being squeezed into arbitrary durations — which also means the burned-in
captions land with the words instead of near them.

    python3 voice.py engines                        # what is available here
    python3 voice.py speak script.md                # one wav per beat -> voice/
    python3 voice.py fit render.json                # beats <- spoken lengths
    python3 voice.py track render.json -o voice.wav # one track, gapped to fit
    python3 voice.py narrate script.md render.json  # all three, in order

Engines, in order of preference. Any one is enough:

* **piper** — best quality, offline. ``pip install piper-tts`` then download a
  voice (``.onnx`` + ``.onnx.json``) and set ``PIPER_VOICE`` to it.
* **espeak-ng** — robotic but everywhere. ``apt install espeak-ng`` /
  ``brew install espeak-ng``.
* **say** — built into macOS, decent.
* **recorded** — a directory of ``beat01.wav`` … files you recorded yourself.
  Your own voice beats any of the above, and for commentary it is worth it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

__all__ = ["beats_from_script", "speak", "fit_plan", "build_track", "attach",
           "utterances", "beat_lengths", "delivery_from_script",
           "measure_words", "annotate_plan", "trim_silence", "kokoro_paths",
           "available_engines", "usable_engines", "VoiceError"]

HERE = Path(__file__).resolve().parent
PAD_SECONDS = 0.09          # fallback gap after a line; the style sets the real one
INSIDE_FLOOR = 0.55         # shortest a beat may be inside a continuous sentence
SAMPLE_RATE = 24000


class VoiceError(RuntimeError):
    """The narration could not be produced."""


def _ffmpeg():
    sys.path.insert(0, str(HERE))
    import render as render_mod
    return render_mod.ffmpeg_bin()


def _duration(path):
    sys.path.insert(0, str(HERE))
    import render as render_mod
    return render_mod.probe(path)["duration"]


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Kokoro: a neural voice that runs offline
# --------------------------------------------------------------------------
KOKORO_SR = 24000
_KOKORO_CACHE = {}


def kokoro_paths():
    """(model, voices_dir) for the local Kokoro weights, or (None, None).

    Set KOKORO_MODEL to the .onnx and KOKORO_VOICES to the directory of
    per-voice .bin files. The weights are ~92MB and are not in this repo --
    they are a download, like a piper voice, not source.
    """
    model = os.environ.get("KOKORO_MODEL", "").strip()
    voices = os.environ.get("KOKORO_VOICES", "").strip()
    if model and voices and Path(model).is_file() and Path(voices).is_dir():
        return Path(model), Path(voices)
    return None, None


def _kokoro_session(model_path):
    """One session per process: loading 92MB of weights per line is not free."""
    key = str(model_path)
    if key not in _KOKORO_CACHE:
        import onnxruntime                                   # optional dependency
        _KOKORO_CACHE[key] = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"])
    return _KOKORO_CACHE[key]


def _kokoro_vocab(model_path):
    key = "vocab:" + str(model_path)
    if key not in _KOKORO_CACHE:
        tok = Path(model_path).parent / "tokenizer.json"
        if not tok.exists():
            raise VoiceError("Kokoro needs tokenizer.json beside %s" % model_path)
        _KOKORO_CACHE[key] = json.loads(tok.read_text(encoding="utf-8"))["model"]["vocab"]
    return _KOKORO_CACHE[key]


def _ipa(text, lang="en-us"):
    """Text to the IPA Kokoro is conditioned on, via espeak-ng."""
    if not shutil.which("espeak-ng"):
        raise VoiceError("Kokoro needs espeak-ng to turn text into phonemes "
                         "(apt install espeak-ng). It does the phonemising only; "
                         "the voice you hear is Kokoro's.")
    proc = subprocess.run(["espeak-ng", "-q", "--ipa", "-v", lang, text],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise VoiceError("espeak-ng could not phonemise %r: %s"
                         % (text[:40], (proc.stderr or "").strip()[-200:]))
    joined = " ".join(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return re.sub(r"\s+", " ", joined.replace("\u02cc", "")).strip()


def _kokoro(text, out_path, settings):
    """Kokoro text-to-speech, entirely on this machine."""
    model_path, voices_dir = kokoro_paths()
    if not model_path:
        raise VoiceError(
            "Kokoro needs its weights. Set KOKORO_MODEL to the .onnx file and "
            "KOKORO_VOICES to the folder of voice .bin files. They are about "
            "92MB and are a download rather than part of this repo.")
    try:
        import numpy
    except ImportError:
        raise VoiceError("Kokoro needs numpy and onnxruntime: pip install "
                         "numpy onnxruntime")
    name = (settings.get("kokoro_voice") or os.environ.get("KOKORO_VOICE")
            or "af_heart")
    style_file = voices_dir / (name + ".bin")
    if not style_file.exists():
        have = sorted(p.stem for p in voices_dir.glob("[a-z][fm]_*.bin"))[:12]
        raise VoiceError("no Kokoro voice %r in %s. Available include: %s"
                         % (name, voices_dir, ", ".join(have)))

    vocab = _kokoro_vocab(model_path)
    ipa = _ipa(text, settings.get("kokoro_language") or "en-us")
    ids = [0] + [vocab[ch] for ch in ipa if ch in vocab] + [0]
    if len(ids) <= 2:
        raise VoiceError("nothing speakable in %r" % text[:40])

    style = numpy.fromfile(style_file, dtype=numpy.float32).reshape(-1, 256)
    # the style vector is chosen by token count -- Kokoro keeps one per length
    row = min(len(ids), style.shape[0] - 1)
    wave_out = _kokoro_session(model_path).run(None, {
        "input_ids": numpy.array([ids], dtype=numpy.int64),
        "style": style[row].reshape(1, 256).astype(numpy.float32),
        "speed": numpy.array([float(settings.get("kokoro_speed", 1.0))],
                             dtype=numpy.float32),
    })[0][0]

    import wave as wave_mod
    raw = out_path.with_name(out_path.stem + ".kokoro.wav")
    pcm = (numpy.clip(wave_out, -1.0, 1.0) * 32767).astype("<i2")
    with wave_mod.open(str(raw), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(KOKORO_SR)
        handle.writeframes(pcm.tobytes())
    # No _retime here, deliberately. Kokoro has its own speed control, so
    # forcing every line to one words-per-minute afterwards would flatten the
    # pace the model chose for each sentence -- and a narrator who reads every
    # line at exactly the same rate is the clearest tell that nobody is home.
    # voice.kokoro_speed sets the pace; the model varies within it.
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(raw), "-ar", str(SAMPLE_RATE), "-ac", "1",
                    str(out_path)], check=True)
    raw.unlink(missing_ok=True)
    return out_path


ELEVEN_HOST = "https://api.elevenlabs.io"
ELEVEN_MODEL = "eleven_multilingual_v2"
# ElevenLabs' own "Rachel" -- a real default beats making the operator hunt for
# an id before they can hear anything.
ELEVEN_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


def _elevenlabs(text, out_path, settings, opener=None):
    """ElevenLabs text-to-speech. Needs ELEVENLABS_API_KEY.

    By some distance the best-sounding option here, and the only one that costs
    money and needs the network. Everything else in this pipeline runs offline,
    so it is opt-in rather than the default.
    """
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise VoiceError(
            "ElevenLabs needs an API key. Set ELEVENLABS_API_KEY (they are on "
            "elevenlabs.io under your profile). Every other engine here runs "
            "offline and for nothing, so this one is never picked for you.")
    voice_id = (settings.get("elevenlabs_voice_id")
                or os.environ.get("ELEVENLABS_VOICE_ID") or ELEVEN_DEFAULT_VOICE)
    body = json.dumps({
        "text": text,
        "model_id": settings.get("elevenlabs_model") or ELEVEN_MODEL,
        "voice_settings": {
            "stability": float(settings.get("elevenlabs_stability", 0.45)),
            "similarity_boost": float(settings.get("elevenlabs_similarity", 0.75)),
            "style": float(settings.get("elevenlabs_style", 0.0)),
            "use_speaker_boost": True,
        },
    }).encode("utf-8")
    url = "%s/v1/text-to-speech/%s" % (ELEVEN_HOST, voice_id)
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": key, "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    try:
        audio = (opener or _open_url)(request)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code == 401:
            raise VoiceError("ElevenLabs rejected the API key (401). %s" % detail)
        if exc.code == 422:
            raise VoiceError("ElevenLabs rejected the request (422) — usually an "
                             "unknown voice id %r. %s" % (voice_id, detail))
        if exc.code == 429:
            raise VoiceError("ElevenLabs rate-limited or out of quota (429). %s" % detail)
        raise VoiceError("ElevenLabs returned HTTP %d. %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise VoiceError("could not reach ElevenLabs: %s. Every other engine "
                         "here works offline." % exc.reason)
    if len(audio) < 512:
        raise VoiceError("ElevenLabs returned %d bytes — not audio" % len(audio))
    mp3 = Path(out_path).with_suffix(".eleven.mp3")
    mp3.write_bytes(audio)
    # mp3 in, wav out: the rest of the pipeline works in wav
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(mp3), "-ar", str(SAMPLE_RATE), "-ac", "1",
                    str(out_path)], check=True)
    mp3.unlink(missing_ok=True)
    return out_path


def _open_url(request, timeout=60):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def available_engines():
    """Every engine this machine knows about, best first, as (name, note, usable).

    `usable` is carried explicitly rather than inferred from the name, because
    "installed" and "able to speak" are different states: piper with no voice
    model is present and useless, and a caller that cannot tell them apart
    reports a green check and then fails at the first line.
    """
    found = []
    voice = os.environ.get("PIPER_VOICE")
    try:
        import piper  # noqa: F401
        if voice and Path(voice).exists():
            found.append(("piper", "offline, best quality (%s)" % Path(voice).name, True))
        else:
            found.append(("piper", "installed, but PIPER_VOICE is not set to a "
                                   ".onnx voice model", False))
    except ImportError:
        pass
    if os.environ.get("ELEVENLABS_API_KEY", "").strip():
        found.insert(0, ("elevenlabs", "best quality; needs the network and costs "
                                       "money per character", True))
    model_path, _voices = kokoro_paths()
    if model_path:
        try:
            import numpy, onnxruntime          # noqa: F401
            runnable = bool(shutil.which("espeak-ng"))
            found.append(("kokoro",
                          "neural, offline, free — the best of these"
                          if runnable else
                          "weights found, but espeak-ng is needed to phonemise",
                          runnable))
        except ImportError:
            found.append(("kokoro", "weights found, but: pip install numpy onnxruntime",
                          False))
    if shutil.which("pico2wave"):
        found.append(("pico2wave", "clear and close to natural, no model to download",
                      True))
    if shutil.which("espeak-ng"):
        mbrola = shutil.which("mbrola") and any(
            Path(d).exists() for d in ("/usr/share/mbrola/en1", "/usr/share/mbrola/us1"))
        found.append(("espeak-ng",
                      "robotic but dependable; set voice.engine_voice to mb-en1 "
                      "for the better MBROLA voice" if mbrola
                      else "robotic but dependable (apt install mbrola mbrola-en1 "
                           "improves it a lot)", True))
    if shutil.which("say"):
        found.append(("say", "built into macOS", True))
    return found


def usable_engines():
    """Just the engines that can actually produce audio right now."""
    return [(name, note) for name, note, ok in available_engines() if ok]


def _voice_style(style=None):
    """The committed voice settings, so every video sounds the same."""
    if style is not None:
        return style.get("voice") or {}
    sys.path.insert(0, str(HERE))
    import style as style_mod
    return style_mod.current().get("voice") or {}


def trim_silence(path, settings=None):
    """Cut the silence off both ends of a spoken line.

    Engines hand back a line with silence around it -- Kokoro adds about a
    third of a second at the front of every one. Left in, and added to the gap
    after each line, a quarter of a Short is nothing: the narration sounds
    halting and every cut lands in a hole. That is what reads as a sloppy edit.

    A few milliseconds are kept at the head so the first consonant is not
    clipped, which is worse than the silence.

    The TAIL keeps more, and that is not symmetry gone wrong. A sentence does
    not stop, it falls off: measured on four of these lines, the decay after
    the last strong sound ran 0.03-0.185s. Trimmed to the head's 25ms, the
    voice cuts out mid-fall and the next sentence begins -- which is heard as
    two sentences run together no matter how much silence is put between them,
    because the first one never finished.
    """
    settings = settings or {}
    path = Path(path)
    floor = float(settings.get("silence_floor_db", -45))
    keep = float(settings.get("keep_head_ms", 25)) / 1000.0
    tail = float(settings.get("keep_tail_ms", 140)) / 1000.0
    before = _duration(path)
    trimmed = path.with_name(path.stem + ".trim.wav")
    chain = ("silenceremove=start_periods=1:start_threshold=%ddB:start_silence=%.3f"
             ":detection=peak,areverse,"
             "silenceremove=start_periods=1:start_threshold=%ddB:start_silence=%.3f"
             ":detection=peak,areverse" % (floor, keep, floor, tail))
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                           "-i", str(path), "-af", chain,
                           "-ar", str(SAMPLE_RATE), "-ac", "1", str(trimmed)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not trimmed.exists() or trimmed.stat().st_size < 256:
        trimmed.unlink(missing_ok=True)
        return before                                   # keep the untrimmed line
    after = _duration(trimmed)
    # a line that trims to almost nothing means the threshold ate the speech
    if after < max(0.12, before * 0.25):
        trimmed.unlink(missing_ok=True)
        print("voice.py: not trimming %s — %.2fs would become %.2fs, which looks "
              "like the threshold catching the speech itself"
              % (path.name, before, after), file=sys.stderr)
        return before
    trimmed.replace(path)
    return after


def _master(path, settings):
    """The treatment every line gets, from the committed style.

    Synthesised speech out of the box is thin, fizzy on the top end and uneven
    line to line. This is the difference between narration that sounds produced
    and narration that sounds pasted on -- and because it comes from the style,
    it is identical in every video. It applies to a recorded voice too, which
    needs the levelling more than a synthesiser does.
    """
    path = Path(path)

    def number(key, cast=float):
        """A setting style.py would have rejected must not lose the line.

        style.py validates these on the way in, so a bad value here means a
        hand-built settings dict or a file edited around the tool. Mastering is
        cosmetic and the narration is not, so a bad value skips its filter.
        """
        raw = settings.get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            return cast(raw)
        except (TypeError, ValueError):
            print("voice.py: ignoring voice.%s=%r — it is not a number"
                  % (key, raw), file=sys.stderr)
            return None

    chain = []
    highpass = number("highpass_hz", int)
    if highpass:
        chain.append("highpass=f=%d" % highpass)
    lowpass = number("lowpass_hz", int)
    if lowpass:
        chain.append("lowpass=f=%d" % lowpass)
    if settings.get("compress"):
        chain.append("acompressor=threshold=-18dB:ratio=3:attack=8:release=140:makeup=2")
    # Levelling is NOT done here. It used to be, with a single-pass loudnorm,
    # and it did not work: measured across 29 takes of one narration the levels
    # ran from -21.5 to -15.9 LUFS, a 5.6 dB spread on lines meant to sound
    # like one person talking. Two reasons, and loudnorm cannot fix either.
    # It is a streaming normaliser that needs seconds to settle, and half these
    # takes are under two of them -- "By her." came back 3 dB under. And it ran
    # after the silence this module writes into a line, which drags the
    # integrated figure down: the correlation between a take's level and how
    # much of it was inserted silence was -0.51. See `_level_together`, which
    # measures the speech and applies one number.
    if not chain:
        return path
    tmp = path.with_name(path.stem + ".mastered.wav")
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                           "-i", str(path), "-filter:a", ",".join(chain),
                           "-ar", str(SAMPLE_RATE), "-ac", "1", str(tmp)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 256:
        # the raw line is still usable, so treat this as cosmetic and say so
        print("voice.py: could not master %s (%s) — using the raw line"
              % (path.name, (proc.stderr or "").strip()[-120:]), file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return path
    tmp.replace(path)
    return path


# A take is levelled by measuring it and applying one gain, not by asking a
# normaliser to converge on something two seconds long. Mean RMS stands in for
# loudness here: measured over a whole narration the two tracked each other to
# within 0.32 dB (sd 0.28), because every take is one speaker through one
# filter chain -- and unlike an integrated LUFS reading, RMS does not need a
# minimum length to mean anything.
RMS_TO_LUFS = 0.3           # measured offset between the two on this material
PEAK_CEILING_DB = -1.5      # what the take may not exceed after the gain
# Generous, because `recorded` takes whatever someone hands it and a quiet
# recording really can arrive this far under. Refusing to bring it up leaves
# it inaudible beneath the music, which is worse than lifting its noise floor
# with it -- and the person can hear the result and record it again. The floor
# below is what stops a gain this large landing on a take with nothing in it.
MAX_GAIN_DB = 40.0
SILENCE_FLOOR_DB = -60.0    # under this there is no speech to level


# How fast Kokoro may be ASKED to speak. Past this it stops compressing a
# phrase evenly and starts squeezing the end of it, which is heard as a line
# that sets off at a sensible pace and then runs out. Measured over five
# phrases, splitting each at its comma and comparing how much each half
# actually shortened against how much was asked for:
#
#     speed   first half   last half   skew
#      1.15      0.93x        0.94x     0.99
#      1.22      0.93x        0.96x     0.96
#      1.28      1.07x        0.87x     1.23    <- the end is squeezed
#      1.40      1.17x        0.99x     1.18
#      1.52      1.13x        0.95x     1.19
#
# Above 1.22 it is not a smooth degradation, it is erratic, which is worse:
# two lines marked the same way come back paced differently.
ARTICULATE_SPEED = 1.22


def _split_speed(settings, want):
    """(settings to synthesise with, tempo to apply after) for a wanted speed.

    Anything past what the engine can say evenly is done afterwards with
    atempo, which compresses the whole line by the same factor and therefore
    cannot squeeze its end. At the same finished speed, measured:

                                    skew   syllable valleys
        engine at 1.368             1.18        5.7 dB
        engine at 1.22 + atempo     0.96        5.6 dB
        engine at 1.512             1.19        3.3 dB
        engine at 1.22 + atempo     0.97        5.4 dB

    The reference, an unhurried 1.0, measures 6.4 dB.
    """
    if "kokoro_speed" not in settings or not settings.get("kokoro_speed"):
        return settings, 1.0
    if want <= ARTICULATE_SPEED + 1e-6:
        return settings, 1.0
    out = dict(settings)
    out["kokoro_speed"] = ARTICULATE_SPEED
    return out, want / ARTICULATE_SPEED


def _stretch(path, tempo):
    """Speed a take up without changing its pitch, evenly across the line."""
    if tempo <= 1.0001:
        return path
    steps, left = [], float(tempo)
    while left > 2.0:                       # atempo's own range, chained
        steps.append(2.0); left /= 2.0
    steps.append(left)
    chain = ",".join("atempo=%.5f" % s for s in steps)
    tmp = Path(path).with_name(Path(path).stem + ".tempo.wav")
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                           "-i", str(path), "-af", chain,
                           "-ar", str(SAMPLE_RATE), "-ac", "1", str(tmp)],
                          capture_output=True, text=True)
    if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 256:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)
        print("voice.py: could not speed %s up by %.2fx — leaving it as spoken"
              % (Path(path).name, tempo), file=sys.stderr)
    return path


def _mean_volume(path):
    """(mean dB, peak dB) for a wav, from ffmpeg's volumedetect, or None."""
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path),
                           "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True)
    mean = re.findall(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
    peak = re.findall(r"max_volume:\s*(-?[\d.]+) dB", proc.stderr)
    if not mean or not peak:
        return None
    return float(mean[-1]), float(peak[-1])


def _level_together(parts, settings):
    """Put these takes on the style's level, with ONE gain across all of them.

    One gain rather than one each, deliberately. The parts of a line are its
    sentences -- "It breathes fire." and "She's faster." -- and levelling them
    apart would lift the quiet half of a deliberate contrast to match the loud
    half, which is the delivery the script asked for being undone by the
    plumbing. Their relative levels are the performance. Their shared level is
    production.

    The measurement happens before any silence is written in, so a line with a
    stop in it is not read as a quiet line.
    """
    target = settings.get("loudness_lufs")
    if target is None or not parts:
        return 0.0
    measured = []
    for part in parts:
        got = _mean_volume(part)
        if got is None:
            return 0.0                      # cannot measure: change nothing
        measured.append((got[0], got[1], _duration(part)))
    span = sum(d for _, _, d in measured) or 1.0
    # energy-weighted, so a long take counts for more than a one-word one
    energy = sum(10.0 ** (m / 10.0) * d for m, _, d in measured) / span
    if energy <= 0:
        return 0.0
    level = 10.0 * math.log10(energy)
    if level < SILENCE_FLOOR_DB:
        print("voice.py: %s measures %.0f dB — nothing to level"
              % (Path(parts[0]).name, level), file=sys.stderr)
        return 0.0
    gain = float(target) - RMS_TO_LUFS - level
    gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain))
    if abs(gain) < 0.05:
        return 0.0
    # The gain goes on and a limiter holds the ceiling, rather than the gain
    # being cut short to protect the peak. Capping it was tried: every take
    # came back 2-3 dB under target, because compressed speech already peaks
    # near full scale and the cap bound before the level ever reached the
    # number the style asks for.
    chain = "volume=%.2fdB,alimiter=limit=%.4f:attack=5:release=50:level=disabled" % (
        gain, 10.0 ** (PEAK_CEILING_DB / 20.0))
    for part in parts:
        tmp = Path(part).with_name(Path(part).stem + ".gain.wav")
        proc = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error",
                               "-y", "-i", str(part), "-af", chain,
                               "-ar", str(SAMPLE_RATE), "-ac", "1", str(tmp)],
                              capture_output=True, text=True)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 256:
            tmp.replace(part)
        else:
            tmp.unlink(missing_ok=True)
            print("voice.py: could not level %s — leaving it as spoken"
                  % Path(part).name, file=sys.stderr)
    return gain


def _pitch(path, semitones):
    """Shift a line's pitch without changing how long it takes to say.

    Resampling alone moves pitch and speed together; putting the speed back
    with atempo leaves the pitch where it was moved to. Pitch is the half of
    delivery that rate cannot reach -- dropping into the bottom of the range
    for a reveal is a thing every narrator does and no flat synthesiser ever
    does on its own.
    """
    path = Path(path)
    step = float(semitones or 0)
    if abs(step) < 0.05:
        return path
    # Past a couple of semitones the formants have moved far enough that it is
    # audibly a different speaker, whatever the delivery asked for.
    step = max(-2.0, min(2.0, step))
    ratio = 2.0 ** (step / 12.0)
    tmp = path.with_name(path.stem + ".pitched.wav")
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
         "-filter:a", "asetrate=%d,aresample=%d,atempo=%.6f"
         % (int(SAMPLE_RATE * ratio), SAMPLE_RATE, 1.0 / ratio),
         "-ar", str(SAMPLE_RATE), "-ac", "1", str(tmp)],
        capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 256:
        # delivery is cosmetic and the line is not
        print("voice.py: could not pitch %s (%s) — using it as spoken"
              % (path.name, (proc.stderr or "").strip()[-120:]), file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return path
    tmp.replace(path)
    return path


def _retime(raw, out_path, text, settings):
    """Speak at the style's rate even when the engine has no rate control.

    Measured rather than assumed: count the words, see how long the engine
    actually took, and nudge. This is what makes voice.words_per_minute mean
    the same thing on every engine, which is the point of committing it to the
    style at all. Clamped, because a big correction sounds worse than a
    slightly-off pace.
    """
    target = float(settings.get("words_per_minute") or 0)
    words = len([w for w in re.findall(r"[\w']+", text) if w])
    actual = _duration(raw)
    ratio = 1.0
    if target > 0 and words and actual > 0.05:
        spoken_wpm = words / (actual / 60.0)
        # atempo=r divides the duration by r, so the resulting rate is
        # spoken_wpm * r. To land on the target, r is target/spoken_wpm --
        # dividing the other way round speeds up a line that was already fast.
        ratio = max(0.8, min(1.25, target / spoken_wpm))
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw)]
    if abs(ratio - 1.0) > 0.02:
        cmd += ["-filter:a", "atempo=%.4f" % ratio]
    cmd += ["-ar", str(SAMPLE_RATE), "-ac", "1", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


def _engine_say(engine, text, out_path, voice=None, settings=None):
    """Hand one stretch of text to the engine. No trimming, no mastering.

    Kept separate from `_synthesise` because a line is sometimes spoken in
    several takes -- see `sentence_pieces` -- and those are trimmed one by one
    but levelled together, so the quiet half of "It breathes fire. She's
    faster." does not come back louder than the loud half.
    """
    settings = settings if settings is not None else _voice_style()
    out_path = Path(out_path)
    if engine == "piper":
        model = voice or os.environ.get("PIPER_VOICE")
        if not model or not Path(model).exists():
            raise VoiceError(
                "piper needs a voice model. Download one (for example "
                "en_GB-alba-medium.onnx and its .onnx.json) and set PIPER_VOICE "
                "to the .onnx path.")
        proc = subprocess.run(
            [sys.executable, "-m", "piper", "-m", str(model), "-f", str(out_path)],
            input=text, capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("piper failed: %s" % (proc.stderr or "").strip()[-300:])
    elif engine == "espeak-ng":
        cmd = ["espeak-ng",
               "-s", str(int(settings.get("words_per_minute", 160))),
               "-p", str(int(settings.get("pitch", 45))),
               "-g", str(int(settings.get("word_gap_ms", 8) / 10) or 0),
               "-a", "170"]
        picked = voice or settings.get("engine_voice")
        if picked:
            cmd += ["-v", str(picked)]
        cmd += ["-w", str(out_path), text]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 and picked:
            # an unavailable voice should not lose the line: fall back to the
            # default voice and say which one was missing
            print("voice.py: espeak-ng has no voice %r — using its default. "
                  "`espeak-ng --voices` lists what is installed." % picked,
                  file=sys.stderr)
            proc = subprocess.run([c for c in cmd if c not in ("-v", str(picked))],
                                  capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("espeak-ng failed: %s" % (proc.stderr or "").strip()[-300:])
    elif engine == "kokoro":
        _kokoro(text, out_path, settings)
    elif engine == "elevenlabs":
        _elevenlabs(text, out_path, settings)
    elif engine == "pico2wave":
        # SVOX Pico. No rate control of its own, so the style's
        # words_per_minute is honoured below by measuring what it actually did.
        raw = out_path.with_name(out_path.stem + ".pico.wav")
        lang = settings.get("pico_language", "en-GB")
        proc = subprocess.run(["pico2wave", "-l", lang, "-w", str(raw), text],
                              capture_output=True, text=True)
        if proc.returncode != 0 or not raw.exists():
            raise VoiceError("pico2wave failed: %s" % (proc.stderr or "").strip()[-300:])
        _retime(raw, out_path, text, settings)
        raw.unlink(missing_ok=True)
    elif engine == "say":
        aiff = out_path.with_suffix(".aiff")
        proc = subprocess.run(["say", "-o", str(aiff), text], capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("say failed: %s" % (proc.stderr or "").strip()[-300:])
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(aiff), "-ar", str(SAMPLE_RATE), "-ac", "1",
                        str(out_path)], check=True)
        aiff.unlink(missing_ok=True)
    else:
        raise VoiceError("unknown engine %r. Available: %s"
                         % (engine, ", ".join(n for n, _ in usable_engines()) or "none"))
    if not out_path.exists() or out_path.stat().st_size < 256:
        raise VoiceError("%s produced no audio for %r" % (engine, text[:40]))
    return out_path


# A full stop the engine does not honour.
#
# Kokoro reads a sentence end inside an utterance as a comma, and reads a comma
# as nothing much: measured over four phrases, an internal full stop bought
# 0.06s of silence -- the same as a comma, and the same as an arbitrary point
# mid-clause. `utterances` already keeps consecutive sentences in separate
# takes for that reason, but a single scripted line can still hold two of them:
#
#     23. Not for weeks. For years.  {breath}
#
# and those are the lines that need the stop most, because the second sentence
# is the one that lands. So a line is spoken in as many takes as it has
# sentences and the silence is put in by hand.
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr",
                 "vs", "etc", "no", "fig", "approx"}

_BREAK = re.compile(r"""([.!?…]+)(['"”’)\]]*)\s+""")

# Everything short of a full stop -- a comma, a semicolon, a colon, a dash --
# is a breath rather than the end of a thought, and is NOT a place to break the
# take. Those are `clause_breaths`, which puts the air into the recording
# afterwards instead of asking the engine for it.
_BREATH_MARKS = ",;:—–"
_BIGGER_BREATH = ";:—–"          # a beat longer than a comma, but not a stop


def sentence_pieces(text):
    """Split a line where the voice should FULLY stop. [(text, weight), ...]

    Only at a sentence end, and for a measured reason. A clause synthesised on
    its own comes back with the wrong tune. Measured on "Across deserts,
    through forests, over mountains that nearly kill her.": spoken whole, the
    pitch over "deserts" FALLS 17.6 Hz into the comma; spoken as the clause
    "Across deserts," alone it RISES 15.4 Hz -- identical to the decimal to
    speaking "Across deserts." as a sentence, because the engine cannot tell a
    comma from a full stop. Breaking a take at a comma therefore puts a
    question mark where the script has a comma. Commas are `clause_breaths`
    instead: the tune is left alone and the air goes in afterwards.

    The weight multiplies the style's `sentence_pause_seconds`; the last piece
    is always 0.0 because the gap after the line is the caller's business.
    A line with nothing to split comes back as one piece, which is the usual
    case -- this costs nothing on a line that does not need it.
    """
    pieces, start = [], 0
    for m in _BREAK.finditer(text):
        mark, after = m.group(1), text[m.end():]
        if not after.strip():
            continue                            # the line's own final stop
        before = re.search(r"([\w']+)\W*$", text[start:m.start()])
        word = (before.group(1) if before else "").lower()
        if mark == "." and (word in ABBREVIATIONS or
                            (len(word) == 1 and word.isalpha())):
            continue                            # "Dr. Vale", "J. Smith"
        if mark == "." and not (after[:1].isupper() or after[:1] in "\"'“‘"):
            continue                            # a decimal point, or mid-word
        pieces.append((text[start:m.end(2)].strip(), 1.0))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        pieces.append((tail, 0.0))
    elif pieces:
        pieces[-1] = (pieces[-1][0], 0.0)
    return pieces or [(text.strip(), 0.0)]


def clause_breaths(text):
    """Where inside one spoken take the voice should take a little air.

    Returns [(words before it, seconds multiplier), ...]. A comma is one
    breath; a semicolon, colon or dash is a bigger one, because those separate
    more than a comma does without ending the thought.

    Counted in words rather than characters because the time of a word
    boundary is what the caller has to find, and word counts are what it has.
    """
    words, out, seen = re.findall(r"[\w']+[^\w']*", text), [], 0
    for i, chunk in enumerate(words, 1):
        if i == len(words):
            break                                # nothing after the last word
        mark = next((c for c in chunk if c in _BREATH_MARKS), "")
        if mark:
            out.append((i, 1.6 if mark in _BIGGER_BREATH else 1.0))
    return out


def clause_pause(settings):
    """How much air a comma inside a spoken line gets.

    Kokoro gives one 0.03-0.14s, which is not a breath, it is nothing -- three
    clauses in a row come out as one unbroken run. This is the whole of the
    pause, and it is deliberately far short of `sentence_pause_seconds`: a
    comma that lands like a full stop is the other failure.
    """
    settings = settings or {}
    held = settings.get("comma_pause_seconds")
    if held is None:
        return sentence_pause(settings) * 0.4
    return float(held)


def _samples(path):
    """A take as mono float samples at SAMPLE_RATE, or None."""
    try:
        import numpy as np
    except ImportError:
        return None
    raw = subprocess.run([_ffmpeg(), "-v", "error", "-i", str(path),
                          "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
                         capture_output=True).stdout
    if len(raw) < 2 * SAMPLE_RATE // 10:
        return None
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


# How loud the quietest point near a comma may be, against the take's own
# median, before the splice is abandoned. A word boundary dips even when it
# does not go silent; the middle of a vowel does not. Cutting there would put a
# stutter in the line, which is worse than the run-on it was meant to fix.
QUIET_ENOUGH = 0.40


def _quietest(x, want, search=0.10, win=0.02):
    """The quietest instant near `want` seconds, so a splice lands off a vowel.

    Returns (seconds, how loud it is against the take's median) or None when
    the search window falls outside the take. The estimate from word counts is
    good to about a tenth of a second, which is enough to land inside a word,
    so the local energy minimum is a much better place to cut than the estimate
    itself -- and the caller still gets to refuse it.
    """
    import numpy as np
    w = max(8, int(win * SAMPLE_RATE))
    lo = max(w, int((want - search) * SAMPLE_RATE))
    hi = min(len(x) - w, int((want + search) * SAMPLE_RATE))
    if hi <= lo:
        return None
    power = np.convolve(x[lo - w:hi + w] ** 2, np.ones(w) / w, mode="same")[w:-w or None]
    if not len(power):
        return None
    k = int(np.argmin(power))
    loud = float(np.median(np.convolve(x ** 2, np.ones(w) / w, mode="same"))) or 1e-9
    return (lo + k) / float(SAMPLE_RATE), float(power[k]) / loud


def _breathe(path, text, settings, weights=None):
    """Put air at the commas of a take that the engine ran straight through.

    The take is spoken whole so its tune is right, then opened up afterwards.
    Silence is spliced in at the quietest point near each comma, with a few
    milliseconds of fade either side so the join does not click.

    Returns the seconds added, or 0.0 if nothing was done -- a missing numpy,
    an unreadable wav or a line with no commas all mean "leave it alone".
    """
    marks = clause_breaths(text)
    held = clause_pause(settings)
    if not marks or held <= 0.001:
        return 0.0
    x = _samples(path)
    if x is None:
        return 0.0
    try:
        import numpy as np
    except ImportError:
        return 0.0
    total = len(x) / float(SAMPLE_RATE)
    words = re.findall(r"[\w']+", text)
    sizes = [float(d or 0.0) for _, d in (weights or [])]
    if len(sizes) != len(words) or not sum(sizes):
        # no measured durations: a word's length tracks its spelling closely
        # enough to put the search window in the right place
        sizes = [len(w) + 1.0 for w in words]
    run = sum(sizes)
    cuts, refused = [], 0
    for n, weight in marks:
        want = total * (sum(sizes[:n]) / run)
        found = _quietest(x, want)
        if found is None:
            continue
        at, loud = found
        if loud > QUIET_ENOUGH:
            refused += 1               # nothing but voice there; leave it alone
            continue
        cuts.append((at, held * weight))
    if refused:
        print("voice.py: %d comma%s in %r had no gap to open — left as spoken"
              % (refused, "" if refused == 1 else "s", text[:40]), file=sys.stderr)
    if not cuts:
        return 0.0
    fade = int(0.006 * SAMPLE_RATE)
    out, last, added = [], 0, 0.0
    for at, seconds in sorted(cuts):
        i = int(at * SAMPLE_RATE)
        if i - last < fade * 2:
            continue
        head = x[last:i].copy()
        head[-fade:] *= np.linspace(1.0, 0.0, fade, dtype="float32")
        out.append(head)
        out.append(np.zeros(int(seconds * SAMPLE_RATE), dtype="float32"))
        added += seconds
        last = i
    tail = x[last:].copy()
    tail[:fade] *= np.linspace(0.0, 1.0, fade, dtype="float32")
    out.append(tail)
    joined = np.concatenate(out)
    with wave.open(str(path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes((np.clip(joined, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return added


def sentence_pause(settings):
    """How long a stop inside a line is held for.

    Longer than `gap_seconds` on purpose. Between two takes the silence is the
    gap plus what frame quantisation and the beat floor add to it -- measured
    at a 0.36s median. Inside a take there is none of that, so the number here
    is the whole pause and has to stand in for all of it.
    """
    settings = settings or {}
    held = settings.get("sentence_pause_seconds")
    if held is None:
        return float(settings.get("gap_seconds", PAD_SECONDS)) * 2.0
    return float(held)


def _say(engine, pieces, out_path, voice, settings, weights=None, tempo=1.0):
    """Speak one line, in the order that keeps each step honest.

    Say it, trim the engine's own silence off both ends, apply the style's
    tone, THEN level, THEN write in the silence the engine would not give.

    The order is the whole point. Levelling used to happen last, over a take
    that already had the pauses in it, and a line with a stop in the middle
    therefore measured quiet and was turned up. Measure the speech, and only
    the speech; the silence is not part of how loud someone is talking.
    """
    solo = len(pieces) == 1
    parts, slices, taken = [], [], 0
    for n, (piece, _) in enumerate(pieces, 1):
        part = out_path if solo else out_path.with_name(
            "%s.p%02d.wav" % (out_path.stem, n))
        _engine_say(engine, piece, part, voice, settings)
        trim_silence(part, settings)
        # before the tone and the level, and well before the silence: a
        # pause written in seconds must not then be sped up with the words
        _stretch(part, tempo)
        _master(part, settings)
        count = len(re.findall(r"[\w']+", piece))
        slices.append((weights or [])[taken:taken + count] if weights else None)
        taken += count
        parts.append(part)
    _level_together(parts, settings)
    for part, (piece, _), got in zip(parts, pieces, slices):
        _breathe(part, piece, settings, got)
    if solo:
        return out_path

    held = sentence_pause(settings)
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    labels, n = [], 0
    for part, (_, weight) in zip(parts, pieces):
        cmd += ["-i", str(part)]
        labels.append("[%d:a]" % n); n += 1
        if weight > 0:
            cmd += ["-f", "lavfi", "-t", "%.3f" % (held * weight),
                    "-i", "anullsrc=r=%d:cl=mono" % SAMPLE_RATE]
            labels.append("[%d:a]" % n); n += 1
    graph = "%sconcat=n=%d:v=0:a=1[out]" % ("".join(labels), len(labels))
    cmd += ["-filter_complex", graph, "-map", "[out]",
            "-ar", str(SAMPLE_RATE), "-ac", "1", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for part in parts:
        part.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 256:
        raise VoiceError("could not join %d takes of %r: %s"
                         % (len(pieces), pieces[0][0][:30],
                            (proc.stderr or "").strip()[-200:]))
    return out_path


def _synthesise(engine, text, out_path, voice=None, settings=None, weights=None,
                tempo=1.0):
    """One line of text to one wav, spoken and treated to the committed style.

    Two silences no engine here supplies are put in by hand. A line holding
    more than one sentence is spoken as one take per sentence with a full stop
    between them; the commas inside each take are opened up afterwards, in the
    recording, because asking the engine for them changes the tune.

    `weights` is [(word, seconds), ...] for this line, from `measure_words`.
    It only decides where a breath goes; without it the placement falls back to
    spelling, which is close enough to find the gap between two words.
    """
    settings = settings if settings is not None else _voice_style()
    out_path = Path(out_path)
    pieces = sentence_pieces(text)
    try:
        return _say(engine, pieces, out_path, voice, settings, weights, tempo)
    except VoiceError:
        if len(pieces) == 1:
            raise                           # nothing left to fall back to
        # a run-on line is better than a missing one
        print("voice.py: could not speak %r in %d takes — one instead"
              % (text[:40], len(pieces)), file=sys.stderr)
        return _say(engine, [(text, 0.0)], out_path, voice, settings, weights, tempo)


# --------------------------------------------------------------------------
# script
# --------------------------------------------------------------------------
# A narrator who reads every line at one rate is most of what people mean when
# they say a voice sounds synthetic, and a rate multiplier per beat only gets
# part of the way there. Delivery is pace, pitch and -- most of all -- where the
# silence goes. The directions belong with the words rather than in a flag, so
# they are written into the script itself:
#
#     8. Then she understood.  {slow, low, hold}
#
# They are stripped before the line is spoken or captioned, so the script stays
# the script.
DELIVERY = {
    "slow":  {"rate": 0.86},
    "slower": {"rate": 0.76},
    "fast":  {"rate": 1.14},
    "faster": {"rate": 1.26},
    # Small on purpose. Shifting pitch by resampling drags the formants along
    # with it, so the same voice stops sounding like the same person -- measured
    # on a finished track, lines asked to drop 1.6 to 3 semitones had spectral
    # centroids from 38% below the rest of the narration to 18% above, and it
    # reads as the narrator being swapped mid-sentence rather than dropping
    # their voice. ffmpeg's rubberband with formant=preserved was no better on
    # speech this short. Under about a semitone the colour changes and the
    # speaker does not, which is all this was ever meant to do; pace and
    # silence carry the rest.
    "low":   {"pitch": -0.5},
    "lower": {"pitch": -1.0},
    "high":  {"pitch": 0.5},
    "higher": {"pitch": 1.0},
    # Three lengths of silence, because a narration with none is as wrong as
    # one with a gap at every cut. A breath separates two thoughts, a hold
    # lands a line, a beat is the one before a reveal.
    "breath": {"hold": 0.18},
    "hold":  {"hold": 0.34},          # sit on the shot before the next line
    "beat":  {"hold": 0.6},
}
_DIRECTIVE = re.compile(r"\s*\{([^{}]*)\}\s*$")


def _split_directives(text):
    """(line, {rate, pitch, hold}) -- the spoken line and how to deliver it."""
    spec = {}
    match = _DIRECTIVE.search(text)
    if not match:
        return text.strip(), spec
    for word in match.group(1).replace(";", ",").split(","):
        word = word.strip().lower()
        if not word:
            continue
        if word not in DELIVERY:
            raise VoiceError(
                "%r is not a delivery direction. Known ones: %s"
                % (word, ", ".join(sorted(DELIVERY))))
        for key, value in DELIVERY[word].items():
            # two directions of the same kind compound rather than fight:
            # {slow, slower} is slower than either, which is what writing both
            # was asking for
            spec[key] = spec.get(key, 1.0) * value if key == "rate" \
                else spec.get(key, 0.0) + value
    return text[:match.start()].strip(), spec


def beats_from_script(script_path):
    """The numbered beats, in order — the same ones render.py plans from.

    Delivery directions are stripped: they tell the narrator how to read the
    line, and they are not part of it.
    """
    return [line for line, _ in _script_lines(script_path)]


def delivery_from_script(script_path):
    """{beat number: {rate, pitch, hold}} for the beats that name a delivery."""
    return {i: spec for i, (_, spec) in enumerate(_script_lines(script_path), 1)
            if spec}


def _script_lines(script_path):
    """[(line, delivery spec), ...] in beat order."""
    text = Path(script_path).read_text(encoding="utf-8")
    beats = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)[.)]\s+(.*\S)", line)
        if match:
            beats.append(_split_directives(match.group(2).strip()))
    if not beats:
        raise VoiceError("no numbered beats found in %s — write them as '1. ...'"
                         % script_path)
    return beats


def utterances(lines, delivery=None, max_words=22):
    """Group consecutive beats into the sentences they will be spoken as.

    Reading a sentence one clause at a time puts a full stop in the middle of
    it. The engine gives every fragment its own falling intonation and its own
    trailing breath, and with a cut on each one that lands as the voice
    stopping and starting again at every change of picture -- measured on a
    41-beat video, 0.16s of silence at the median cut and over 0.15s at 22 of
    the 40. Spoken whole, the sentence has one contour and the pictures cut
    underneath it.

    A group ends at every sentence boundary, at a hold, and as a backstop once
    it reaches `max_words`. It does NOT run two sentences together, and that
    is deliberate: measured on Kokoro, a full stop inside an utterance buys
    0.06s of silence -- the same as a comma, and the same as an arbitrary point
    mid-clause. Joining sentences therefore deletes the pause between them
    rather than shortening it, and the narration reads straight past the end of
    one thought into the next.

    The delivery therefore belongs to a passage rather than to a clause: the
    rate and pitch come from the first beat in the group that names any, and
    the hold from the last.
    """
    delivery = delivery or {}
    groups, current, words = [], [], 0
    for i, line in enumerate(lines, 1):
        current.append(i)
        words += len(re.findall(r"[\w']+", line))
        ends_sentence = line.rstrip().endswith((".", "!", "?"))
        if ends_sentence or (delivery.get(i) or {}).get("hold") or words >= max_words:
            groups.append(current); current, words = [], 0
    if current:
        groups.append(current)
    return groups


def _group_spec(beats, delivery):
    """(rate, pitch, hold) for a group of beats spoken as one utterance."""
    delivery = delivery or {}
    rate, pitch = 1.0, 0.0
    for i in beats:
        spec = delivery.get(i) or {}
        if spec.get("rate") and rate == 1.0:
            rate = float(spec["rate"])
        if spec.get("pitch") and pitch == 0.0:
            pitch = float(spec["pitch"])
    hold = float((delivery.get(beats[-1]) or {}).get("hold", 0.0))
    return rate, pitch, hold


def _inside_pauses(beats, lines, settings):
    """Seconds of hand-placed silence inside each beat of an utterance.

    A beat holding two sentences carries a stop, and a beat with a comma in it
    carries a breath. The word weights know about neither, so without this the
    picture would cut early by however long they run. A comma at the very end
    of a beat is charged to that beat, because that is where it falls.
    """
    held, air = sentence_pause(settings), clause_pause(settings)
    out = [sum(w for _, w in sentence_pieces(lines[i - 1])) * held for i in beats]
    counts = [len(re.findall(r"[\w']+", lines[i - 1])) for i in beats]
    for n, weight in clause_breaths(" ".join(lines[i - 1] for i in beats)):
        seen = 0
        for k, count in enumerate(counts):
            seen += count
            if n <= seen:
                out[k] += air * weight
                break
    return out


def _shares(beats, lines, weights=None, pauses=None, seconds=None):
    """How an utterance's length divides between the beats inside it.

    From measured word durations when they are available -- they are already
    computed for the karaoke timing -- and from syllables when they are not.
    Only the ratios matter; the total is whatever the engine actually took.

    `pauses` and `seconds` fold in silence this module put inside a beat by
    hand, which is time the beat takes but no word accounts for.
    """
    sizes = []
    for i in beats:
        line = lines[i - 1]
        if weights and len(weights) >= i and weights[i - 1]:
            sizes.append(sum(float(d) for _, d in weights[i - 1]) or 0.0)
        else:
            sizes.append(0.0)
    if not any(sizes):
        sizes = [max(1.0, float(len(re.findall(r"[\w']+", lines[i - 1])))) for i in beats]
    total = sum(sizes) or float(len(beats))
    shares = [x / total for x in sizes]
    if not pauses or not any(pauses) or not seconds:
        return shares
    speech = max(0.0, float(seconds) - sum(pauses))
    lengths = [s * speech + p for s, p in zip(shares, pauses)]
    grand = sum(lengths) or 1.0
    return [x / grand for x in lengths]


def speak(script_path, out_dir, engine=None, voice=None, recorded=None, style=None,
          emphasis=None, delivery=None, weights=None):
    """Narrate the script. Returns one entry per UTTERANCE, not per beat.

    Each entry is {path, seconds, beats, shares, hold}: the wav, how long it
    ran, which beats it covers, how its length divides between them, and any
    hold asked for at its end. Several beats share one recording whenever they
    are clauses of the same sentence -- see `utterances` for why.

    `emphasis` is {beat number: rate multiplier}; `delivery` is the richer
    {beat: {rate, pitch, hold}} the script's own `{slow, hold}` directions
    become. Both apply per sentence now: an utterance is spoken at one rate.

    `weights` is measure_words' output, used to work out where inside an
    utterance each beat ends. Without it the split falls back to word counts.
    """
    beats = beats_from_script(script_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = _voice_style(style)          # read once, not per line

    if recorded:
        source = Path(recorded)
        spoken = []
        for i in range(len(beats)):
            candidates = sorted(source.glob("beat%02d.*" % (i + 1))) or \
                sorted(source.glob("beat%d.*" % (i + 1)))
            if not candidates:
                raise VoiceError("no recording for beat %d in %s (expected beat%02d.wav)"
                                 % (i + 1, source, i + 1))
            target = out_dir / ("beat%02d.wav" % (i + 1))
            subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(candidates[0]), "-ar", str(SAMPLE_RATE),
                            "-ac", "1", str(target)], check=True)
            # a recorded voice gets the same treatment: it needs the levelling
            # more than a synthesiser does, and the point is that every video
            # sounds the same whoever or whatever spoke it
            trim_silence(target, settings)
            _master(target, settings)
            spoken.append({"path": target, "seconds": _duration(target),
                           "beats": [i + 1], "shares": [1.0],
                           "hold": float((delivery or {}).get(i + 1, {}).get("hold", 0.0))})
        return spoken

    if engine is None:
        options = [n for n, _ in usable_engines()]
        if not options:
            raise VoiceError(
                "no speech engine available. Install one: pip install piper-tts "
                "(then set PIPER_VOICE), apt install espeak-ng, or record the "
                "lines yourself and pass --recorded <dir>. Your own voice is "
                "better than any of them for commentary.")
        engine = options[0]

    emphasis = emphasis or {}
    delivery = delivery or {}
    spoken, said_ceiling = [], False
    for n, group in enumerate(utterances(beats, delivery), 1):
        text = " ".join(beats[i - 1] for i in group)
        rate, pitch, hold = _group_spec(group, delivery)
        for i in group:                      # emphasis is still per beat
            rate *= float(emphasis.get(i, 1.0))
        target = out_dir / ("say%02d.wav" % n)
        # the measured words for this group, flattened -- they place the
        # breaths inside the take
        flat = None
        if weights:
            flat = [w for i in group
                    for w in (weights[i - 1] if len(weights) >= i else [])]
        per_line = settings
        if abs(rate - 1.0) > 0.001:
            per_line = dict(settings)
            for key in ("kokoro_speed", "words_per_minute"):
                if key in per_line and per_line[key]:
                    per_line[key] = type(per_line[key])(per_line[key] * rate)
        # A rate mark is a multiplier on the style's base, and the style's base
        # has been raised since the marks were chosen -- {faster} on a 1.2 base
        # asks for 1.512, well past what the engine says evenly. Ask for what it
        # can say and do the rest afterwards.
        per_line, tempo = _split_speed(per_line, float(per_line.get("kokoro_speed") or 0.0))
        if tempo > 1.0001 and not said_ceiling:
            said_ceiling = True             # once per narration, not per take
            print("voice.py: a line wants %.3fx; asking kokoro for %.2f and "
                  "taking the rest evenly with atempo, because past %.2f it "
                  "squeezes the end of a phrase"
                  % (float(settings.get("kokoro_speed") or 0) * rate,
                     ARTICULATE_SPEED, ARTICULATE_SPEED), file=sys.stderr)
        _synthesise(engine, text, target, voice, per_line, flat, tempo)
        if pitch:
            _pitch(target, pitch)
        seconds = _duration(target)
        spoken.append({"path": target, "seconds": seconds,
                       "beats": list(group),
                       "shares": _shares(group, beats, weights,
                                         _inside_pauses(group, beats, per_line),
                                         seconds),
                       "hold": hold})
    return spoken


def beat_lengths(spoken, fps, gap, floor):
    """Beat durations, in whole frames, that sum exactly to the audio.

    The picture cuts inside a continuous sentence, so the beats of one
    utterance have to add up to that utterance's own length -- a rounding
    error here is a beat of drift between voice and picture that never comes
    back. Frames are handed out by the measured shares, then the remainder is
    given to the longest beats, and anything below the floor borrows from
    whichever sibling has most to spare.
    """
    out = {}
    low = max(1, int(round(floor * fps)))
    for utt in spoken:
        # The hold belongs to the beat that asked for it, NOT to the passage it
        # sits in. Adding it to the total and splitting by share gave "By her."
        # -- a line marked for the longest pause in the script -- 29% of a 0.6s
        # hold and a 0.77s shot, while the clause before it took the rest. The
        # two most important moments in the video flashed past because their
        # lines were short.
        hold_frames = max(0, int(round(float(utt.get("hold") or 0.0) * fps)))
        total = utt["seconds"] + gap
        frames = max(len(utt["beats"]), int(round(total * fps)))
        want = [f * frames for f in utt["shares"]]
        got = [max(1, int(x)) for x in want]
        # hand out what rounding left over, biggest fractional part first
        spare = frames - sum(got)
        order = sorted(range(len(got)), key=lambda k: -(want[k] - int(want[k])))
        for k in range(spare):
            got[order[k % len(got)]] += 1
        while spare < 0:                     # gave away too much; take it back
            got[max(range(len(got)), key=lambda k: got[k])] -= 1
            spare += 1
        if len(got) > 1:
            # A safety net for a share too small to see, NOT a target: set it
            # to the style's floor and it overrides the measured shares
            # entirely -- 0.5/0.3/0.2 of a three-second sentence came out as
            # three equal beats, which is the picture ignoring the voice it is
            # supposed to be cut to. Stop a beat being a flash and leave the
            # rest alone.
            want_low = min(low, max(1, int(round(INSIDE_FLOOR * fps))),
                           frames // len(got))
            for k in range(len(got)):
                while got[k] < want_low:
                    donor = max(range(len(got)), key=lambda j: got[j])
                    if donor == k or got[donor] - 1 < want_low:
                        break
                    got[donor] -= 1
                    got[k] += 1
        got[-1] += hold_frames           # the silence lands on the line that asked
        if len(got) == 1 and got[0] < low:
            # A whole passage spoken on its own still gets the style's minimum,
            # the way every beat used to. Applied AFTER the hold, because the
            # floor is about how long the shot is and the hold is part of that
            # -- flooring the spoken part and then adding the hold counts the
            # same silence twice.
            got[0] = low
        for beat, f in zip(utt["beats"], got):
            out[beat] = round(f / float(fps), 6)
    return out


# --------------------------------------------------------------------------
# cut the video to the voice
# --------------------------------------------------------------------------
def fit_plan(plan_path, spoken, pad=None, style=None, delivery=None):
    """Rewrite each beat's duration to where it falls in the narration.

    This is the whole point: a beat that runs shorter than its line cuts the
    narration off mid-sentence, and one that runs longer leaves dead air. The
    spoken length is the truth, so the cut follows it.

    `spoken` is speak()'s output, one entry per utterance. Where several beats
    share an utterance the picture cuts inside a continuous sentence, and their
    durations have to add up to that utterance's own length exactly -- see
    beat_lengths. The gap is applied once per utterance rather than once per
    beat, because it is the pause between sentences, not between words.
    """
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    beats = plan.get("beats")
    if not isinstance(beats, list) or not beats:
        raise VoiceError("%s has no beats" % path)
    covered = [i for utt in spoken for i in utt["beats"]]
    if sorted(covered) != list(range(1, len(beats) + 1)):
        raise VoiceError(
            "the narration covers beats %s but the plan has %d. They have to "
            "match — regenerate the plan from the same script."
            % (_span(covered), len(beats)))

    sys.path.insert(0, str(HERE))
    import style as style_mod
    look = style or style_mod.current()
    if pad is None:
        pad = float((look.get("voice") or {}).get("gap_seconds", PAD_SECONDS))
    fps = float(look["format"].get("fps") or 30)
    low = look["pacing"]["min_beat_seconds"]
    high = look["pacing"]["max_beat_seconds"]

    lengths = beat_lengths(spoken, fps, pad, low)
    notes, total = [], 0.0
    for utt in spoken:
        ran = sum(lengths[i] for i in utt["beats"])
        if ran > high + 0.01 and len(utt["beats"]) == 1:
            notes.append(
                "beat %d needs %.1fs to say but the style caps a beat at %.1fs — "
                "the line is too long for this pacing, so shorten the line rather "
                "than stretching the beat." % (utt["beats"][0], ran, high))
    for i, beat in enumerate(beats, 1):
        beat["duration"] = lengths[i]
        total += lengths[i]

    ok, reasons = style_mod.shorts_verdict(total, look["format"]["width"],
                                           look["format"]["height"], look)
    if not ok:
        raise VoiceError("spoken at this length the video would not be a Short: "
                         + " ".join(reasons) + " Cut lines.")
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return {"total": round(total, 2), "notes": notes,
            "durations": [b["duration"] for b in beats],
            "utterances": len(spoken)}


def _span(numbers):
    if not numbers:
        return "none"
    return "%d-%d" % (min(numbers), max(numbers))


def build_track(plan_path, spoken, out_path):
    """One audio track: each utterance once, padded to the beats it covers.

    An utterance is written whole. Nothing is inserted between the clauses
    inside it -- that silence is exactly what made the voice stop at every cut.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    beats = plan["beats"]
    covered = [i for utt in spoken for i in utt["beats"]]
    if sorted(covered) != list(range(1, len(beats) + 1)):
        raise VoiceError("the narration and the plan do not cover the same beats")

    ff = _ffmpeg()
    out_path = Path(out_path)
    parts_dir = out_path.parent / (out_path.stem + "-parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    listing = []
    for n, utt in enumerate(spoken, 1):
        want = sum(float(beats[i - 1]["duration"]) for i in utt["beats"])
        padded = parts_dir / ("seg%02d.wav" % n)
        # apad then -t: the sentence plays, then silence to the end of the last
        # beat it covers
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(utt["path"]),
                        "-af", "apad", "-t", "%.3f" % want,
                        "-ar", str(SAMPLE_RATE), "-ac", "1", str(padded)], check=True)
        listing.append(padded)

    concat = parts_dir / "list.txt"
    concat.write_text("".join("file '%s'\n" % p.resolve() for p in listing), encoding="utf-8")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                    "-safe", "0", "-i", str(concat), "-c:a", "pcm_s16le",
                    "-ar", str(SAMPLE_RATE), "-ac", "1", str(out_path)], check=True)
    shutil.rmtree(parts_dir, ignore_errors=True)

    # One more loudness pass over the assembled track. Normalising each line
    # evens out line-to-line variation, but the integrated loudness of the
    # whole track still lands wherever the mix of speech and gaps puts it --
    # measured 2dB under target on one engine and on target on another. The
    # style names a number; deliver that number.
    settings = _voice_style()
    target = settings.get("loudness_lufs")
    if target is not None:
        levelled = out_path.with_name(out_path.stem + ".level.wav")
        proc = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(out_path),
             "-af", "loudnorm=I=%.1f:TP=-1.5:LRA=11" % float(target),
             "-ar", str(SAMPLE_RATE), "-ac", "1", str(levelled)],
            capture_output=True, text=True)
        if proc.returncode == 0 and levelled.exists() and levelled.stat().st_size > 256:
            levelled.replace(out_path)
        else:
            levelled.unlink(missing_ok=True)
            print("voice.py: could not level the finished track (%s) — the "
                  "per-line levels still apply"
                  % (proc.stderr or "").strip()[-120:], file=sys.stderr)
    return {"path": str(out_path), "duration": _duration(out_path)}


def measure_words(script_path, out_dir=None, engine=None, voice=None, style=None):
    """How long the engine takes to say each word, line by line.

    Guessing this from spelling is a losing game -- English syllable counting
    without a dictionary gets "video" and "creative" wrong, and those errors
    land the on-screen word next to the spoken one rather than on it. The
    engine already knows: ask it.

    A word said alone runs longer than the same word in a sentence, but these
    are only used as relative weights inside a beat whose total length is
    already measured, so the stretch cancels out.

    Returns [[(word, seconds), ...], ...], one list per beat. Falls back to
    None when no engine can speak, and the caller then estimates.
    """
    beats = beats_from_script(script_path)
    if engine is None:
        options = [n for n, _ in usable_engines()]
        if not options:
            return None
        engine = options[0]
    settings = _voice_style(style)
    tmp = Path(out_dir or (HERE / "voice")) / "_words"
    tmp.mkdir(parents=True, exist_ok=True)
    measured = []
    for b, line in enumerate(beats, 1):
        words = [w for w in re.split(r"\s+", line.strip()) if w]
        row = []
        for i, word in enumerate(words, 1):
            target = tmp / ("b%02d_w%03d.wav" % (b, i))
            try:
                _synthesise(engine, word, target, voice, settings)
                row.append((word, _duration(target)))
            except (VoiceError, subprocess.CalledProcessError):
                row.append((word, None))       # caller estimates this one
            finally:
                target.unlink(missing_ok=True)
        measured.append(row)
    shutil.rmtree(tmp, ignore_errors=True)
    return measured


def annotate_plan(plan_path, measured):
    """Write per-word speaking times onto each beat, for the caption builder."""
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    beats = plan.get("beats") or []
    if len(measured) != len(beats):
        raise VoiceError("measured %d lines but the plan has %d beats"
                         % (len(measured), len(beats)))
    written = 0
    for beat, row in zip(beats, measured):
        usable = [[w, round(s, 3)] for w, s in row if s and s > 0]
        if len(usable) == len(row) and usable:
            beat["words"] = usable
            written += 1
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return written


def attach(plan_path, track_path, licence="original narration"):
    """Point the render plan at the finished voice track."""
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["audio"] = {"path": str(track_path), "license": licence}
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan["audio"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="voice.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("engines", help="what speech engines are available here")

    def add_common(p):
        p.add_argument("--engine")
        p.add_argument("--voice", help="piper .onnx path (or set PIPER_VOICE)")
        p.add_argument("--recorded", help="directory of beat01.wav … you recorded")
        p.add_argument("--out-dir", default="voice")
        return p

    p_speak = add_common(sub.add_parser("speak", help="synthesise one wav per beat"))
    p_speak.add_argument("script")

    p_fit = sub.add_parser("fit", help="rewrite beat durations to the spoken lengths")
    p_fit.add_argument("plan")
    p_fit.add_argument("--voice-dir", default="voice")

    p_track = sub.add_parser("track", help="assemble the single voice track")
    p_track.add_argument("plan")
    p_track.add_argument("--voice-dir", default="voice")
    p_track.add_argument("-o", "--out", default="voice.wav")

    p_nar = add_common(sub.add_parser("narrate", help="speak, fit and attach in one go"))
    p_nar.add_argument("script")
    p_nar.add_argument("plan")
    p_nar.add_argument("-o", "--out", default="voice.wav")

    args = parser.parse_args(argv)
    try:
        if args.command == "engines":
            found = available_engines()
            for name, note, ok in found:
                print("  %-12s %-4s %s" % (name, "ok" if ok else "--", note))
            # Exit non-zero unless something can actually speak: an engine that
            # is installed but unusable must not read as a passing check.
            if not any(ok for _, _, ok in found):
                print("\nno engine can speak yet. Any one of these is enough:\n"
                      "  pip install piper-tts   (then set PIPER_VOICE to a .onnx voice)\n"
                      "  apt install espeak-ng   /  brew install espeak-ng\n"
                      "  record the lines yourself and pass --recorded <dir>  <- best")
                return 1
            return 0

        if args.command == "speak":
            clips = speak(args.script, args.out_dir, args.engine, args.voice, args.recorded)
            for path, seconds in clips:
                print("  %-22s %.2fs" % (Path(path).name, seconds))
            print("%d line(s), %.1fs spoken" % (len(clips), sum(c[1] for c in clips)))
            return 0

        if args.command in ("fit", "track"):
            voice_dir = Path(args.voice_dir)
            wavs = sorted(voice_dir.glob("say*.wav")) or sorted(voice_dir.glob("beat*.wav"))
            if not wavs:
                raise VoiceError("no say*.wav in %s — run voice.py speak first" % voice_dir)
            # Loose wavs on disk carry no record of which beats they cover, so
            # this path assumes one per beat. `narrate` does it properly.
            clips = [{"path": w, "seconds": _duration(w), "beats": [i],
                      "shares": [1.0], "hold": 0.0}
                     for i, w in enumerate(wavs, 1)]
            if args.command == "fit":
                got = fit_plan(args.plan, clips)
                print("beats fitted to the voice: %s" %
                      ", ".join("%.2fs" % d for d in got["durations"]))
                for note in got["notes"]:
                    print("  note: %s" % note)
                print("total %.2fs" % got["total"])
            else:
                got = build_track(args.plan, clips, args.out)
                print("%s  %.2fs" % (got["path"], got["duration"]))
            return 0

        weights = measure_words(args.script, out_dir=args.out_dir, engine=args.engine)
        clips = speak(args.script, args.out_dir, args.engine, args.voice, args.recorded,
                      delivery=delivery_from_script(args.script), weights=weights)
        fitted = fit_plan(args.plan, clips)
        for note in fitted["notes"]:
            print("  note: %s" % note)
        track = build_track(args.plan, clips, args.out)
        attach(args.plan, args.out)
        print("narrated: %d lines, %.2fs, cut fitted to the voice" %
              (len(clips), track["duration"]))
        print("plan now points at %s — render it with: python3 render.py build %s"
              % (args.out, args.plan))
        return 0
    except VoiceError as exc:
        print("voice.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
