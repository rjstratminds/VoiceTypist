#!/usr/bin/env python3
"""
VoiceTypist Linux
Richard Jhang | @rjstratminds GitHub

VoiceTypist, inspired by VoiceInk, maintained by Richard Jhang | @rjstratminds

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
import sys
import time
import json
import io
import math
import subprocess
import struct
import tempfile
import threading
import traceback
import wave
import site
import shutil
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
    "whisper_threads": max(os.cpu_count() or 4, 1),
    "type_backend": "auto",
    "parakeet_model": "nvidia/parakeet-tdt-0.6b-v3",
    "gemini_model": "gemini-2.5-flash-lite",
    "audio_source": "default",
    "rewrite_system_prompt": (
        "Refine this transcript to sound native and easy-to-understand in English.\n"
        "Enhance its flow and clarity, ensuring key points are conveyed accurately\n"
        "and intuitively without needless repetition.\n\n"
        "Only give the refined text."
    ),
}

HF_HOME = Path.home() / ".cache" / "voicetypist-hf"
HF_HUB_CACHE = HF_HOME / "hub"
TRAY_ICON_DIR = HF_HOME / "tray-icons"

# -----------------------------
# Utilities
# -----------------------------


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def log(*parts):
    print(*parts, flush=True)


def current_asr_backend() -> str:
    return str(config.get("asr", "whisper")).lower()


def ensure_model_cache_env():
    HF_HOME.mkdir(parents=True, exist_ok=True)
    HF_HUB_CACHE.mkdir(parents=True, exist_ok=True)
    TRAY_ICON_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_CACHE"] = str(HF_HUB_CACHE)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_HUB_CACHE)


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
ensure_model_cache_env()

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

if "whisper_threads" not in config:
    try:
        config["whisper_threads"] = int(
            config.get("whisper", {}).get("threads", DEFAULT_CONFIG["whisper_threads"])
        )
    except Exception:
        config["whisper_threads"] = DEFAULT_CONFIG["whisper_threads"]

if "gemini_model" not in config:
    try:
        config["gemini_model"] = config.get("rewrite", {}).get("model", "gemini-2.5-flash-lite")
    except Exception:
        config["gemini_model"] = "gemini-2.5-flash-lite"

if "rewrite_system_prompt" not in config:
    try:
        config["rewrite_system_prompt"] = config.get("rewrite", {}).get(
            "system_prompt", DEFAULT_CONFIG["rewrite_system_prompt"]
        )
    except Exception:
        config["rewrite_system_prompt"] = DEFAULT_CONFIG["rewrite_system_prompt"]

if "audio_source" not in config:
    try:
        config["audio_source"] = config.get("audio", {}).get("input_device", "default")
    except Exception:
        config["audio_source"] = "default"

if "parakeet_model" not in config:
    config["parakeet_model"] = "nvidia/parakeet-tdt-0.6b-v3"

if "type_backend" not in config:
    config["type_backend"] = "auto"

# -----------------------------
# ASR
# -----------------------------


class WhisperASR:
    def __init__(self):
        self.binary = expand(config["whisper_bin"])
        self.model = expand(config["model"])
        self.threads = max(int(config.get("whisper_threads", DEFAULT_CONFIG["whisper_threads"])), 1)

    def transcribe_file(self, wav_path: str):
        cmd = [
            self.binary,
            "-m",
            self.model,
            "-t",
            str(self.threads),
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
            first = result[0]
            if isinstance(first, str):
                return first.strip()

            text = getattr(first, "text", None)
            if isinstance(text, str):
                return text.strip()

            return str(first).strip()
        finally:
            os.close(fd)


def build_asr():
    backend = str(config.get("asr", "whisper")).lower()
    if backend == "parakeet":
        log(f"ASR backend: parakeet ({config['parakeet_model']})")
        return ParakeetASR()

    log(f"ASR backend: whisper ({config['model']}, threads={config.get('whisper_threads')})")
    return WhisperASR()


asr = build_asr()


app_state = "idle"
tray_icon = None
tray_lock = threading.Lock()
pystray = None
gtk = None
appindicator = None
app_indicator = None
gtk_status_icon = None
gtk_menu = None
glib = None
gdk = None
overlay_window = None
overlay_area = None
overlay_levels = []
overlay_visible = False
transcription_history = []
gtk_main_loop_active = False

# -----------------------------
# Gemini refinement
# -----------------------------


def gemini_refine(text: str):
    api_key = os.environ.get("GOOGLE_API_KEY")

    if not api_key:
        return text

    system_prompt = config.get("rewrite_system_prompt", DEFAULT_CONFIG["rewrite_system_prompt"]).strip()
    prompt = f"{system_prompt}\n\nTranscript:\n{text}\n"

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
    backend = str(config.get("type_backend", "auto")).lower()
    attempted = []

    if backend in {"auto", "ydotool"}:
        attempted.append("ydotool")
        if _type_text_with_ydotool(text):
            return
        if backend == "ydotool":
            return

    if backend in {"auto", "xdotool"}:
        attempted.append("xdotool")
        if _type_text_with_xdotool(text):
            return

    log(f"Typing failed using backends: {', '.join(attempted)}")


def _type_text_with_xdotool(text: str) -> bool:
    if shutil.which("xdotool") is None:
        log("xdotool unavailable")
        return False

    result = subprocess.run(
        ["xdotool", "type", "--clearmodifiers", text],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"xdotool type failed: {result.stderr.strip() or result.stdout.strip()}")
        return False

    log(f"Typed text ({len(text)} chars) via xdotool")
    return True


def _type_text_with_ydotool(text: str) -> bool:
    if shutil.which("ydotool") is None:
        log("ydotool unavailable")
        return False

    socket_path = os.environ.get("YDOTOOL_SOCKET") or str(Path.home() / ".ydotool_socket")
    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = socket_path

    result = subprocess.run(
        ["ydotool", "type", text],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        log(f"ydotool type failed: {result.stderr.strip() or result.stdout.strip()}")
        return False

    log(f"Typed text ({len(text)} chars) via ydotool")
    return True


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


def write_tray_icon(state: str) -> str:
    TRAY_ICON_DIR.mkdir(parents=True, exist_ok=True)
    icon_path = TRAY_ICON_DIR / f"tray-icon-{state}.png"
    build_tray_image(state).save(icon_path)
    return str(icon_path)


def history_preview(text: str, limit: int = 44) -> str:
    single_line = " ".join((text or "").split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 1] + "…"


def add_transcription_history(text: str):
    cleaned = normalize_transcript(text)
    if not cleaned:
        return
    transcription_history.insert(0, cleaned)
    del transcription_history[10:]
    refresh_tray()


def copy_text_to_clipboard(text: str):
    try:
        if gtk is not None and gdk is not None:
            clipboard = gtk.Clipboard.get(gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
            log(f"Copied transcript ({len(text)} chars)")
            return
    except Exception as exc:
        log(f"GTK clipboard failed: {exc}")

    for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        try:
            subprocess.run(cmd, input=text, text=True, check=True, capture_output=True)
            log(f"Copied transcript ({len(text)} chars)")
            return
        except Exception:
            continue

    log("Clipboard copy unavailable")


def refresh_tray():
    global tray_icon
    global app_indicator
    global gtk_status_icon

    if tray_icon is None and app_indicator is None and gtk_status_icon is None:
        return

    with tray_lock:
        state = app_state

    title = f"VoiceTypist Linux: {_state_label(state)} [{current_asr_backend()}]"

    if app_indicator is not None or gtk_status_icon is not None:
        if glib is not None and gtk_main_loop_active:
            glib.idle_add(_refresh_gtk_tray, state, title)
        else:
            _refresh_gtk_tray(state, title)

    if tray_icon is not None:
        tray_icon.icon = build_tray_image(state)
        tray_icon.title = title
        tray_icon.menu = build_pystray_menu()
        tray_icon.update_menu()


def _refresh_gtk_tray(state: str, title: str):
    icon_path = write_tray_icon(state)

    if app_indicator is not None:
        app_indicator.set_icon_full(icon_path, title)
        app_indicator.set_status(appindicator.IndicatorStatus.ACTIVE)
        app_indicator.set_menu(gtk_menu)

    if gtk_status_icon is None:
        rebuild_gtk_menu()
        return False

    gtk_status_icon.set_from_file(icon_path)
    gtk_status_icon.set_tooltip_text(title)
    rebuild_gtk_menu()
    return False


def _overlay_position(width: int, height: int):
    if gdk is None:
        return (0, 0)
    screen = gdk.Screen.get_default()
    if screen is None:
        return (0, 0)
    screen_width = screen.get_width()
    screen_height = screen.get_height()
    return (
        max((screen_width - width) // 2, 0),
        max(screen_height - height - 54, 0),
    )


def _draw_overlay(widget, cr):
    width = widget.get_allocated_width()
    height = widget.get_allocated_height()

    cr.set_source_rgba(0.07, 0.09, 0.12, 0.9)
    cr.rectangle(0, 0, width, height)
    cr.fill()

    cr.set_source_rgba(1, 1, 1, 0.08)
    cr.rectangle(1, 1, width - 2, height - 2)
    cr.stroke()

    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(13)
    cr.set_source_rgba(0.95, 0.97, 0.99, 0.92)
    cr.move_to(14, 18)
    cr.show_text("VoiceTypist Listening")

    baseline = height - 18
    graph_top = 26
    graph_height = baseline - graph_top
    bar_width = 4
    gap = 2
    start_x = 14

    for index, level in enumerate(overlay_levels[-44:]):
        x = start_x + index * (bar_width + gap)
        bar_height = max(6, level * graph_height)
        y = baseline - bar_height

        if level >= 0.38:
            cr.set_source_rgba(0.22, 0.82, 0.49, 0.95)
        elif level >= 0.16:
            cr.set_source_rgba(0.98, 0.78, 0.20, 0.95)
        else:
            cr.set_source_rgba(0.42, 0.56, 0.96, 0.95)

        cr.rectangle(x, y, bar_width, bar_height)
        cr.fill()

    current = overlay_levels[-1] if overlay_levels else 0.0
    meter_width = 58
    meter_height = 8
    meter_x = width - meter_width - 14
    meter_y = 11

    cr.set_source_rgba(1, 1, 1, 0.14)
    cr.rectangle(meter_x, meter_y, meter_width, meter_height)
    cr.fill()

    if current >= 0.38:
        cr.set_source_rgba(0.22, 0.82, 0.49, 0.95)
    elif current >= 0.16:
        cr.set_source_rgba(0.98, 0.78, 0.20, 0.95)
    else:
        cr.set_source_rgba(0.42, 0.56, 0.96, 0.95)
    cr.rectangle(meter_x, meter_y, max(current * meter_width, 2), meter_height)
    cr.fill()

    cr.set_font_size(10)
    cr.set_source_rgba(0.75, 0.80, 0.86, 0.9)
    cr.move_to(meter_x, meter_y + 21)
    cr.show_text("level")

    cr.move_to(14, height - 6)
    cr.show_text("Right Alt x2 stop   Right Ctrl x2 cancel")


def init_overlay():
    global overlay_window
    global overlay_area

    if gtk is None or overlay_window is not None:
        return

    overlay_window = gtk.Window(type=gtk.WindowType.POPUP)
    overlay_window.set_decorated(False)
    overlay_window.set_keep_above(True)
    overlay_window.set_skip_taskbar_hint(True)
    overlay_window.set_skip_pager_hint(True)
    overlay_window.set_accept_focus(False)
    overlay_window.set_resizable(False)
    overlay_window.set_default_size(292, 74)
    overlay_window.set_type_hint(gdk.WindowTypeHint.NOTIFICATION)

    overlay_area = gtk.DrawingArea()
    overlay_area.set_size_request(292, 74)
    overlay_area.connect("draw", _draw_overlay)
    overlay_window.add(overlay_area)
    overlay_window.realize()
    overlay_window.hide()


def _show_overlay():
    global overlay_visible

    if overlay_window is None:
        return False

    width = 292
    height = 74
    x, y = _overlay_position(width, height)
    overlay_window.move(x, y)
    overlay_window.show_all()
    overlay_visible = True
    return False


def _hide_overlay():
    global overlay_visible

    if overlay_window is not None:
        overlay_window.hide()
    overlay_visible = False
    return False


def show_overlay():
    if gtk is None or glib is None:
        return
    glib.idle_add(_show_overlay)


def hide_overlay():
    if gtk is None or glib is None:
        return
    glib.idle_add(_hide_overlay)


def _push_overlay_level(level: float):
    if overlay_area is None:
        return False
    overlay_levels.append(max(0.0, min(level, 1.0)))
    del overlay_levels[:-44]
    overlay_area.queue_draw()
    return False


def push_overlay_level(level: float):
    if gtk is None or glib is None:
        return
    glib.idle_add(_push_overlay_level, level)


def set_state(state: str):
    global app_state

    with tray_lock:
        app_state = state

    refresh_tray()
    if state == "listening":
        show_overlay()
    else:
        hide_overlay()


def save_config():
    import yaml

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)


def restart_service():
    def _restart():
        try:
            subprocess.Popen(
                ["systemctl", "--user", "restart", "voicetypist.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log(f"Failed to restart service: {exc}")

    threading.Thread(target=_restart, daemon=True).start()


def switch_backend(backend: str):
    backend = backend.lower()
    if backend not in {"whisper", "parakeet"}:
        return
    if current_asr_backend() == backend:
        return

    config["asr"] = backend
    save_config()
    log(f"Switching backend to {backend}")
    restart_service()


def tray_use_whisper(icon=None, item=None):
    switch_backend("whisper")


def tray_use_parakeet(icon=None, item=None):
    switch_backend("parakeet")


def tray_copy_history_entry(text: str):
    copy_text_to_clipboard(text)


def tray_quit(icon=None, item=None):
    global record_session

    if record_session is not None:
        session = record_session
        record_session = None
        session.stop()

    if tray_icon is not None:
        tray_icon.stop()

    if gtk is not None:
        gtk.main_quit()

    raise SystemExit(0)


def build_gtk_history_menu():
    history_menu = gtk.Menu()

    if not transcription_history:
        empty_item = gtk.MenuItem.new_with_label("No transcriptions yet")
        empty_item.set_sensitive(False)
        history_menu.append(empty_item)
        history_menu.show_all()
        return history_menu

    for index, text in enumerate(transcription_history, start=1):
        label = f"{index}. {history_preview(text)}"
        item = gtk.MenuItem.new_with_label(label)
        submenu = gtk.Menu()

        copy_item = gtk.MenuItem.new_with_label("Copy")
        copy_item.connect("activate", lambda _, entry=text: tray_copy_history_entry(entry))
        submenu.append(copy_item)

        full_item = gtk.MenuItem.new_with_label(text if len(text) <= 120 else text[:117] + "…")
        full_item.set_sensitive(False)
        submenu.append(full_item)

        submenu.show_all()
        item.set_submenu(submenu)
        history_menu.append(item)

    history_menu.show_all()
    return history_menu


def rebuild_gtk_menu():
    global gtk_menu

    if gtk is None or gtk_menu is None:
        return

    for child in gtk_menu.get_children():
        gtk_menu.remove(child)

    whisper_item = gtk.CheckMenuItem.new_with_label("Use Whisper")
    whisper_item.set_draw_as_radio(True)
    whisper_item.set_active(current_asr_backend() == "whisper")
    whisper_item.connect("activate", lambda _: tray_use_whisper())
    gtk_menu.append(whisper_item)

    parakeet_item = gtk.CheckMenuItem.new_with_label("Use Parakeet")
    parakeet_item.set_draw_as_radio(True)
    parakeet_item.set_active(current_asr_backend() == "parakeet")
    parakeet_item.connect("activate", lambda _: tray_use_parakeet())
    gtk_menu.append(parakeet_item)

    history_item = gtk.MenuItem.new_with_label("Transcription History")
    history_item.set_submenu(build_gtk_history_menu())
    gtk_menu.append(history_item)

    gtk_menu.append(gtk.SeparatorMenuItem())

    quit_item = gtk.MenuItem.new_with_label("Quit")
    quit_item.connect("activate", lambda _: tray_quit())
    gtk_menu.append(quit_item)

    gtk_menu.show_all()


def build_pystray_history_menu():
    if not transcription_history:
        return pystray.Menu(
            pystray.MenuItem("No transcriptions yet", None, enabled=False)
        )

    items = []
    for index, text in enumerate(transcription_history, start=1):
        label = f"{index}. {history_preview(text)}"
        items.append(
            pystray.MenuItem(
                label,
                pystray.Menu(
                    pystray.MenuItem(
                        "Copy",
                        lambda icon, item, entry=text: tray_copy_history_entry(entry),
                    ),
                    pystray.MenuItem(text if len(text) <= 120 else text[:117] + "…", None, enabled=False),
                ),
            )
        )
    return pystray.Menu(*items)


def build_pystray_menu():
    return pystray.Menu(
        pystray.MenuItem(
            "Use Whisper",
            tray_use_whisper,
            checked=lambda item: current_asr_backend() == "whisper",
            radio=True,
        ),
        pystray.MenuItem(
            "Use Parakeet",
            tray_use_parakeet,
            checked=lambda item: current_asr_backend() == "parakeet",
            radio=True,
        ),
        pystray.MenuItem("Transcription History", build_pystray_history_menu()),
        pystray.MenuItem("Quit", tray_quit),
    )


def init_tray():
    global pystray
    global tray_icon
    global gtk
    global appindicator
    global app_indicator
    global gdk
    global glib
    global gtk_status_icon
    global gtk_menu

    try:
        pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
        for candidate in (
            "/usr/lib/python3/dist-packages",
            f"/usr/lib/python{pyver}/site-packages",
            f"/usr/lib64/python{pyver}/site-packages",
        ):
            if os.path.isdir(candidate):
                site.addsitedir(candidate)
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        from gi.repository import Gdk
        from gi.repository import GLib
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AyatanaAppIndicator
            appindicator = AyatanaAppIndicator
        except Exception:
            appindicator = None

        gtk = Gtk
        gdk = Gdk
        glib = GLib
        gtk_menu = Gtk.Menu()
        rebuild_gtk_menu()
        init_overlay()

        if appindicator is not None:
            app_indicator = appindicator.Indicator.new(
                "voicetypist-linux",
                write_tray_icon(app_state),
                appindicator.IndicatorCategory.APPLICATION_STATUS,
            )
            app_indicator.set_title("VoiceTypist Linux")
            app_indicator.set_status(appindicator.IndicatorStatus.ACTIVE)
            app_indicator.set_menu(gtk_menu)
            refresh_tray()
            log("Tray icon started (AppIndicator)")
            return "gtk"

        gtk_status_icon = Gtk.StatusIcon()
        gtk_status_icon.set_visible(True)
        gtk_status_icon.set_title("VoiceTypist Linux")
        gtk_status_icon.set_tooltip_text(f"VoiceTypist Linux: Idle [{current_asr_backend()}]")
        gtk_status_icon.set_from_file(write_tray_icon(app_state))

        def popup_menu(icon, button=0, activate_time=0):
            gtk_menu.popup(
                None,
                None,
                Gtk.StatusIcon.position_menu,
                icon,
                button,
                activate_time,
            )

        gtk_status_icon.connect("popup-menu", popup_menu)
        gtk_status_icon.connect("activate", lambda icon: popup_menu(icon, 0, Gtk.get_current_event_time()))

        log("Tray icon started (GTK)")
        return "gtk"
    except Exception as exc:
        gtk = None
        appindicator = None
        app_indicator = None
        gtk_status_icon = None
        gtk_menu = None
        log(f"GTK tray unavailable: {exc}")

    try:
        import pystray as pystray_module

        pystray = pystray_module
        tray_icon = pystray.Icon(
            "voicetypist-linux",
            build_tray_image(app_state),
            f"VoiceTypist Linux: Idle [{current_asr_backend()}]",
            menu=build_pystray_menu(),
        )

        thread = threading.Thread(target=tray_icon.run, daemon=True)
        thread.start()
        log("Tray icon started")
        return "pystray"
    except Exception as exc:
        tray_icon = None
        log(f"Tray unavailable: {exc}")
        return None


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
            push_overlay_level(self._chunk_level(chunk))

    def _chunk_level(self, chunk: bytes) -> float:
        if not chunk:
            return 0.0

        sample_count = len(chunk) // 2
        if sample_count <= 0:
            return 0.0

        samples = struct.unpack("<%dh" % sample_count, chunk[: sample_count * 2])
        peak = max(abs(sample) for sample in samples)
        return min(peak / 12000.0, 1.0)

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
        overlay_levels.clear()
        self.recorder.start()
        set_state("listening")
        play_chime("start")
        log("Recording started")

    def stop(self):
        pcm = self.recorder.stop()
        play_chime("stop")
        set_state("processing")
        try:
            transcript = normalize_transcript(asr.transcribe_pcm(pcm))
            log("Final transcript:", transcript)
            if not transcript:
                log("Transcript empty; skipping refinement and typing")
                return

            refined = normalize_transcript(gemini_refine(transcript))
            log("Refined transcript:", refined)
            if refined:
                add_transcription_history(refined)
                type_text(refined)
        except Exception as exc:
            log(f"Transcription failed: {exc}")
            traceback.print_exc()
        finally:
            set_state("idle")

    def cancel(self):
        self.recorder.stop()
        set_state("idle")
        log("Recording cancelled")


record_session = None


def toggle_recording():
    global record_session

    try:
        if record_session is None:
            record_session = LiveDictationSession()
            record_session.start()
        else:
            session = record_session
            record_session = None
            session.stop()
    except Exception:
        record_session = None
        set_state("idle")
        log("Toggle recording failed")
        traceback.print_exc()


def cancel_recording():
    global record_session

    try:
        if record_session is None:
            return
        session = record_session
        record_session = None
        session.cancel()
    except Exception:
        record_session = None
        set_state("idle")
        log("Cancel recording failed")
        traceback.print_exc()


# -----------------------------
# Hotkey via evdev
# -----------------------------


import evdev


class HotkeyBase:
    def __init__(self):
        self.last_toggle = 0.0
        self.last_cancel = 0.0
        self.window = 0.35

    def trigger_toggle(self):
        now = time.time()
        if now - self.last_toggle < self.window:
            toggle_recording()
            self.last_toggle = 0.0
        else:
            self.last_toggle = now

    def trigger_cancel(self):
        now = time.time()
        if now - self.last_cancel < self.window:
            cancel_recording()
            self.last_cancel = 0.0
        else:
            self.last_cancel = now


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
                self.trigger_toggle()
            elif (
                key_event.scancode == evdev.ecodes.KEY_RIGHTCTRL
                and key_event.keystate == key_event.key_down
            ):
                self.trigger_cancel()


class PynputAltHotkey(HotkeyBase):
    def __init__(self):
        super().__init__()
        from pynput import keyboard

        self.keyboard = keyboard
        self.listener = keyboard.Listener(on_press=self.on_press)
        log("Hotkey backend: pynput")

    def _is_ctrl_cancel_key(self, key) -> bool:
        if key in (self.keyboard.Key.ctrl_r, self.keyboard.Key.ctrl):
            return True

        key_text = str(key).lower()
        return "ctrl_r" in key_text or key_text == "key.ctrl"

    def on_press(self, key):
        if key in (self.keyboard.Key.alt_r, self.keyboard.Key.alt_gr):
            self.trigger_toggle()
        elif self._is_ctrl_cancel_key(key):
            log(f"Cancel hotkey keypress: {key}")
            self.trigger_cancel()

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
    global gtk_main_loop_active

    log("VoiceTypist Linux started")
    ensure_x11_display()
    tray_backend = init_tray()

    hotkey = AltHotkey()

    t = threading.Thread(target=hotkey.run)
    t.daemon = True
    t.start()

    if tray_backend == "gtk" and gtk is not None:
        gtk_main_loop_active = True
        gtk.main()
        return

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
