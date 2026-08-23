#!/usr/bin/env bash
#
# setup_audio.sh — install the local speech engines the audio adapter can use.
#
# WHAT THIS DEMONSTRATES
# ----------------------
# That "optional heavy dependency" can be a first-class, reviewable part of a
# repository rather than three lines in a README that rot. Three properties:
#
#   1. It says what it will download, and how big, BEFORE downloading anything.
#      A setup script that starts a 480 MB fetch on a hotel connection without
#      asking has made a decision that was not its to make.
#   2. It is idempotent. Every step checks for its own output first, so running it
#      twice is fast and running it after a partial failure resumes.
#   3. Nothing here is required. `pip install -e ".[dev]" && pytest` passes with
#      none of it installed, because the audio path replays committed fixtures.
#      This script buys you *live* synthesis and *real* transcription — which is
#      what turns the harness's refusal to report word error rate into a number.
#
# WHAT IT INSTALLS
# ----------------
#   Kokoro-82M      Apache-2.0 text-to-speech, local, CPU-only. The default
#                   synthesiser. Piper would have been the obvious alternative
#                   and its maintained fork (OHF-Voice/piper1-gpl) is GPL-3.0,
#                   which is the wrong obligation to attach to a fixture in an
#                   MIT-licensed repo.
#   whisper.cpp     MIT speech-to-text with a real Metal backend. The correct
#                   choice on Apple Silicon: faster-whisper runs on CTranslate2,
#                   which has no Metal support and silently falls back to CPU, so
#                   the speedup that motivates choosing it does not exist there.
#
# USAGE
#   scripts/setup_audio.sh                 # show the plan, then ask
#   scripts/setup_audio.sh --yes           # no prompt (CI, or a second run)
#   scripts/setup_audio.sh --only whisper  # one component
#   scripts/setup_audio.sh --plan          # print the plan and exit
#   scripts/setup_audio.sh --uninstall     # remove what this script installed
#
# ENVIRONMENT
#   LAB_AUDIO_HOME        install root (default ~/.cache/lab-audio)
#   LAB_WHISPER_MODEL     GGML model name (default ggml-base.en.bin)
#   HF_HOME               where Kokoro's weights are cached
#
set -euo pipefail

# ---------------------------------------------------------------- configuration

AUDIO_HOME="${LAB_AUDIO_HOME:-$HOME/.cache/lab-audio}"
WHISPER_DIR="$AUDIO_HOME/whisper.cpp"
WHISPER_REPO="https://github.com/ggml-org/whisper.cpp.git"
WHISPER_BIN="$WHISPER_DIR/build/bin/whisper-cli"
WHISPER_MODEL_NAME="${LAB_WHISPER_MODEL:-ggml-base.en.bin}"
WHISPER_MODEL_DIR="$WHISPER_DIR/models"
WHISPER_MODEL="$WHISPER_MODEL_DIR/$WHISPER_MODEL_NAME"
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL_NAME"

KOKORO_REPO="hexgrad/Kokoro-82M"

# Sizes are approximate and stated as approximate. They exist so the reader can
# decide, not so the script can be precise about somebody else's release.
SIZE_WHISPER_SRC="~30 MB, shallow git clone"
SIZE_WHISPER_BUILD="~50 MB of compiled objects"
SIZE_WHISPER_MODEL="~148 MB"
SIZE_KOKORO_PKG="~2.5 GB with torch, ~20 MB without"
SIZE_KOKORO_WEIGHTS="~330 MB"

ASSUME_YES=0
ONLY=""
PLAN_ONLY=0
UNINSTALL=0

# --------------------------------------------------------------------- plumbing

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  # Print the header comment block and stop at the first line of real script, so
  # the help text cannot drift out of sync with the documentation above it.
  awk 'NR>1 && /^[^#]/ {exit} NR>1 {sub(/^# ?/, ""); print}' "$0"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    --plan)       PLAN_ONLY=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    --only)       shift; ONLY="${1:-}" ;;
    -h|--help)    usage ;;
    *)            die "unknown option $1 (try --help)" ;;
  esac
  shift
done

wants() {
  [ -z "$ONLY" ] && return 0
  [ "$ONLY" = "$1" ] && return 0
  return 1
}

# ------------------------------------------------------------------- uninstall

