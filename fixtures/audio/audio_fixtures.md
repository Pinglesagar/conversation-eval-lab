# The committed sample clips

Recorded 2026-09-16 by `scripts/make_audio_fixtures.py`.

- **10 clips**, 25.63 s of audio, 95,177 bytes on disk.
- Engine `tts:system-say/Samantha`, 16000 Hz mono, 16 kHz mono Ogg Opus.
- Lines, not conversations. The audio tier's subject is the audio layer;
  the conversational evidence is `fixtures/audio/spoken_call/`.

Remake them (macOS, no keys, no network):

```bash
make audio-fixtures
```

`manifest.json` indexes the clips by the digest of their text; 
`transcripts.json` maps the digest of each clip's **decoded** audio to the 
line that produced it, which is what makes `RecordedSTT` strict.
