# Installation

## Requirements

VoiceTypist expects a Linux desktop environment with:

- Python 3.12 or compatible Python 3
- `ffmpeg`
- `ydotool` or `xdotool`
- PipeWire or PulseAudio
- `evdev` access for hotkeys or X11 access for `pynput`

Python dependencies from `requirements.txt`:

- `PyYAML`
- `evdev`
- `pynput`
- `Pillow`
- `pystray`

The current tray implementation also uses system GTK bindings available from the host Python environment. On Plasma/Wayland hosts it can additionally use Ayatana AppIndicator bindings from the host Python environment.

For the `whisper` backend you also need:

- a built `whisper.cpp` checkout
- a `whisper-cli` binary
- a downloaded model file

For the `parakeet` backend you additionally need:

- PyTorch
- NVIDIA NeMo ASR dependencies
- enough CPU or GPU resources for the model you select

Those Parakeet packages are not installed by `requirements.txt`.

Recommended backend choices:

- choose `whisper` if you want the fastest path to a working setup
- choose `parakeet` if you want NeMo-based ASR and are willing to install a much heavier runtime

## 1. Clone And Create A Virtual Environment

```bash
git clone <your-repo-url> VoiceTypist
cd VoiceTypist
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

## 2. Install System Packages

Install the equivalents of these packages for your distro:

```bash
sudo apt install ffmpeg python3-venv xdotool ydotool
```

You may also need development headers or desktop integration packages depending on your distro and Python environment.

## 3. Configure ASR

On first start, VoiceTypist writes a default config to:

```text
~/.config/voicetypist-linux/config.yaml
```

Example config:

```yaml
asr: whisper
model: ~/whisper.cpp/models/ggml-small.en.bin
whisper_bin: ~/whisper.cpp/build/bin/whisper-cli
whisper_threads: 8
type_backend: auto
toggle_key: alt_r
toggle_press_mode: double
cancel_key: ctrl_r
cancel_press_mode: double
parakeet_model: nvidia/parakeet-tdt-0.6b-v3
gemini_model: gemini-2.5-flash-lite
audio_source: default
```

### Whisper Setup

Point these values at your local `whisper.cpp` install:

- `whisper_bin`
- `model`
- `whisper_threads`

### Whisper On NVIDIA GPU

If the host has an NVIDIA GPU and you want Whisper rather than Parakeet, build `whisper.cpp` with CUDA enabled and point `whisper_bin` at that build.

Typical build:

```bash
cmake -S ~/whisper.cpp -B ~/whisper.cpp/build-cuda -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build ~/whisper.cpp/build-cuda --config Release -j "$(nproc)" --target whisper-cli
```

Then set:

```yaml
whisper_bin: ~/whisper.cpp/build-cuda/bin/whisper-cli
```

Verify with:

```bash
ldd ~/whisper.cpp/build-cuda/bin/whisper-cli | rg 'ggml-cuda|cublas|cudart'
```

### Parakeet Setup

Set:

```yaml
asr: parakeet
parakeet_model: nvidia/parakeet-tdt-0.6b-v3
```

Then install the required NeMo and PyTorch stack in the same virtual environment.

### Parakeet On CPU

If you install NeMo against a CPU-only PyTorch build, Parakeet will work, but first-use latency will usually be noticeably worse than `whisper.cpp`.

### Parakeet On NVIDIA GPU

On this repository's current ARM64/NVIDIA setup, Parakeet only became GPU-capable after replacing the default CPU-only PyTorch wheel with an official CUDA-enabled ARM64 wheel.

Example verification:

```bash
./venv/bin/python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.version.cuda)"
```

On the current machine, the working installation path was:

```bash
./venv/bin/python -m pip install --force-reinstall --no-cache-dir --index-url https://download.pytorch.org/whl/cu130 torch==2.10.0
./venv/bin/python -m pip install setuptools==82.0.1 fsspec==2024.12.0
```

Important caveats:

- default `pip install torch` produced a CPU-only build here
- the CUDA wheel is large and pulls in substantial NVIDIA user-space packages
- PyTorch may warn if your GPU architecture is newer than the officially listed maximum supported capability for that wheel
- verify a real CUDA operation before assuming Parakeet is actually using the GPU

Example CUDA smoke test:

```bash
./venv/bin/python - <<'PY'
import torch
print(torch.cuda.is_available())
a = torch.randn(2, 2, device="cuda")
b = torch.randn(2, 2, device="cuda")
print((a @ b).device)
PY
```

## 4. Optional Gemini Refinement

If `GOOGLE_API_KEY` is available in the environment, VoiceTypist sends the final transcript to Gemini and types the refined result.

Without that variable, the app skips refinement.

Example:

```bash
export GOOGLE_API_KEY=your-key
```

If you run the app via `systemd --user`, use a user drop-in and environment file rather than relying on shell startup files.

Recommended pattern:

1. Create a secret file:

```bash
printf 'GOOGLE_API_KEY=your-key\n' > ~/.config/voicetypist.env
chmod 600 ~/.config/voicetypist.env
```

2. Add a drop-in:

```bash
mkdir -p ~/.config/systemd/user/voicetypist.service.d
cat > ~/.config/systemd/user/voicetypist.service.d/env.conf <<'EOF'
[Service]
EnvironmentFile=%h/.config/voicetypist.env
EOF
```

3. Reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart voicetypist.service
```

