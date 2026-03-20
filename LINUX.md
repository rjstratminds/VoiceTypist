# VoiceTypist Linux

This repository is a Linux-focused dictation package modeled after VoiceInk's interaction style.

The implementation is intentionally simple:

- one Python entry point
- one user `systemd` service
- optional user `ydotoold` service
- local audio capture
- local or in-process ASR
- optional cloud-side transcript refinement
- configurable text injection with `ydotool` or `xdotool`
- AppIndicator or GTK tray menu depending on desktop
- bottom-center GTK recording HUD

## Runtime Flow

### 1. Startup

`voicetypist_linux.py` performs these steps:

1. Load config from `~/.config/voicetypist-linux/config.yaml`
2. Fall back to `~/.config/voiceink-linux/config.yaml` if needed
3. Build the selected ASR backend
4. Detect a usable X11 display
5. Start the tray icon if available
6. Start the global hotkey listener

### 2. Toggle Dictation

The hotkey is configurable.

Defaults:

- `hotkey_backend: auto`
- `hotkey_device: auto`
- `toggle_key: alt_r`
- `toggle_press_mode: double`
- `cancel_key: ctrl_r`
- `cancel_press_mode: double`

On start:

- the recorder launches `ffmpeg`
- raw 16 kHz mono PCM is buffered in memory
- tray state changes to `Listening`
- a bottom-center recording HUD becomes visible
- a start chime is played

On stop:

- `ffmpeg` terminates
- the in-memory PCM buffer is finalized
- tray state changes to `Refining`
- the selected ASR backend transcribes the capture
- Gemini optionally rewrites the final transcript
- the configured typing backend types the result into the focused app
- tray state returns to `Idle`

On cancel:

- the current in-memory capture is discarded
- no transcription runs
- the recording HUD hides
- tray state returns to `Idle`

Backend switching is separate from recording:

- the tray menu can switch between Whisper and Parakeet
- switching writes the config file and restarts the user service
- recording itself is still controlled by the hotkey path
- the configured cancel hotkey cancels the active recording

## Components

### Recorder

Audio capture is handled by `StreamingRecorder`, which runs:

```text
ffmpeg -f pulse -i <audio_source> -ac 1 -ar 16000 -f s16le -acodec pcm_s16le pipe:1
```

The process writes PCM bytes to stdout. VoiceTypist buffers that stream in memory until the session ends.

The same PCM stream is also sampled to drive the live level HUD while recording.

### ASR Backends

#### Whisper

The `whisper` backend shells out to `whisper-cli`.

To avoid writing a session WAV file to disk, VoiceTypist:

1. wraps PCM bytes into an in-memory WAV
2. places that WAV into an anonymous memfd
3. passes `/proc/<pid>/fd/<fd>` to `whisper-cli`

#### Parakeet

The `parakeet` backend uses NVIDIA NeMo directly from Python.

Model loading is lazy. The model is loaded only on first transcription request, then kept in memory.

Current implementation details:

- model cache is forced to `~/.cache/voicetypist-hf`
- if CUDA-enabled PyTorch is available, the model is moved to `cuda`
- otherwise it falls back to `cpu`
- NeMo `Hypothesis` objects are normalized to plain text before refinement and typing

## Transcript Refinement

If `GOOGLE_API_KEY` is set, the final transcript is sent to the configured Gemini model with the configured rewrite prompt.

Prompt source precedence:

1. `rewrite_system_prompt`
2. legacy `rewrite.system_prompt`
3. built-in default prompt

If the API key is missing or the request fails, VoiceTypist falls back to the unrefined transcript.

## Output Injection

Text output is implemented with one of these backends:

```text
ydotool type ...
```

or:

```text
xdotool type --clearmodifiers ...
```

`ydotool` is the preferred path for Plasma/Wayland-style hosts. `xdotool` remains available as fallback for X11-oriented setups.

## Tray State Model

