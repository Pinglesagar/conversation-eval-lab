"""Record the WebRTC transport tier's evidence: three live sessions, three files.

WHY IT IS A SCRIPT AND NOT A TEST
---------------------------------
Because a room is real time and cannot be replayed. There is no seed that
reproduces a jitter buffer and no cassette that makes a network path behave the
same way twice, so the tier splits: this script opens real sessions and writes
down what happened, and the offline suite recomputes every reported number from
what it wrote down. A human runs this when the recordings need refreshing; CI
never does.

The same split is what keeps the cardinal rule intact. A fresh clone with every
key unset runs `pytest` and exercises the whole measurement path against the
committed recordings; nothing skips, and nothing dials out.

WHAT IT COSTS
-------------
**Zero synthesis characters.** The tier publishes a clip this repository already
committed, because what a transport does to audio has nothing to do with what the
audio says. The ElevenLabs allowance is the binding constraint on the audio suite
as a whole, so the transport tier is designed to consume none of it: the entire
cost of running this script is about three minutes of real time and a few seconds
of a LiveKit session.

WHY THIS CLIP
-------------
`agent-4133d85a3343.opus` — "What date were you thinking of?", 1.42 s, an *agent*
utterance, which is what this tier publishes: the direction under test is the
agent's audio reaching a listener.

Three properties made it the choice, and two of them are checked rather than
assumed:

    short              twelve turns of it fit inside the row's 90 s cap with the
                       inter-utterance silence the segmenter needs
    no long pause       60 ms is its longest internal quiet stretch, measured, so
                       it arrives as exactly one speech run and run k can be
                       paired with utterance k. `require_segmentable` refuses a
                       clip that fails this — two of the committed clips do, with
                       260 ms and 280 ms sentence boundaries
    already committed   no synthesis, so a re-run is free and the audio is
                       identical to the one the offline tests read

USAGE
-----
    export LAB_LIVE_TRANSPORT=1
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    python -m scripts.make_transport_fixtures            # all three rows
    python -m scripts.make_transport_fixtures --row delivery-gap
    python -m scripts.make_transport_fixtures --dry-run  # what it would do, no room
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - convenience for direct runs
    sys.path.insert(0, str(REPO_ROOT))

from lab.voice.transport.measure import (  # noqa: E402
    DEFAULT_MAX_GAP_S,
    DEFAULT_THRESHOLD_RMS,
)
from lab.voice.transport.records import TransportRecording  # noqa: E402
from lab.voice.transport.rows import load_rows  # noqa: E402
from lab.voice.transport.session import (  # noqa: E402
    LiveKitTransport,
    load_clip_frames,
    longest_quiet_ms,
    require_segmentable,
)
from lab.voice.transport.trace import trace_from_recording  # noqa: E402

#: The published clip. See the module docstring for why this one.
CLIP = REPO_ROOT / "fixtures" / "audio" / "clips" / "agent-4133d85a3343.opus"

#: Where recordings and their traces land. Both committed.
OUT_DIR = REPO_ROOT / "fixtures" / "audio" / "transport"

ROW_KEYS = ("delivery-gap", "degradation", "lifecycle")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record the three WebRTC transport rows against a live room."
    )
    parser.add_argument(
        "--row",
        action="append",
        choices=ROW_KEYS,
        help="Record one row. Repeatable. Default: all three.",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=12,
        help="Utterances for the delivery-gap row (default: 12).",
    )
    parser.add_argument(
        "--drop-every",
        type=int,
        default=4,
        help="Withhold every Nth frame in the degradation row (default: 4, so 25%%).",
    )
    parser.add_argument(
        "--drop-after-frames",
        type=int,
        default=40,
        help="Frames pushed before the lifecycle row's disconnect (default: 40).",
    )
    parser.add_argument("--out", default=str(OUT_DIR), help="Output directory.")
    parser.add_argument(
        "--suffix",
        default="",
        help=(
            "Suffix for the output filenames, e.g. --suffix -second-session. Use it to "
            "record the same row twice and commit both: a live figure whose run-to-run "
            "spread is not in the repository is a figure a reader has to take on trust."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be recorded, and check the clip, without opening a room.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows = args.row or list(ROW_KEYS)
    out = Path(args.out)

    clip = load_clip_frames(CLIP)
    quiet = longest_quiet_ms(clip, threshold_rms=DEFAULT_THRESHOLD_RMS)
    # Checked before the room is opened, not after: a clip that cannot be
    # segmented wastes a live session and reports nothing.
    require_segmentable(
        clip,
        threshold_rms=DEFAULT_THRESHOLD_RMS,
        max_gap_ms=DEFAULT_MAX_GAP_S * 1000.0,
    )

    transport = LiveKitTransport()
    print(f"clip      : {clip.name} — {clip.duration_s:.2f}s, {len(clip.frames)} frames "
          f"of {clip.frame_ms:.0f} ms, longest internal quiet {quiet:.0f} ms")
    print(f"transport : {transport.describe()}")
    print(f"rows      : {', '.join(rows)}")
    print("characters: 0 — this tier publishes a committed clip and synthesises nothing")

    if args.dry_run:
        print("\ndry run: the clip is segmentable and the row definitions load; no room opened")
        for row in load_rows():
            print(f"  {row.summary_line()}")
        return 0

    if not transport.available():
        print(
            "\nrefusing to run: " + ", ".join(transport.missing_requirements()) + " missing.\n"
            "Export LAB_LIVE_TRANSPORT=1 and the three LiveKit variables, and install "
            'the client with `pip install -e ".[transport]"`.',
            file=sys.stderr,
        )
        return 2

    out.mkdir(parents=True, exist_ok=True)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    recorded: list[TransportRecording] = []

    if "delivery-gap" in rows:
        print(f"\nrecording delivery-gap: {args.turns} turns, real time, no shortcuts...")
        recorded.append(transport.record_delivery_gap(clip, turns=args.turns))
    if "degradation" in rows:
        print(f"\nrecording degradation: control arm, then 1-in-{args.drop_every} withheld...")
        recorded.append(transport.record_degradation(clip, drop_every=args.drop_every))
    if "lifecycle" in rows:
        print(
            f"\nrecording lifecycle: drop after {args.drop_after_frames} frames, "
            "then rejoin..."
        )
        recorded.append(
            transport.record_lifecycle(clip, drop_after_frames=args.drop_after_frames)
        )

    print()
    for recording in recorded:
        name = recording.row.replace("audio-transport-", "") + args.suffix
        path = recording.write(out / f"{name}.json")
        trace = trace_from_recording(recording)
        from lab.trace.io import write_jsonl  # noqa: PLC0415 - only needed on the live path

        trace_path = write_jsonl(trace, out / "traces" / f"{name}.jsonl")
        print(recording.describe())
        print(f"  -> {path.relative_to(REPO_ROOT)} ({path.stat().st_size // 1024} KB)")
        print(f"  -> {trace_path.relative_to(REPO_ROOT)} ({len(trace)} events)")

    print(
        "\nCommit both the recordings and the traces. Every figure the tier reports is "
        "recomputed from them offline, so they are the evidence, not a cache."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
