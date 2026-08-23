"""The digest cache: what makes a re-run of the paid voice suite cost nothing.

WHAT THIS DEMONSTRATES
----------------------
The binding cost of a cloud voice suite is not time and it is not tokens. It is
the vendor's character allowance, which for a free ElevenLabs account is 10,000
characters that **do not renew until the monthly reset**. A suite that re--
synthesises its corpus on every run is a suite you can execute about four times
before it stops working, and the fourth run fails in the middle, leaving half a
corpus. That is not a cost problem, it is a *reliability* problem: an eval you
cannot re-run is an eval you cannot trust, because you can never check a result
by repeating it.

So synthesis in this package is content-addressed. Every clip is keyed by a
digest of everything that can change the samples, and an unchanged line is never
paid for twice — not across runs, not across checkouts, not across machines.

TWO LAYERS, AND WHY THE ORDER MATTERS
-------------------------------------
    committed   `fixtures/audio/tts_cache/` — in the repository, read-only here.
    scratch     `~/.cache/lab-audio/tts-cache/` — outside it, writable.

Reads try committed first, then scratch. Writes go to scratch only.

That asymmetry is the whole design. The repo's cardinal rule is that a fresh
clone with every key unset must pass `pytest`, which means the paid path has to
ship its evidence. If the cache lived only in `~/.cache` — as the interrupted
first draft of `elevenlabs_tts.py` had it — then a reviewer cloning this
repository would get a cache miss on every line, and the "re-runs are free"
claim would be true only on the machine that happened to synthesise them. The
committed layer is what makes the claim portable. The scratch layer is what keeps
a working session's experiments out of `git status`.

Writes never go to the committed layer because a fixture entering the repository
should be a decision with a diff attached, not a side effect of running a test.
`promote()` is the deliberate step, and it is what `make audio-fixtures` calls.

WHY LOSSLESS WAV AND NOT OPUS
-----------------------------
`lab.voice.engines.audiofile` makes a good case for committing Opus: it is about
a tenth the size and the committed *conversation* clips use it. This cache must
not. Opus is lossy, so a clip that goes through it comes back with different
samples, which means a different `audio_digest` — and `audio_digest` is the key
of the STT transcript cassette. Compressing this cache would silently break every
recorded transcript in the suite, and it would break them by *missing*, so the
symptom would be a surprise bill rather than an error. Sixteen-bit PCM at 16 kHz
is 32 kB per second; a corpus of short utterances is a few megabytes, and that is
the right thing to spend to keep two fixture stores in agreement.

WHAT IS IN THE KEY
------------------
Text, voice, model, output format, and normalisation mode. The first four are
obvious. Normalisation is in the key because it changes the audio and not only
the metadata: measured on the live API, the same sentence at
`apply_text_normalization="on"` and `"auto"` came back 3.808 s and 3.854 s long.
A cache that ignored the mode would serve one recording under the other's
settings, and the spoken-form reference committed beside it would then describe
audio nobody synthesised.

Nothing about the *account* is in the key. Two developers with different keys
synthesising the same line with the same settings should share a cache entry;
that is the point.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from lab.voice.engines.audiofile import read_audio, write_audio
from lab.voice.engines.base import Audio

__all__ = [
    "CACHE_DIR_ENV_VAR",
    "COMMITTED_CACHE_DIR",
    "SCRATCH_CACHE_DIR",
    "CacheEntry",
    "ClipCache",
    "clip_cache_key",
]

#: Overrides the *scratch* layer only. The committed layer is a property of the
#: checkout, not of the environment, and letting a variable move it would make
#: "does this clone contain its fixtures?" unanswerable without reading the shell.
CACHE_DIR_ENV_VAR: str = "LAB_TTS_CACHE_DIR"

#: In the repository. Resolved from this file rather than the working directory,
#: so the cache is found whether the suite is run from the repo root, from an
#: editable install, or from a test that changed directory.
COMMITTED_CACHE_DIR: Path = (
    Path(__file__).resolve().parents[3] / "fixtures" / "audio" / "tts_cache"
)

#: Outside the repository, so a working session cannot commit clips by accident
#: and `git clean` cannot throw away a paid-for cache.
SCRATCH_CACHE_DIR: Path = Path.home() / ".cache" / "lab-audio" / "tts-cache"


def clip_cache_key(
    *,
    text: str,
    voice: str,
    model: str,
    output_format: str,
    normalisation: str = "on",
) -> str:
    """Content digest of everything that can change the samples.

    A 32-hex-character prefix of a SHA-256, which is 128 bits — far past the
    point where a collision in a corpus of a few thousand clips is worth
    thinking about, and short enough that a filename is readable in a diff.

    The literal `"clipcache-v1"` prefix is a version tag. If the key recipe ever
    changes, bumping it invalidates every entry at once, which is the correct
    behaviour: the alternative is a cache that half-answers under two recipes.
    """
    hasher = hashlib.sha256()
    hasher.update(
        "|".join(
            ["clipcache-v1", model, voice, output_format, normalisation, text]
        ).encode("utf-8")
    )
    return hasher.hexdigest()[:32]


class CacheEntry:
    """One cached clip: its samples, its rate, its sidecar, and which layer it came from.

    `layer` is carried because "the cache hit" and "the cache hit *the committed
    fixtures*" are different facts. The first says this run was free; the second
    says a fresh clone would also get it free, which is the claim the cardinal
    rule actually makes.
    """

    __slots__ = ("audio", "sample_rate", "meta", "layer", "key")

    def __init__(
        self,
        *,
        key: str,
        audio: Audio,
        sample_rate: int,
        meta: dict[str, Any],
        layer: str,
    ) -> None:
        self.key = key
        self.audio = audio
        self.sample_rate = sample_rate
        self.meta = meta
        self.layer = layer

    @property
    def spoken_text(self) -> str | None:
        """The spoken form recorded beside the audio, or None if there wasn't one.

        This is the field whose loss would be invisible. A cache that returned
        the samples and dropped the spoken form would hand back a clip whose WER
        reference quietly reverted to the caller's input string — the exact
        failure `WER_NORMALISATION.md` was written about, reintroduced by a
        caching layer rather than by the scoring code, and therefore looked for
        in the wrong file.
        """
        value = self.meta.get("spoken_text")
        return str(value) if value else None

    def __repr__(self) -> str:
        return (
            f"CacheEntry(key={self.key[:8]}…, layer={self.layer}, "
            f"{self.sample_rate}Hz, spoken={'yes' if self.spoken_text else 'no'})"
        )


class ClipCache:
    """Content-addressed clips on disk. Reads two layers, writes one.

    Counters are kept — `hits`, `misses`, `committed_hits`, `writes` — because
    "the cache worked" is a claim that should be checkable from the run's own
    output. A cache with no instrumentation is a cache you find out about from
    the invoice.
    """

    def __init__(
        self,
        *,
        committed: str | Path | None = None,
        scratch: str | Path | None = None,
    ) -> None:
        """
        Args:
            committed: Read-only layer. Defaults to the in-repo fixture directory.
                Pass a tmp_path in tests to exercise promotion without touching
                the repository.
            scratch: Writable layer. Defaults to `$LAB_TTS_CACHE_DIR`, then
                `~/.cache/lab-audio/tts-cache`.
        """
        self.committed = Path(committed) if committed is not None else COMMITTED_CACHE_DIR
        configured = scratch if scratch is not None else os.environ.get(CACHE_DIR_ENV_VAR)
        self.scratch = Path(configured) if configured else SCRATCH_CACHE_DIR
        self.hits = 0
        self.committed_hits = 0
        self.misses = 0
        self.writes = 0

    # ----------------------------------------------------------------- layout

    def _paths(self, root: Path, key: str) -> tuple[Path, Path]:
        return root / f"{key}.wav", root / f"{key}.json"

    def layers(self) -> tuple[tuple[str, Path], ...]:
        """Read order: committed first, then scratch. See the module docstring."""
        return (("committed", self.committed), ("scratch", self.scratch))

    # ------------------------------------------------------------------ reads

    def get(self, key: str) -> CacheEntry | None:
        """The cached entry for `key`, or None. Never raises on a damaged entry.

        A half-written pair — the process died between the WAV and the sidecar —
        is treated as a miss rather than an error. The next synthesis overwrites
        it. Crashing here would mean a cache that can permanently break a suite,
        which is a worse failure than paying for one line again.
        """
        for layer, root in self.layers():
            audio_path, meta_path = self._paths(root, key)
            if not (audio_path.is_file() and meta_path.is_file()):
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                audio, rate = read_audio(audio_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            self.hits += 1
            if layer == "committed":
                self.committed_hits += 1
            return CacheEntry(
                key=key, audio=audio, sample_rate=int(rate), meta=meta, layer=layer
            )
        self.misses += 1
        return None

    def __contains__(self, key: str) -> bool:
        """Presence without counting a hit or a miss.

        Separate from `get` on purpose: a report that wants to say how many lines
        of a corpus are already paid for must not perturb the counters it is
        about to print.
        """
        return any(
            self._paths(root, key)[0].is_file() and self._paths(root, key)[1].is_file()
            for _, root in self.layers()
        )

    # ----------------------------------------------------------------- writes

    def put(
        self, key: str, audio: Audio, sample_rate: int, meta: dict[str, Any]
    ) -> Path | None:
        """Store an entry in the scratch layer. Returns the WAV path, or None on failure.

        Best effort, and deliberately so: a cache write failing is a
        performance problem, not a result problem, and the samples the caller is
        holding are still perfectly good. Turning an unwritable cache directory
        into a failed evaluation would be the tail wagging the dog.
        """
        audio_path, meta_path = self._paths(self.scratch, key)
        try:
            self.scratch.mkdir(parents=True, exist_ok=True)
            write_audio(audio_path, audio, sample_rate)
            meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            return None
        self.writes += 1
        return audio_path

    def promote(self, key: str) -> bool:
        """Copy a scratch entry into the committed layer. The deliberate step.

        Returns False when there is nothing to promote or it is already there.
        Called by the fixture generator, never by the engine: a clip enters the
        repository because somebody ran the generator and read the diff, not
        because a test happened to run with a key exported.
        """
        src_audio, src_meta = self._paths(self.scratch, key)
        dst_audio, dst_meta = self._paths(self.committed, key)
        if not (src_audio.is_file() and src_meta.is_file()):
            return False
        if dst_audio.is_file() and dst_meta.is_file():
            return False
        self.committed.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_audio, dst_audio)
        shutil.copy2(src_meta, dst_meta)
        return True

    # ---------------------------------------------------------------- reports

    def committed_keys(self) -> list[str]:
        """Keys present in the committed layer, sorted. What a fresh clone gets free."""
        if not self.committed.is_dir():
            return []
        return sorted(
            path.stem
            for path in self.committed.glob("*.wav")
            if (self.committed / f"{path.stem}.json").is_file()
        )

    def describe(self) -> str:
        return (
            f"clip cache: {len(self.committed_keys())} committed in {self.committed}, "
            f"scratch {self.scratch} "
            f"(hits={self.hits} of which {self.committed_hits} committed, "
            f"misses={self.misses}, writes={self.writes})"
        )

    def __repr__(self) -> str:
        return f"ClipCache({self.describe()})"