if [ "$UNINSTALL" -eq 1 ]; then
  step "Removing $AUDIO_HOME"
  say "This deletes the whisper.cpp checkout, its build and its models."
  say "Kokoro's pip package and its Hugging Face cache are NOT touched:"
  say "  pip uninstall kokoro         # the package"
  say "  rm -rf \"\${HF_HOME:-\$HOME/.cache/huggingface}/hub/models--hexgrad--Kokoro-82M\""
  if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Delete %s? [y/N] ' "$AUDIO_HOME"
    read -r reply
    case "$reply" in [yY]*) ;; *) say "aborted"; exit 0 ;; esac
  fi
  rm -rf "$AUDIO_HOME"
  say "removed."
  exit 0
fi

# ------------------------------------------------------------------ the plan

# Prefer the interpreter of an activated virtualenv. Installing Kokoro into the
# system python while the harness runs in a venv is the single most common way
# for this script to "succeed" and leave the engine unavailable.
if [ -n "${PYTHON:-}" ]; then
  python_bin="$PYTHON"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  python_bin="$VIRTUAL_ENV/bin/python"
else
  python_bin="python3"
  warn "no virtualenv is active; installing into $(command -v python3 || echo python3).
         If the harness runs in a venv, activate it first or set PYTHON=."
fi
have "$python_bin" || die "no $python_bin on PATH"

kokoro_installed=0
if "$python_bin" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("kokoro") else 1)' 2>/dev/null; then
  kokoro_installed=1
fi
kokoro_weights=0
hf_home="${HF_HOME:-$HOME/.cache/huggingface}"
if [ -d "$hf_home/hub/models--hexgrad--Kokoro-82M" ]; then
  kokoro_weights=1
fi
torch_installed=0
if "$python_bin" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("torch") else 1)' 2>/dev/null; then
  torch_installed=1
fi

step "Plan"
say "install root : $AUDIO_HOME"
say "python       : $($python_bin -V 2>&1) at $(command -v "$python_bin")"
say "platform     : $(uname -s) $(uname -m)"
say ""
say "Nothing below is required to run the test suite. The committed fixtures in"
say "fixtures/audio/ replay the whole audio path with no models at all. This"
say "script buys live synthesis and real transcription."
say ""

if wants kokoro; then
  say "[kokoro]  text-to-speech, Apache-2.0, local"
  if [ "$kokoro_installed" -eq 1 ]; then
    say "          package  : already installed — skip"
  elif [ "$torch_installed" -eq 1 ]; then
    say "          package  : pip install kokoro soundfile   ($SIZE_KOKORO_PKG)"
    say "                     torch is already present, so this is the small case"
  else
    say "          package  : pip install kokoro soundfile   ($SIZE_KOKORO_PKG)"
    say "                     torch is NOT present; expect the large download"
  fi
  if [ "$kokoro_weights" -eq 1 ]; then
    say "          weights  : already cached in $hf_home — skip"
  else
    say "          weights  : $KOKORO_REPO  ($SIZE_KOKORO_WEIGHTS) into $hf_home"
  fi
  say ""
fi

if wants whisper; then
  say "[whisper] speech-to-text, MIT, local, Metal-accelerated on Apple Silicon"
  if [ -d "$WHISPER_DIR/.git" ]; then
    say "          source   : already cloned — skip ($WHISPER_DIR)"
  else
    say "          source   : git clone $WHISPER_REPO   ($SIZE_WHISPER_SRC)"
  fi
  if [ -x "$WHISPER_BIN" ]; then
    say "          build    : already built — skip ($WHISPER_BIN)"
  else
    say "          build    : cmake --build   ($SIZE_WHISPER_BUILD)"
    if [ "$(uname -s)" = "Darwin" ]; then
      say "                     Metal and Accelerate are on by default on macOS"
    fi
  fi
  if [ -f "$WHISPER_MODEL" ]; then
    say "          model    : already present — skip ($WHISPER_MODEL)"
  else
    say "          model    : $SIZE_WHISPER_MODEL from Hugging Face"
    say "                     base.en, not tiny.en: tiny hallucinates on degraded"
    say "                     audio, which would make a perturbation study measure"
    say "                     the model's imagination instead of the channel."
  fi
  say ""
fi