The current service unit also passes `GOOGLE_API_KEY` through with `PassEnvironment`, but a drop-in `EnvironmentFile` is the safer persistent configuration.

## 5. Test Manually

Run the app directly first:

```bash
./venv/bin/python voicetypist_linux.py
```

Expected startup logs look like:

- `VoiceTypist Linux started`
- `Using X11 display :1`
- `Tray icon started`
- `Hotkey backend: evdev on /dev/input/...`

Or, if `evdev` is not available:

- `Hotkey backend: pynput`

On GNOME/X11-style hosts, you may instead see:

- `Tray icon started (GTK)`

On Plasma/Wayland-style hosts with AppIndicator support, you may see:

- `Tray icon started (AppIndicator)`

If you use Toshy, XWayKeyz, or another keyboard remapper that exposes a virtual keyboard device, `evdev` may bind to that device instead of the laptop’s physical keyboard. That is fine as long as the remapped key still appears to Linux as the configured `toggle_key`.

Useful hotkey configs:

```yaml
toggle_key: alt_any
toggle_press_mode: single
cancel_key: ctrl_r
cancel_press_mode: double
```

If `type_backend` is `ydotool`, also verify the daemon first:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/ydotoold.service ~/.config/systemd/user/ydotoold.service
systemctl --user daemon-reload
systemctl --user enable --now ydotoold.service
```

## 6. Install The User Service

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/voicetypist.service ~/.config/systemd/user/voicetypist.service
systemctl --user daemon-reload
systemctl --user enable --now voicetypist.service
```

Check status:

```bash
systemctl --user status voicetypist.service
```

Follow logs:

```bash
journalctl --user -u voicetypist.service -f
```

## GNOME Notes

VoiceTypist can run under GNOME, but the current implementation still depends on X11 access for typing and for the `pynput` hotkey fallback.

If the service starts but logs show:

- `No working X11 display detected`
- `Tray unavailable`
- `Hotkey disabled`

then the service is missing your live desktop session environment. The included unit file is written to inherit the required session variables.

Tray-specific note:

- the GTK tray path is used because `pystray` on X11 does not provide a real menu implementation
- the tray is intended for backend switching and status, not start/stop recording
- the tray also exposes transcription history entries with copy actions

## Plasma / Wayland Notes

For Plasma/Wayland hosts, the recommended setup is:

- `type_backend: ydotool`
- a running `ydotoold` user service
- AppIndicator-capable tray support

Why:

- `xdotool` may trigger KDE remote-control prompts under Wayland
- `ydotool` avoids the portal path and works better when `/dev/uinput` is available to the user

Repo-provided user service:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/ydotoold.service ~/.config/systemd/user/ydotoold.service
systemctl --user daemon-reload
systemctl --user enable --now ydotoold.service
```

Recording HUD note:

- while recording, VoiceTypist opens a small bottom-center GTK overlay showing live input level bars
- finishing recording hides the overlay and proceeds to transcription
- cancelling recording hides the overlay and discards the capture

## Dedicated Hugging Face Cache For Parakeet

The current code sets a dedicated model cache:

```text
~/.cache/voicetypist-hf
```

This avoids failures caused by a broken or root-owned shared Hugging Face cache under `~/.cache/huggingface`.

If you previously used NeMo or Hugging Face tools as another user or with `sudo`, a dedicated VoiceTypist cache is often simpler than repairing the shared cache.
