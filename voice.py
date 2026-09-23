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
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
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
    """
    settings = settings or {}
    path = Path(path)
    floor = float(settings.get("silence_floor_db", -45))
    keep = float(settings.get("keep_head_ms", 25)) / 1000.0
    before = _duration(path)
    trimmed = path.with_name(path.stem + ".trim.wav")
    chain = ("silenceremove=start_periods=1:start_threshold=%ddB:start_silence=%.3f"
             ":detection=peak,areverse,"
             "silenceremove=start_periods=1:start_threshold=%ddB:start_silence=%.3f"
             ":detection=peak,areverse" % (floor, keep, floor, keep))
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
    loudness = number("loudness_lufs")
    if loudness is not None:
        # single-pass loudnorm: not as exact as two-pass, but a per-beat clip is
        # short enough that the difference is inaudible, and it keeps one line
        # of narration from arriving twice as loud as the next
        chain.append("loudnorm=I=%.1f:TP=-1.5:LRA=11" % loudness)
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


def _synthesise(engine, text, out_path, voice=None, settings=None):
    """One line of text to one wav, spoken and treated to the committed style."""
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
    trim_silence(out_path, settings)
    return _master(out_path, settings)


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


def _shares(beats, lines, weights=None):
    """How an utterance's length divides between the beats inside it.

    From measured word durations when they are available -- they are already
    computed for the karaoke timing -- and from syllables when they are not.
    Only the ratios matter; the total is whatever the engine actually took.
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
    return [x / total for x in sizes]


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
    spoken = []
    for n, group in enumerate(utterances(beats, delivery), 1):
        text = " ".join(beats[i - 1] for i in group)
        rate, pitch, hold = _group_spec(group, delivery)
        for i in group:                      # emphasis is still per beat
            rate *= float(emphasis.get(i, 1.0))
        target = out_dir / ("say%02d.wav" % n)
        per_line = settings
        if abs(rate - 1.0) > 0.001:
            per_line = dict(settings)
            for key in ("kokoro_speed", "words_per_minute"):
                if key in per_line and per_line[key]:
                    per_line[key] = type(per_line[key])(per_line[key] * rate)
        _synthesise(engine, text, target, voice, per_line)
        if pitch:
            _pitch(target, pitch)
        spoken.append({"path": target, "seconds": _duration(target),
                       "beats": list(group),
                       "shares": _shares(group, beats, weights),
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
