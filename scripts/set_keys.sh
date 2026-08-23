#!/usr/bin/env zsh
# Interactive, no-echo credential setup for the live tiers.
# Writes .env (gitignored). Never prints a value. Blank input = skip that key.
set -e
cd "$(dirname "$0")/.."
ENVF=".env"
touch "$ENVF"; chmod 600 "$ENVF"

put() {  # put NAME "prompt"  — silent read, replace-or-append, never echo
  local name=$1 prompt=$2 val=""
  printf '%s' "$prompt"
  read -rs val; echo
  if [[ -z "$val" ]]; then print -- "  skipped $name"; return; fi
  # strip accidental surrounding quotes/whitespace
  val="${val##[[:space:]]#}"; val="${val%%[[:space:]]#}"; val="${val#\"}"; val="${val%\"}"
  grep -v "^${name}=" "$ENVF" > "$ENVF.tmp" 2>/dev/null || : ; mv "$ENVF.tmp" "$ENVF"
  print -- "${name}=${val}" >> "$ENVF"
  print -- "  set $name (${#val} chars)"
}

print -- "Paste each key and press Enter. Input is hidden. Press Enter alone to skip."
print -- ""
put DEEPGRAM_API_KEY   "Deepgram API key            : "
put ELEVENLABS_API_KEY "ElevenLabs API key          : "
put LIVEKIT_URL        "LiveKit URL (wss://...)     : "
put LIVEKIT_API_KEY    "LiveKit API key             : "
put LIVEKIT_API_SECRET "LiveKit API secret          : "
print -- ""
chmod 600 "$ENVF"
print -- "Wrote $ENVF  (mode 600, gitignored). Names present:"
cut -d= -f1 "$ENVF" | sed 's/^/  /'
