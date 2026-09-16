"""Record the committed sample clips that the engine layer is tested against.

WHAT THIS IS FOR
----------------
`lab/voice/` can synthesise speech, recognise it, perturb it and time it. Proving
any of that needs real audio on disk — and a repository that says "download 330 MB
and buy two API keys before the first test runs" is a repository nobody runs.

So a small set of lines is synthesised **once**, written as 16 kHz mono Ogg Opus,
indexed in a manifest, and committed. From then on `FixtureTTS` and `RecordedSTT`
replay them, and `pytest` needs no engine, no network and no credential.

WHY IT SYNTHESISES LOCALLY
--------------------------
`say(1)` ships with macOS, costs nothing and needs no key. That makes this whole
fixture set reproducible by anyone on a Mac with one command, and the committed
Opus files make it replayable by everyone else. Kokoro is available with
`--engine kokoro` for a platform-independent regeneration; it is a download rather
than a default for the same reason `say` is not the default engine anywhere else
in the library.

WHAT IT IS NOT
--------------
It records **lines, not conversations**. There is no agent here and no scenario
corpus: the audio tier's subject is the audio layer, and a fixture set that
depended on a particular system under test would have to be re-recorded every time
that system changed a sentence. The conversational evidence in this repository is
the advisory spoken call under `fixtures/audio/spoken_call/`, recorded by
`roleplay/spoken.py` against real cloud engines.

THE TWO PASSES, AND WHY THEY CANNOT BE ONE
------------------------------------------
Clips are written first, then the cassette is keyed from the audio **as it reads
back off disk** — after the Opus round trip. A cassette keyed on the audio before
encoding would miss on every lookup, because the bytes that reach the recogniser
at replay time are the decoded ones.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from lab.voice.engines.audiofile import read_audio, write_audio
from lab.voice.engines.base import audio_digest, text_digest
from lab.voice.engines.stt import REFERENCE_ENGINE, TranscriptCassette
from lab.voice.engines.tts import ClipManifest, KokoroTTS, SystemSayTTS

__all__ = [
    "FIXTURE_ROOT",
    "CALLER_LINES",
    "AGENT_LINES",
    "CLIP_SUFFIX",
    "clip_is_unchanged",
    "record_clips",
    "record_cassette",
    "write_readme",
    "main",
]

#: Where the committed fixtures live, relative to the repository root.
FIXTURE_ROOT = Path("fixtures/audio")

CLIP_SUFFIX = ".opus"

DEFAULT_SAMPLE_RATE = 16_000

#: Lines whose committed audio must never be re-synthesised. The WebRTC
#: recordings under `fixtures/audio/transport/` were captured sending this exact
#: clip, and `lab/voice/transport/report.py` re-perturbs the same file offline to
#: build the other half of that comparison.
PINNED_LINES: frozenset[str] = frozenset(
    {
        # Sent over a real WebRTC room; the transport comparison re-perturbs this
        # exact file offline to build its other half.
        "What date were you thinking of?",
        # Two sentences with a gap longer than the tolerance. `test_voice_transport`
        # uses it to prove the clip guard refuses audio that would split into two
        # speech runs — a property of this recording, not of its wording.
        "That is all in hand. Is there anything else I can help with?",
    }
)

#: The customer's side. Chosen for what they exercise rather than for drama: a
#: long sentence and a short one (duration arithmetic), a proper name (a word no
#: recogniser has a prior for), a spoken number and a spoken reference code (the
#: two things a recogniser gets wrong in ways that matter), and a question (so a
#: turn ends the way the classifier expects at least once).
CALLER_LINES: tuple[str, ...] = (
    "I would like to invest twenty thousand pounds, please.",
    "Marta Reyes.",
    "My reference is T M one zero four two.",
    "What happens if I need the money back early?",
    "About ten years, I should think.",
)

#: The adviser's side. Shorter, because the agent's audio is only synthesised on
#: the rows that measure it.
AGENT_LINES: tuple[str, ...] = (
    "Good morning. What would you like this money to be doing for you?",
    "Your capital is at risk: you could get back less than you put in.",
    "The annual management charge is 0.68 per cent a year.",
    "That is all in hand. Is there anything else I can help with?",
    #: Pinned. The committed WebRTC recordings under `fixtures/audio/transport/`
    #: were captured sending *this* clip over a real room, and the file-ladder
    #: side of that comparison re-perturbs the same audio offline. Change the
    #: wording and the two sides stop measuring the same sound, so the transport
    #: verdict silently loses its baseline. See docs/AUDIO_TRANSPORT.md.
    "What date were you thinking of?",
)


def _engine(name: str, *, voice: str | None = None) -> Any:
    if name == "say":
        return SystemSayTTS(voice=voice) if voice else SystemSayTTS()
    if name == "kokoro":
        return KokoroTTS()
    raise SystemExit(f"unknown engine {name!r}; choose 'say' or 'kokoro'")


def clip_is_unchanged(path: Path, audio: Any, sample_rate: int) -> bool:
    """True when the committed file already decodes to exactly this audio.

    Checked rather than assumed so that re-running the recorder does not rewrite
    every clip with byte-different but identical-sounding output, which would put
    a megabyte of noise into a pull request that changed one line.
    """
    if not path.is_file():
        return False
    try:
        decoded, rate = read_audio(path)
    except Exception:  # noqa: BLE001 - an unreadable clip is a clip to rewrite
        return False
    return rate == sample_rate and audio_digest(decoded, rate) == audio_digest(audio, sample_rate)


def record_clips(
    caller_lines: Sequence[str],
    agent_lines: Sequence[str],
    *,
    tts: Any,
    root: Path,
    sample_rate: int,
) -> dict[str, Any]:
    """Synthesise each line once, write it as Ogg Opus, and build the manifest.

    A clip whose committed bytes already decode to this audio is left untouched,
    and clips the new manifest does not reference are pruned at the end rather
    than deleted up front — so a failure part-way through leaves the previous
    fixture set intact instead of half of it.
    """
    clips_dir = root / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "generated_by": "scripts/make_audio_fixtures.py",
        "engine": tts.name,
        "voice": getattr(tts, "voice", None),
        "sample_rate": sample_rate,
        "format": "16 kHz mono Ogg Opus",
        "clips": {},
    }
    kept = 0
    pinned = 0
    for role, lines in (("caller", caller_lines), ("agent", agent_lines)):
        for line in lines:
            key = text_digest(line, sample_rate)
            filename = f"{role}-{key[:12]}{CLIP_SUFFIX}"
            path = clips_dir / filename
            if line in PINNED_LINES and path.is_file():
                # Never re-synthesise a pinned clip. `say` is not bit-stable
                # across macOS releases, and the committed WebRTC recordings were
                # captured sending exactly these bytes: re-making the audio would
                # leave the transport comparison measuring two different sounds
                # while still reporting a verdict.
                pinned += 1
            else:
                result = tts.synthesise(line, sample_rate=sample_rate)
                if clip_is_unchanged(path, result.audio, sample_rate):
                    kept += 1
                else:
                    write_audio(path, result.audio, sample_rate)
            decoded, _rate = read_audio(path)
            manifest["clips"][key] = {
                "file": f"clips/{filename}",
                "role": role,
                "text": line,
                "voice": getattr(tts, "voice", None),
                "sample_rate": sample_rate,
                "samples": int(decoded.size),
                "duration_s": round(decoded.size / sample_rate, 4),
                "bytes": path.stat().st_size,
                "pcm16_bytes": int(decoded.size) * 2,
            }
    referenced = {Path(entry["file"]).name for entry in manifest["clips"].values()}
    for stale in sorted(clips_dir.glob(f"*{CLIP_SUFFIX}")):
        if stale.name not in referenced:
            stale.unlink()
    manifest["clips_unchanged"] = kept
    manifest["clips_pinned"] = pinned
    (root / ClipManifest.FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return manifest


def record_cassette(*, root: Path) -> dict[str, Any]:
    """Key every committed clip by the audio that reads back off disk.

    The entry is the line that was synthesised, recorded against the digest of the
    decoded audio — so `RecordedSTT` answers for exactly the sound the replay path
    produces, and raises rather than guessing for any other sound. That strictness
    is the whole value of a cassette: it cannot quietly answer for audio it has
    never heard.
    """
    manifest = ClipManifest.load(root)
    entries: dict[str, dict[str, Any]] = {}
    for entry in manifest.clips.values():
        decoded, rate = read_audio(root / entry["file"])
        entries[audio_digest(decoded, rate)] = {
            "text": entry["text"],
            "engine": REFERENCE_ENGINE,
            "provenance": "reference",
            "language": "en",
            "spoken": entry["text"],
            "role": entry["role"],
        }
    document = {
        "generated_by": "scripts/make_audio_fixtures.py",
        "keyed_by": (
            "sha256 of the clip as 16-bit PCM after the Opus round trip, per "
            "lab.voice.engines.base.audio_digest"
        ),
        "entries": entries,
    }
    (root / TranscriptCassette.FILENAME).write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return document


def write_readme(manifest: dict[str, Any], *, root: Path) -> None:
    """A note beside the fixtures saying what they are and how to remake them."""
    clips = manifest["clips"]
    total_bytes = sum(int(c["bytes"]) for c in clips.values())
    total_seconds = sum(float(c["duration_s"]) for c in clips.values())
    lines = [
        "# The committed sample clips",
        "",
        f"Recorded {date.today().isoformat()} by `scripts/make_audio_fixtures.py`.",
        "",
        f"- **{len(clips)} clips**, {total_seconds:.2f} s of audio, {total_bytes:,} bytes on disk.",
        f"- Engine `{manifest['engine']}`, {manifest['sample_rate']} Hz mono, {manifest['format']}.",
        "- Lines, not conversations. The audio tier's subject is the audio layer;",
        "  the conversational evidence is `fixtures/audio/spoken_call/`.",
        "",
        "Remake them (macOS, no keys, no network):",
        "",
        "```bash",
        "make audio-fixtures",
        "```",
        "",
        "`manifest.json` indexes the clips by the digest of their text; ",
        "`transcripts.json` maps the digest of each clip's **decoded** audio to the ",
        "line that produced it, which is what makes `RecordedSTT` strict.",
        "",
    ]
    (root / "audio_fixtures.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--engine", default="say", choices=("say", "kokoro"))
    parser.add_argument("--voice", default=None, help="engine voice; the engine's default if unset")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    args = parser.parse_args(argv)

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    tts = _engine(args.engine, voice=args.voice)

    manifest = record_clips(
        CALLER_LINES, AGENT_LINES, tts=tts, root=root, sample_rate=args.sample_rate
    )
    cassette = record_cassette(root=root)
    write_readme(manifest, root=root)

    clips = manifest["clips"]
    print(f"wrote {len(clips)} clip(s) to {root}/clips  (engine {manifest['engine']})")
    print(f"  {manifest['clips_unchanged']} unchanged, {len(clips) - manifest['clips_unchanged']} rewritten")
    print(f"  {sum(int(c['bytes']) for c in clips.values()):,} bytes, "
          f"{sum(float(c['duration_s']) for c in clips.values()):.2f} s of audio")
    print(f"  cassette: {len(cassette['entries'])} entries keyed on decoded audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
