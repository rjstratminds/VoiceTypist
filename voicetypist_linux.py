#!/usr/bin/env python3
"""
VoiceTypist Linux
Richard Jhang | @rjstratminds GitHub

Features
- double-Right-Alt toggle dictation
- in-memory PipeWire capture (no session file on disk)
- whisper.cpp or NVIDIA Parakeet v3 transcription
- optional Gemini transcript refinement
- X11 text injection
- tray icon with listening/refining/idle states
"""

from __future__ import annotations

import os
import time
import json
import io
import math
import subprocess
import tempfile
import threading
import traceback
import wave
from glob import glob
from pathlib import Path

from PIL import Image, ImageDraw

# -----------------------------
# Config
# -----------------------------

CONFIG_PATH = Path.home() / ".config/voicetypist-linux/config.yaml"
LEGACY_CONFIG_PATH = Path.home() / ".config/voiceink-linux/config.yaml"

DEFAULT_CONFIG = {
    "asr": "whisper",
    "model": "~/whisper.cpp/models/ggml-small.en.bin",
    "whisper_bin": "~/whisper.cpp/build/bin/whisper-cli",
    "parakeet_model": "nvidia/parakeet-tdt-0.6b-v3",
    "gemini_model": "gemini-2.5-flash-lite",
    "audio_source": "default",
}

# -----------------------------
# Utilities
# -----------------------------


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def log(*parts):
    print(*parts, flush=True)


def _x11_env(display: str):
    env = os.environ.copy()
    env["DISPLAY"] = display

    xauthority = env.get("XAUTHORITY")
    if not xauthority:
        candidate = str(Path.home() / ".Xauthority")
        if os.path.exists(candidate):
            env["XAUTHORITY"] = candidate

    return env


