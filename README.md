# VoiceTypist Linux

**VoiceTypist, inspired by VoiceInk, maintained by Richard Jhang | [@rjstratminds](https://github.com/rjstratminds)**

VoiceTypist is a Linux-first dictation project inspired by [VoiceInk](https://github.com/Beingpax/VoiceInk).

It keeps the same quick toggle-to-dictate workflow, but targets Linux desktops with PipeWire or PulseAudio, desktop tray integration, configurable text injection, and a global hotkey.

## What It Does

- Double-tap Right Alt to start dictation
- Double-tap Right Alt again to stop
- Capture audio in memory instead of writing session audio to `/tmp`
- Run a final transcription pass after the session ends
- Optionally refine the transcript with Gemini
- Type the final text into the focused app with `ydotool` or `xdotool`
- Show tray state as `Idle`, `Listening`, or `Refining`
- Let you switch between `whisper.cpp` and Parakeet from the tray menu
- Show a live bottom-center recording HUD with waveform-style level bars while dictating
- Let you copy from the last 10 transcriptions through the tray menu

## Current Platform Assumptions

VoiceTypist currently targets Linux desktop sessions with:

- `evdev` access for hotkeys, or X11 access for `pynput` fallback
- PipeWire or PulseAudio audio capture through `ffmpeg`
- A working tray implementation if you want status icons
- Optional `ydotool` for Wayland-friendly text injection
- Optional CUDA-enabled `whisper.cpp` on NVIDIA hosts

The same codebase is intended to run across both GNOME/X11-style hosts and Plasma/Wayland hosts. In practice that means the app auto-selects different tray and typing backends depending on what the machine provides.

## Pipeline

`PipeWire/PulseAudio source -> ffmpeg PCM stream -> in-memory buffer -> ASR -> optional Gemini refine -> ydotool or xdotool type`

Supported ASR backends:

- `whisper.cpp`
- NVIDIA NeMo Parakeet: `nvidia/parakeet-tdt-0.6b-v3`

Recommended default:

- `whisper.cpp` for simple setup and fast first-use latency
- Parakeet when you want higher-end ASR and are willing to install the heavier NeMo and CUDA stack

## Quick Start

1. Install system packages:
   `ffmpeg`, `python3`, `python3-venv`, and desktop/input dependencies required by your distro.
   Typical typing packages are `ydotool` for Wayland-capable hosts and `xdotool` for X11 hosts.
2. Create a virtual environment and install Python requirements:
   `python3 -m venv venv`
   `./venv/bin/pip install -r requirements.txt`
3. Configure ASR in `~/.config/voicetypist-linux/config.yaml`.
4. Run the app manually:
   `./venv/bin/python voicetypist_linux.py`
5. If that works, install the user service from [`systemd/voicetypist.service`](systemd/voicetypist.service).

Full setup instructions are in [INSTALL.md](INSTALL.md).

## Configuration

Primary config path:

- `~/.config/voicetypist-linux/config.yaml`

Legacy config path still recognized:

- `~/.config/voiceink-linux/config.yaml`

Current keys:

- `asr`: `whisper` or `parakeet`
- `model`: path to the `whisper.cpp` model
- `whisper_bin`: path to `whisper-cli`
- `whisper_threads`: thread count passed to `whisper-cli`
- `type_backend`: `auto`, `ydotool`, or `xdotool`
- `parakeet_model`: NeMo model name
- `gemini_model`: Gemini model ID used when `GOOGLE_API_KEY` is present
- `rewrite_system_prompt`: prompt used for Gemini transcript refinement
- `audio_source`: PulseAudio/PipeWire source name, default `default`

Example:

```yaml
asr: whisper
model: ~/whisper.cpp/models/ggml-small.en.bin
whisper_bin: ~/whisper.cpp/build/bin/whisper-cli
whisper_threads: 8
type_backend: auto
parakeet_model: nvidia/parakeet-tdt-0.6b-v3
gemini_model: gemini-2.5-flash-lite
rewrite_system_prompt: |
  Refine this transcript to sound native and easy-to-understand in English.
  Only give the refined text.
audio_source: default
```

If `GOOGLE_API_KEY` is not set, Gemini refinement is skipped and the raw transcript is typed directly.

Older nested YAML layouts are still partially supported. In particular, the current code can still read:

- `whisper.binary`
- `whisper.model`
- `audio.input_device`
- `rewrite.model`
- `rewrite.system_prompt`

## Service

The repo includes a user service at [systemd/voicetypist.service](systemd/voicetypist.service).

The service is intended to inherit your active desktop session environment:

- `DISPLAY`
- `WAYLAND_DISPLAY`
- `XAUTHORITY`
- `XDG_CURRENT_DESKTOP`
- `XDG_SESSION_TYPE`
- `DBUS_SESSION_BUS_ADDRESS`
- `GOOGLE_API_KEY`

Install and enable it with:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/voicetypist.service ~/.config/systemd/user/voicetypist.service
systemctl --user daemon-reload
systemctl --user enable --now voicetypist.service
```

Logs:

```bash
journalctl --user -u voicetypist.service -f
```

For secret management, prefer a user drop-in with an `EnvironmentFile` rather than putting API keys into the repo or shell startup files. See [INSTALL.md](INSTALL.md).

## Performance

Backend behavior is materially different:

- `whisper.cpp` is usually the fastest path to a responsive setup on CPU
- a CUDA-enabled `whisper.cpp` build is the practical speed path on NVIDIA hosts when you do not want the heavier Parakeet runtime
- Parakeet has a heavier first-use cost because the model loads lazily on the first transcription
- Parakeet becomes much more competitive when CUDA is working and the model is already resident in memory

In other words:

- fastest setup: Whisper on CPU
- best practical NVIDIA path with minimal app changes: Whisper on CUDA
- best chance of faster high-end inference: Parakeet on GPU

## How Input Works

Hotkey backends are tried in this order:

1. `evdev`
2. `pynput`

`evdev` is preferred because it can work without relying on X11 keyboard hooks, but it requires readable input devices. If that is not available, VoiceTypist falls back to `pynput`, which depends on a working X11 session.

Current hotkey behavior:

- double `Right Alt`: start dictation, or finish and transcribe if already recording
- double `Right Ctrl`: cancel the current recording without transcription

Typing output backends:

- `ydotool` when `type_backend` is `ydotool` or `auto` and the daemon socket is available
- `xdotool type --clearmodifiers` as fallback when `type_backend` is `auto` or explicitly `xdotool`

Recommended host choices:

- GNOME/X11-style hosts: `type_backend: xdotool` is acceptable
- Plasma/Wayland hosts: `type_backend: ydotool` avoids KDE remote-control portal prompts

## Tray Behavior

Tray backend selection is environment-dependent:

- AppIndicator on Plasma/Wayland or other desktops that expose StatusNotifier/AppIndicator support
- GTK `StatusIcon` as fallback on GNOME/X11-style setups
- `pystray` only as a last fallback

Current tray menu actions:

- `Use Whisper`
- `Use Parakeet`
- `Transcription History`
- `Quit`

Selecting a backend:

1. updates `~/.config/voicetypist-linux/config.yaml`
2. restarts `voicetypist.service`

The tray is not used to start or stop dictation. Recording is still controlled by the global hotkey.

`Transcription History` shows the last 10 finalized transcriptions. Each entry exposes a `Copy` action so you can quickly move previous dictated text to the clipboard.

## Recording HUD

While VoiceTypist is actively recording, it shows a small bottom-center HUD overlay.

The overlay is intended to answer two questions immediately:

- is recording actually active?
- is the incoming level strong enough?

Current behavior:

- appears only while recording
- updates live from the incoming PCM stream
- shows a compact waveform-style level display
- hides automatically when recording stops or is cancelled

## Documentation

- [INSTALL.md](INSTALL.md)
- [LINUX.md](LINUX.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [systemd/voicetypist.service](systemd/voicetypist.service)

## Limitations

- tray behavior still depends on your desktop environment and tray support
- `ydotool` requires a running `ydotoold` daemon and access to `/dev/uinput`
- `xdotool` still depends on X11 and may trigger remote-control prompts under Plasma/Wayland
- The app transcribes after capture ends; it is not a streaming partial-transcript UI
- Parakeet requires additional heavyweight Python dependencies not listed in `requirements.txt`
- The first Parakeet transcription after service start is slower because model loading is lazy
- GPU-enabled Parakeet requires a CUDA-enabled ARM64 PyTorch build on this machine

## Cross-Machine Setup

The current code is intended to stay shared across machines such as:

- `rj-spark`: GNOME-oriented DGX Spark environment
- `rj-blade`: Plasma 6 / Wayland / NVIDIA laptop environment

The generalization point is config and local packaging, not machine-specific forks.

Shared code handles:

- tray backend auto-selection
- typing backend selection
- Whisper thread configuration
- CUDA-capable Whisper binary paths

Machine-specific configuration should stay in `~/.config/voicetypist-linux/config.yaml`, especially:

- `whisper_bin`
- `whisper_threads`
- `type_backend`
- `audio_source`

## Inspiration

VoiceTypist is explicitly inspired by VoiceInk's interaction model and product direction. This repository is not a macOS build of VoiceInk. It is a Linux-focused implementation intended to make that style of dictation feel natural on Linux.

## License

This project remains licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).