say "After this, verify with:"
say "  python -c \"from lab.voice.engines import KokoroTTS, WhisperCppSTT as W; \\"
say "             print(KokoroTTS().describe(), KokoroTTS().available()); \\"
say "             print(W().describe(), W().available())\""
say "  python -m scripts.make_audio_fixtures --stt whisper   # re-record with real STT"

if [ "$PLAN_ONLY" -eq 1 ]; then
  exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
  printf '\nProceed? [y/N] '
  read -r reply
  case "$reply" in [yY]*) ;; *) say "aborted; nothing was downloaded"; exit 0 ;; esac
fi

mkdir -p "$AUDIO_HOME"

# --------------------------------------------------------------------- kokoro

if wants kokoro; then
  step "Kokoro-82M"
  if [ "$kokoro_installed" -eq 1 ]; then
    say "package already installed"
  else
    "$python_bin" -m pip install --upgrade "kokoro>=0.9" soundfile
  fi
  if [ "$kokoro_weights" -eq 1 ]; then
    say "weights already cached"
  else
    say "fetching weights into $hf_home"
    # Downloaded here, explicitly, rather than lazily on first synthesis. A model
    # fetched in the middle of an evaluation turns a measurement into a network
    # test, and KokoroTTS.available() returns False until the weights exist
    # precisely so that cannot happen by accident.
    "$python_bin" - <<PY
from huggingface_hub import snapshot_download
path = snapshot_download("$KOKORO_REPO")
print("kokoro weights at", path)
PY
  fi
fi

# -------------------------------------------------------------------- whisper

if wants whisper; then
  step "whisper.cpp"
  have git || die "git is required to fetch whisper.cpp"
  if [ ! -d "$WHISPER_DIR/.git" ]; then
    git clone --depth 1 "$WHISPER_REPO" "$WHISPER_DIR"
  else
    say "source already present"
  fi

  if [ ! -x "$WHISPER_BIN" ]; then
    have cmake || die "cmake is required to build whisper.cpp (brew install cmake)"
    # Metal and Accelerate are enabled by default in whisper.cpp's CMake on
    # Apple platforms; they are named explicitly here so the build is the same
    # whether or not that default changes upstream.
    cmake_args=(-B "$WHISPER_DIR/build" -S "$WHISPER_DIR" -DCMAKE_BUILD_TYPE=Release)
    if [ "$(uname -s)" = "Darwin" ]; then
      cmake_args+=(-DGGML_METAL=ON -DGGML_ACCELERATE=ON)
    fi
    cmake "${cmake_args[@]}"
    cmake --build "$WHISPER_DIR/build" --config Release -j
  else
    say "already built"
  fi
  [ -x "$WHISPER_BIN" ] || die "build finished but $WHISPER_BIN is missing"

  mkdir -p "$WHISPER_MODEL_DIR"
  if [ ! -f "$WHISPER_MODEL" ]; then
    say "fetching $WHISPER_MODEL_NAME"
    if have curl; then
      curl -L --fail --progress-bar -o "$WHISPER_MODEL.part" "$WHISPER_MODEL_URL"
    elif have wget; then
      wget -O "$WHISPER_MODEL.part" "$WHISPER_MODEL_URL"
    else
      die "need curl or wget to fetch the model"
    fi
    # Renamed only after a complete download, so an interrupted fetch cannot
    # leave a truncated model that loads and then transcribes nonsense.
    mv "$WHISPER_MODEL.part" "$WHISPER_MODEL"
  else
    say "model already present"
  fi
fi

# --------------------------------------------------------------------- verify

step "Verifying"
"$python_bin" - <<'PY'
from lab.voice.engines.stt import WhisperCppSTT
from lab.voice.engines.tts import KokoroTTS, SystemSayTTS

for engine in (KokoroTTS(), SystemSayTTS(), WhisperCppSTT()):
    mark = "ok " if engine.available() else "-- "
    print(f"{mark}{engine.describe()}")
PY

step "Done"
say "Re-record the fixtures with real transcripts:"
say "  python -m scripts.make_audio_fixtures --engine kokoro --stt whisper"
say ""
say "Then a word error rate is a real number instead of a refusal:"
say "  python -c \"from lab.voice.adapter import audio_wer_report, load_audio_trace; \\"
say "             print(audio_wer_report(load_audio_trace('fixtures/audio/traces/voice-noise-over-party-size.jsonl')).describe())\""