def can_connect_x11(display: str) -> bool:
    try:
        result = subprocess.run(
            ["xdotool", "getwindowfocus"],
            env=_x11_env(display),
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_x11_display():
    displays = []

    current = os.environ.get("DISPLAY")
    if current:
        displays.append(current)

    displays.extend(f":{Path(path).name[1:]}" for path in sorted(glob("/tmp/.X11-unix/X*")))
    displays.extend([":0", ":1", ":2"])

    seen = set()
    for display in displays:
        if not display or display in seen:
            continue
        seen.add(display)
        if can_connect_x11(display):
            os.environ["DISPLAY"] = display
            if "XAUTHORITY" not in os.environ:
                candidate = str(Path.home() / ".Xauthority")
                if os.path.exists(candidate):
                    os.environ["XAUTHORITY"] = candidate
            log(f"Using X11 display {display}")
            return display

    log("No working X11 display detected")
    return None


def load_config():
    source_path = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH

    if not source_path.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            import yaml

            yaml.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG

    import yaml

    with open(source_path) as f:
        loaded = yaml.safe_load(f) or {}

    if source_path == LEGACY_CONFIG_PATH and not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(loaded, f)

    return loaded


config = load_config()

# Backwards compatibility with older YAML configs
if "asr" not in config:
    config["asr"] = "whisper"

if "whisper_bin" not in config:
    # try to derive from legacy structure
    try:
        config["whisper_bin"] = config.get("whisper", {}).get("binary", "~/whisper.cpp/build/bin/whisper-cli")
    except Exception:
        config["whisper_bin"] = "~/whisper.cpp/build/bin/whisper-cli"

if "model" not in config:
    try:
        config["model"] = config.get("whisper", {}).get("model", "~/whisper.cpp/models/ggml-small.en.bin")
    except Exception:
        config["model"] = "~/whisper.cpp/models/ggml-small.en.bin"

if "gemini_model" not in config:
    try:
        config["gemini_model"] = config.get("rewrite", {}).get("model", "gemini-2.5-flash-lite")
    except Exception:
        config["gemini_model"] = "gemini-2.5-flash-lite"

if "audio_source" not in config:
    try:
        config["audio_source"] = config.get("audio", {}).get("input_device", "default")
    except Exception:
        config["audio_source"] = "default"

if "parakeet_model" not in config:
    config["parakeet_model"] = "nvidia/parakeet-tdt-0.6b-v3"

# -----------------------------
# ASR
# -----------------------------


class WhisperASR:
    def __init__(self):
        self.binary = expand(config["whisper_bin"])
        self.model = expand(config["model"])

    def transcribe_file(self, wav_path: str):
        cmd = [
            self.binary,
            "-m",
            self.model,
            "-f",
            wav_path,
            "-nt",
            "-np",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout.strip()

    def transcribe_pcm(self, pcm_bytes: bytes):
        if not pcm_bytes:
            return ""

        wav_bytes = io.BytesIO()
        with wave.open(wav_bytes, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm_bytes)

        fd = os.memfd_create("voicetypist-audio")
        try:
            os.write(fd, wav_bytes.getvalue())
            os.lseek(fd, 0, os.SEEK_SET)
            path = f"/proc/{os.getpid()}/fd/{fd}"
            return self.transcribe_file(path)
        finally:
            os.close(fd)


class ParakeetASR:
    def __init__(self):
        self.model_name = config["parakeet_model"]
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        import torch
        from nemo.collections.asr.models import ASRModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = ASRModel.from_pretrained(model_name=self.model_name)
        model = model.to(device)
        model.eval()
        self._model = model
        log(f"Parakeet model loaded on {device}: {self.model_name}")
        return model

    def transcribe_pcm(self, pcm_bytes: bytes):
        if not pcm_bytes:
            return ""

        model = self._load_model()
        wav_bytes = io.BytesIO()
        with wave.open(wav_bytes, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm_bytes)

        fd = os.memfd_create("voicetypist-parakeet")
        try:
            os.write(fd, wav_bytes.getvalue())
            os.lseek(fd, 0, os.SEEK_SET)
            path = f"/proc/{os.getpid()}/fd/{fd}"
            result = model.transcribe([path], batch_size=1)
            if not result:
                return ""
            if isinstance(result[0], str):
                return result[0].strip()
            return str(result[0]).strip()
        finally:
            os.close(fd)


def build_asr():
    backend = str(config.get("asr", "whisper")).lower()
    if backend == "parakeet":
        log(f"ASR backend: parakeet ({config['parakeet_model']})")
        return ParakeetASR()

    log(f"ASR backend: whisper ({config['model']})")
    return WhisperASR()


asr = build_asr()


app_state = "idle"
tray_icon = None
tray_lock = threading.Lock()
pystray = None

# -----------------------------
# Gemini refinement
# -----------------------------


def gemini_refine(text: str):
    api_key = os.environ.get("GOOGLE_API_KEY")

    if not api_key:
        return text

    prompt = f"""
Refine this transcript to sound native and easy-to-understand in English.
Enhance its flow and clarity, ensuring key points are conveyed accurately
and intuitively without needless repetition.

Only give the refined text.

Transcript:
{text}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config['gemini_model']}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read().decode())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    except Exception:
        return text


# -----------------------------
# Typing output
# -----------------------------


def type_text(text):
    subprocess.run(["xdotool", "type", "--clearmodifiers", text])


def play_chime(name: str):
    try:
        if name in {"start", "stop"}:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                sample_rate = 32000
                tones = [(740, 0.07), (988, 0.10)] if name == "start" else [(988, 0.07), (740, 0.10)]
                frames = bytearray()
                fade = int(sample_rate * 0.008)

                for frequency, duration in tones:
                    count = int(sample_rate * duration)
                    for index in range(count):
                        envelope = 1.0
                        if index < fade:
                            envelope = index / max(fade, 1)
                        elif count - index < fade:
                            envelope = (count - index) / max(fade, 1)
                        value = int(
                            12000
                            * envelope
                            * math.sin(2 * math.pi * frequency * (index / sample_rate))
                        )
                        frames.extend(value.to_bytes(2, byteorder="little", signed=True))

                with wave.open(temp_file, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(frames)

                subprocess.Popen(
                    ["aplay", "-q", temp_file.name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    except Exception as exc:
        log(f"Sound unavailable: {exc}")


def normalize_transcript(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned in {"", "[BLANK_AUDIO]", "[blank_audio]"}:
        return ""
    return cleaned


def _state_color(state: str):
    if state == "listening":
        return "#dc2626"
    if state == "processing":
        return "#2563eb"
    return "#6b7280"


def _state_label(state: str):
    return {
        "idle": "Idle",
        "listening": "Listening",
        "processing": "Refining",
    }.get(state, state.title())


def build_tray_image(state: str):
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse((8, 8, 56, 56), fill="#111827")
    draw.ellipse((18, 18, 46, 46), fill=_state_color(state))
    draw.rounded_rectangle((28, 42, 36, 56), radius=3, fill="#f3f4f6")
    return image


def refresh_tray():
    global tray_icon

    if tray_icon is None:
        return

    with tray_lock:
        state = app_state

    tray_icon.icon = build_tray_image(state)
    tray_icon.title = f"VoiceTypist Linux: {_state_label(state)}"
    tray_icon.update_menu()


def set_state(state: str):
    global app_state

    with tray_lock:
        app_state = state

    refresh_tray()


def tray_toggle(icon=None, item=None):
    toggle_recording()


def tray_quit(icon=None, item=None):
    global record_session

    if record_session is not None:
        session = record_session
        record_session = None
        session.stop()

    if tray_icon is not None:
        tray_icon.stop()

    raise SystemExit(0)


def init_tray():
    global pystray
    global tray_icon

    try:
        import pystray as pystray_module

        pystray = pystray_module
        tray_icon = pystray.Icon(
            "voicetypist-linux",
            build_tray_image(app_state),
            "VoiceTypist Linux: Idle",
            menu=pystray.Menu(
                pystray.MenuItem("Toggle Recording", tray_toggle, default=True),
                pystray.MenuItem("Quit", tray_quit),
            ),
        )

        thread = threading.Thread(target=tray_icon.run, daemon=True)
        thread.start()
        log("Tray icon started")
    except Exception as exc:
        tray_icon = None
        log(f"Tray unavailable: {exc}")


class StreamingRecorder:
    def __init__(self):
        self.source = config.get("audio_source", "default")
        self.proc = None
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.closed = threading.Event()
        self.reader = None

    def start(self):
        self.proc = subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "quiet",
                "-f",
                "pulse",
                "-i",
                self.source,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        log(f"Recording source: {self.source}")

    def _read_loop(self):
        while not self.closed.is_set():
            chunk = self.proc.stdout.read(4096)
            if not chunk:
                break
            with self.lock:
                self.buffer.extend(chunk)

    def snapshot(self) -> bytes:
        with self.lock:
            return bytes(self.buffer)

    def stop(self) -> bytes:
        self.closed.set()
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1)
        if self.reader is not None:
            self.reader.join(timeout=1)
        data = self.snapshot()
        log(f"Captured PCM size: {len(data)} bytes")
        return data


class LiveDictationSession:
    def __init__(self):
        self.recorder = StreamingRecorder()

    def start(self):
        self.recorder.start()
        set_state("listening")
        play_chime("start")
        log("Recording started")

    def stop(self):
        pcm = self.recorder.stop()
        play_chime("stop")
        set_state("processing")
        transcript = normalize_transcript(asr.transcribe_pcm(pcm))
        log("Final transcript:", transcript)
        if not transcript:
            log("Transcript empty; skipping refinement and typing")
            set_state("idle")
            return

        refined = normalize_transcript(gemini_refine(transcript))
        log("Refined transcript:", refined)
        if refined:
            type_text(refined)
        set_state("idle")


record_session = None


def toggle_recording():
    global record_session

    if record_session is None:
        record_session = LiveDictationSession()
        record_session.start()
    else:
        session = record_session
        record_session = None
        session.stop()


# -----------------------------
# Hotkey via evdev
# -----------------------------


import evdev


class HotkeyBase:
    def __init__(self):
        self.last = 0.0
        self.window = 0.35

    def trigger(self):
        now = time.time()
        if now - self.last < self.window:
            toggle_recording()
            self.last = 0.0
        else:
            self.last = now


class EvdevAltHotkey(HotkeyBase):
    def __init__(self):
        super().__init__()
        self.dev = self._select_device()
        log(f"Hotkey backend: evdev on {self.dev.path} ({self.dev.name})")

    def _supports_right_alt(self, device):
        try:
            keys = device.capabilities().get(evdev.ecodes.EV_KEY, [])
        except OSError:
            return False
        return evdev.ecodes.KEY_RIGHTALT in keys

    def _select_device(self):
        devices = []
        errors = []

        for path in evdev.list_devices():
            try:
                devices.append(evdev.InputDevice(path))
            except OSError as exc:
                errors.append(f"{path}: {exc}")

        if not devices:
            detail = ", ".join(errors) if errors else "evdev.list_devices() returned no devices"
            raise RuntimeError(detail)

        keyboards = []
        right_alt_devices = []

        for device in devices:
            name = (device.name or "").lower()
            if "keyboard" in name or "kbd" in name:
                keyboards.append(device)
            if self._supports_right_alt(device):
                right_alt_devices.append(device)

        for candidate in right_alt_devices:
            if candidate in keyboards:
                return candidate
        if right_alt_devices:
            return right_alt_devices[0]
        if keyboards:
            return keyboards[0]
        return devices[0]

    def run(self):
        for event in self.dev.read_loop():
            if event.type != evdev.ecodes.EV_KEY:
                continue

            key_event = evdev.categorize(event)
            if (
                key_event.scancode == evdev.ecodes.KEY_RIGHTALT
                and key_event.keystate == key_event.key_down
            ):
                self.trigger()


class PynputAltHotkey(HotkeyBase):
    def __init__(self):
        super().__init__()
        from pynput import keyboard

        self.keyboard = keyboard
        self.listener = keyboard.Listener(on_press=self.on_press)
        log("Hotkey backend: pynput")

    def on_press(self, key):
        if key in (self.keyboard.Key.alt_r, self.keyboard.Key.alt_gr):
            self.trigger()

    def run(self):
        self.listener.start()
        self.listener.join()


class DisabledHotkey:
    def __init__(self, reasons):
        self.reasons = reasons
        log("Hotkey disabled; no usable backend found")
        for reason in reasons:
            log(f"Hotkey backend unavailable: {reason}")

    def run(self):
        while True:
            time.sleep(60)


class AltHotkey:
    def __init__(self):
        self.backend = self._build_backend()

    def _build_backend(self):
        reasons = []

        try:
            return EvdevAltHotkey()
        except Exception as exc:
            reasons.append(f"evdev: {exc}")

        try:
            return PynputAltHotkey()
        except Exception as exc:
            reasons.append(f"pynput: {exc}")

        return DisabledHotkey(reasons)

    def run(self):
        try:
            self.backend.run()
        except Exception:
            log("Hotkey listener crashed")
            traceback.print_exc()
            raise


# -----------------------------
# Main
# -----------------------------


def main():
    log("VoiceTypist Linux started")
    ensure_x11_display()
    init_tray()

    hotkey = AltHotkey()

    t = threading.Thread(target=hotkey.run)
    t.daemon = True
    t.start()

    while True:
        time.sleep(1)  # fixed missing parenthesis previously causing SyntaxError
            # original code likely truncated
            # keep loop alive1)


if __name__ == "__main__":
    main()
