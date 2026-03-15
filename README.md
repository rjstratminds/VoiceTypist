# VoiceTypist

VoiceTypist is a Linux-first dictation fork inspired by [VoiceInk](https://github.com/Beingpax/VoiceInk).

VoiceInk is built for macOS. VoiceTypist keeps the same fast voice-to-text spirit, but is intended for Linux desktops with X11, PipeWire/PulseAudio, and global hotkey-driven dictation.

## What This Fork Adds

- Linux toggle dictation using double Right Alt
- In-memory audio capture instead of `/tmp` session files
- Final-pass transcription and refinement when a dictation session ends
- Tray feedback for `Listening`, `Refining`, and `Idle`
- Linux-oriented input and text injection using `evdev` or `pynput` plus `xdotool`

## Linux Flow

`PipeWire/PulseAudio source -> in-memory PCM buffer -> ASR -> optional Gemini refine -> type into focused X11 window`

The current Linux package supports:

- `whisper.cpp`
- NVIDIA Parakeet v3 integration path

## Documentation

- [Linux Notes](LINUX.md)
- [Repo Systemd Unit](systemd/voicetypist.service)

## Inspiration

VoiceTypist is explicitly inspired by VoiceInk's interaction model and product direction. This repository is not a macOS build of VoiceInk; it is a Linux-focused fork intended to make that style of dictation feel natural on Linux.

## License

This project remains licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).