State values used internally:

- `idle`
- `listening`
- `processing`

User-visible labels:

- `Idle`
- `Listening`
- `Refining`

Color mapping:

- idle: gray
- listening: red
- processing: blue

Tray backend selection is environment-dependent.

Preferred order:

- Ayatana AppIndicator when available
- GTK `StatusIcon`
- `pystray` fallback

Current tray menu actions:

- `Use Whisper`
- `Use Parakeet`
- `Transcription History`
- `Quit`

History behavior:

- stores the last 10 finalized transcriptions in memory
- newest first
- each item exposes a copy action
- cancelled recordings are not stored

## Hotkey Backends

### Preferred: `evdev`

`evdev` is attempted first because it can read directly from input devices and does not require an X11 keyboard hook.

Selection behavior:

- enumerate `/dev/input/event*`
- prefer devices that expose `KEY_RIGHTALT`
- prefer keyboard-like device names where possible

### Fallback: `pynput`

If `evdev` is unavailable, VoiceTypist falls back to `pynput`.

That fallback requires:

- a working X11 session
- valid `DISPLAY`
- valid `XAUTHORITY`

If both backends fail, the app stays alive but logs why hotkeys are disabled.

The current `pynput` path also handles:

- configurable toggle and cancel keys

For virtual-keyboard remappers such as Toshy/XWayKeyz:

- `evdev` may intentionally bind to the remapper’s virtual keyboard
- `toggle_key: alt_any` is often safer than assuming a specific physical right-Alt key
- `toggle_press_mode: single` gives one-press start and one-press stop
- `hotkey_device: physical` lets the `evdev` path prefer the laptop keyboard when you want to bypass the virtual remapper device

## Desktop Session Requirements

The user service should inherit these environment variables from the active desktop session:

- `DISPLAY`
- `WAYLAND_DISPLAY`
- `XAUTHORITY`
- `XDG_CURRENT_DESKTOP`
- `XDG_SESSION_TYPE`
- `DBUS_SESSION_BUS_ADDRESS`
- `GOOGLE_API_KEY` if Gemini refinement is enabled

Without them, the most common failures are:

- tray creation errors
- `pynput` startup failures
- inability to type into focused applications

## Config Reference

Current config keys:

- `asr`
- `model`
- `whisper_bin`
- `whisper_threads`
- `type_backend`
- `hotkey_backend`
- `hotkey_device`
- `parakeet_model`
- `gemini_model`
- `rewrite_system_prompt`
- `audio_source`

Notes:

- `audio_source` defaults to `default`
- older nested config layouts are still partially supported for compatibility
- if no config exists, a default one is written on first launch
- a dedicated Hugging Face cache is configured at `~/.cache/voicetypist-hf`

## Logging

Typical healthy startup log lines:

- `ASR backend: whisper (...)`
- `VoiceTypist Linux started`
- `Using X11 display :1`
- `Tray icon started`
- `Hotkey backend: evdev on /dev/input/...`

Healthy GNOME/X11 fallback logs may instead show:

- `Hotkey backend: pynput`

Tray startup may show:

- `Tray icon started (AppIndicator)`
- `Tray icon started (GTK)`

Typical healthy Parakeet logs after the first transcription include:

- `Model EncDecRNNTBPEModel was successfully restored from ...`
- `Parakeet model loaded on cuda: nvidia/parakeet-tdt-0.6b-v3`

If CUDA is not available, the same line will report `cpu` instead.

Recent builds also log:

- `Typed text (N chars) via ydotool`
- `Typed text (N chars) via xdotool`
- `xdotool type failed: ...` when typing fails

## Known Constraints

- The app performs final-pass transcription after capture ends, not live streaming transcription
- Typing is X11-based
- Tray support depends on desktop shell integration
- Parakeet setup is intentionally not bundled into the lightweight default requirements
- The first Parakeet transcription after service start is slower because model loading is lazy
