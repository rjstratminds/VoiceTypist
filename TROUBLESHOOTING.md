# Troubleshooting

## Service Is Running But The Tray Is Missing

Check the service logs:

```bash
journalctl --user -u voicetypist.service -n 50 --no-pager
```

If you see:

- `No working X11 display detected`
- `Tray unavailable: Bad display name`

then the service does not have the correct desktop session environment.

Verify the user `systemd` environment:

```bash
systemctl --user show-environment | rg '^(DISPLAY|XAUTHORITY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS)='
```

The repo service file is designed to inherit those variables through `PassEnvironment`.

If you are on GNOME/X11 and clicking the icon does nothing, check that you are running a build with the GTK tray path. The older `pystray` X11 backend does not expose a useful menu.

If you are on Plasma/Wayland, look for:

- `Tray icon started (AppIndicator)`

If the icon is still missing even though that line appears, the remaining issue is usually desktop-side tray presentation rather than VoiceTypist failing to publish a status item.

## Hotkey Does Not Work

VoiceTypist tries hotkey backends in this order:

1. `evdev`
2. `pynput`

If `evdev` fails, common causes are:

- no readable `/dev/input/event*` devices
- missing permissions
- the selected input device does not expose Right Alt

If `pynput` fails, common causes are:

- no usable X11 session
- missing `DISPLAY`
- bad `XAUTHORITY`

Inspect logs:

```bash
journalctl --user -u voicetypist.service -n 100 --no-pager
```

Important behavior note:

- the tray no longer starts or stops recording
- recording is still driven by the hotkey path
- the tray is used for backend switching between Whisper and Parakeet
- double `Right Ctrl` is used to cancel the active recording without transcription

## Dictation Uses The Wrong Microphone

Set `audio_source` in:

```text
~/.config/voicetypist-linux/config.yaml
```

Example:

```yaml
audio_source: alsa_input.usb-Focusrite_Scarlett-00.mono-fallback
```

List available PulseAudio or PipeWire sources with your preferred audio toolchain, then restart VoiceTypist.

## Transcript Is Empty

Empty output usually means one of these:

- the wrong `audio_source` is selected
- the microphone captured silence
- the ASR backend path is wrong
- the ASR backend itself failed

For `whisper.cpp`, verify:

- `whisper_bin` points to a working `whisper-cli`
- `model` points to an existing model file

Run those paths manually if needed.

## Transcript Exists But Nothing Is Typed

There are two distinct failure classes:

1. transcription never completed
2. transcription completed, but X11 injection failed

Recent builds log both stages explicitly.

If the logs show:

- `Final transcript: ...`
- `Refined transcript: ...`

then ASR finished and the problem is downstream of transcription.

If the logs show:

- `xdotool type failed: ...`
- `ydotool type failed: ...`

then the failure is in the selected text injection backend rather than transcription.

## Recording HUD Does Not Appear

The live waveform HUD is tied to the GTK/X11 path.

Check:

- the service is running in an X11 session
- the logs show `Tray icon started (GTK)`
- the logs do not show GTK tray initialization failures

If the service falls back away from the GTK tray path, the recording HUD will also be unavailable.

## Gemini Refinement Is Not Happening

Gemini refinement only runs when `GOOGLE_API_KEY` is present in the process environment.

If the service is running under `systemd --user`, verify the variable is available to the service.

Also note:

- the current code reads `rewrite.system_prompt` from YAML and maps it into `rewrite_system_prompt`
- without `GOOGLE_API_KEY`, the configured Gemini model and prompt are ignored

## Text Is Not Typed Into The Focused App

Typing may be done with either:

```bash
ydotool type ...
```

or:

```bash
xdotool type --clearmodifiers ...
```

If nothing is typed:

- confirm `type_backend` in `~/.config/voicetypist-linux/config.yaml`
- if using `ydotool`, confirm `ydotoold.service` is running and `~/.ydotool_socket` exists
- if using `xdotool`, confirm VoiceTypist can reach X11 and the target app accepts X11 synthetic input
- test the backend manually in the same session

GNOME-specific note:

- the service can be running correctly while still missing the desktop session environment it needs for tray and typing
- confirm `DISPLAY` and `XAUTHORITY` are correct in the running process or inherited user `systemd` environment

Plasma-specific note:

- if `xdotool` triggers a KDE remote-control prompt, switch to `type_backend: ydotool`
- `ydotool` is the tighter fix than pre-authorizing KDE remote-desktop portal permissions

## ydotool Does Not Work

Check:

```bash
systemctl --user status ydotoold.service --no-pager
ls -l ~/.ydotool_socket
```

Common causes:

- `ydotoold.service` is not running
- the socket path does not match the one VoiceTypist expects
- the user lacks access to `/dev/uinput`

If needed, test directly:

```bash
YDOTOOL_SOCKET=$HOME/.ydotool_socket ydotool type "test"
```

## Parakeet Backend Fails To Start

The `parakeet` path requires dependencies beyond `requirements.txt`.

At minimum, the environment must provide:

- `torch`
- `nemo.collections.asr`

If those imports fail, install the NeMo and PyTorch stack into the active virtual environment.

## Parakeet Uses CPU Instead Of GPU

Start with:

```bash
./venv/bin/python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.version.cuda); print(torch.cuda.device_count())"
```

If you see `+cpu` or `False`, then Parakeet cannot use the GPU regardless of the rest of the system.

On this ARM64/NVIDIA setup, `pip install torch` from the default index produced a CPU-only build. A CUDA-enabled ARM64 wheel from the official PyTorch CUDA index was required.

If CUDA is still unavailable after installing a CUDA wheel:

- verify `nvidia-smi` works on the host
- verify a real CUDA tensor operation succeeds
- verify the check is performed outside restrictive sandboxes

Example:

```bash
./venv/bin/python - <<'PY'
import torch
print(torch.cuda.is_available())
print(torch.randn(2, 2, device="cuda"))
PY
```

If `torch.cuda.is_available()` is `True` but VoiceTypist still feels slow on the first dictation, that is usually model-load latency rather than a GPU failure.

## Parakeet Fails With Hugging Face Cache Permission Errors

Typical error:

```text
PermissionError: ... ~/.cache/huggingface/hub/models--nvidia--parakeet-tdt-0.6b-v3
```

Current builds avoid this by forcing a dedicated cache path:

```text
~/.cache/voicetypist-hf
```

If you still see the old path in logs, the running service has not picked up the newer code yet.

## First Dictation Is Slow

This is expected behavior for Parakeet more than for Whisper.

Reason:

- the Parakeet model loads lazily on the first transcription after service start

What helps:

- keep the service running
- use Whisper if you care more about startup responsiveness than model complexity
- get CUDA working if you want Parakeet to become competitive on latency after model load

## Transcription History Is Empty

The tray history is currently in-memory only.

That means:

- it stores the last 10 finalized transcriptions for the running service
- it is cleared when `voicetypist.service` restarts
- cancelled recordings are not added
