# VoiceTypist Linux

This directory contains the Linux dictation package for VoiceTypist.

VoiceTypist is inspired by VoiceInk's product feel and interaction model, but this fork is intended for Linux rather than macOS.

## Current Behavior

- Double-tap Right Alt to start a dictation session.
- While the microphone is live, the tray icon shows `Listening` in red.
- Double-tap Right Alt again to end the session.
- The app then runs ASR on the in-memory recording, optionally refines the transcript with Gemini, and types the final text into the focused X11 window.
- During post-processing, the tray icon shows `Refining` in blue.
- When idle, the tray icon shows `Idle` in gray.

## Audio Path

The Linux clone now captures audio in memory instead of writing a session recording to `/tmp`.

Pipeline:

`PipeWire/PulseAudio source -> ffmpeg stdout PCM stream -> in-memory buffer -> ASR -> Gemini refine -> xdotool type`

This keeps the session flow close to VoiceInk toggle mode while staying Linux-native.

Supported ASR backends:

- `whisper.cpp`
- `nvidia/parakeet-tdt-0.6b-v3`

For `whisper.cpp`, the script satisfies the CLI's file-path requirement with an anonymous in-memory file descriptor rather than a temporary recording file on disk.

## Hotkey Backend

Hotkey detection prefers `evdev`. If no readable input device is available, the app falls back to `pynput` against the active X11 session.

Typical startup logs:

- `Using X11 display :1`
- `Tray icon started`
- `Hotkey backend: evdev on /dev/input/...`
- `Hotkey backend: pynput`

## Config

Config lives at `~/.config/voicetypist-linux/config.yaml`.

The script will also read the legacy `~/.config/voiceink-linux/config.yaml` path if it exists.

Current keys:

- `model`
- `whisper_bin`
- `parakeet_model`
- `gemini_model`
- `audio_source`
- `asr`

`audio_source` defaults to `default`. If transcription is empty or clearly using the wrong microphone, point this at a specific PipeWire/PulseAudio source name.

## Notes

- The tray icon relies on X11 desktop tray support.
- Text injection currently uses `xdotool`.
- The script is optimized for toggle dictation: capture first, transcribe and refine after the session ends.
