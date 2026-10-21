"""
Music Light Sync Simulator
Python desktop music player + music-file frequency analysis + virtual Christmas lights.
Optional ESP32 UDP control (Wi-Fi).

Tested design target: Python 3.10+
Install: pip install pygame numpy soundfile mutagen
Audio analysis uses soundfile to analyze the loaded track directly; no microphone or system loopback.
"""

import math
import os
import socket
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser

import numpy as np
import pygame

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    from generate_ino import export_ino_file, export_sd_show_file, COLOR_PALETTES
except ImportError:
    export_ino_file = None
    export_sd_show_file = None
    COLOR_PALETTES = {}



APP_TITLE = "Music Light Sync Simulator"
UDP_PORT = 4210

BG = "#101522"
PANEL = "#192235"
PANEL2 = "#202d44"
TEXT = "#eef4ff"
MUTED = "#9aabc4"
ACCENT = "#58a6ff"
GREEN = "#47d18c"
GOLD = "#ffcc66"
RED = "#ff647c"

class MusicLightSimulator:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.root.minsize(900, 650)
        self.root.configure(bg=BG)

        pygame.mixer.init()
        self.track = ""
        self.playing = False
        self.duration = 0.0
        self.paused_at = 0.0
        # Music-file analysis only: no microphone and no system-audio loopback.
        self.analysis_frames = []
        self.analysis_ready = False
        self.analysis_error = ""
        self.analysis_hop_seconds = 0.05
        self.playback_offset = 0.0
        self.playback_started_at = 0.0
        self.bass = 0.0
        self.mid = 0.0
        self.treble = 0.0
        self.rms = 0.0
        self.beat = 0.0
        self.beat_strength = 0.0
        # Initialize smoothing and previous-frame values before the first UI update.
        self._bass_smooth = 0.0
        self._mid_smooth = 0.0
        self._treble_smooth = 0.0
        self.prev_bass = 0.0
        self.prev_rms = 0.0
        self.prev_spectrum = None
        self.onset_history = []
        self.last_beat = 0.0
        self.audio_source = "Not connected"
        self.audio_sample_rate = 44100
        self.breath_phase = 0.0
        self.last_frame = time.perf_counter()
        self.leds_enabled = True
        self.esp_connected = False
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.host_var = tk.StringVar(value="192.168.1.100")
        self.port_var = tk.StringVar(value=str(UDP_PORT))
        self.mode_var = tk.StringVar(value="Music Reactive")
        self.brightness_var = tk.DoubleVar(value=75)
        self.sensitivity_var = tk.DoubleVar(value=1.5)
        self.breath_var = tk.DoubleVar(value=0.45)
        self.color_var = tk.StringVar(value="Christmas")
        self.gradient_var = tk.StringVar(value="Red → Purple → Blue")
        self.custom_color = "#58a6ff"
        self.status_var = tk.StringVar(value="Ready — effects analyze the loaded music file directly")
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        self.volume_var = tk.DoubleVar(value=70)
        self.seek_var = tk.DoubleVar(value=0)
        self.dragging_seek = False

        # Each physical-looking set can use its own animation preset.
        self.effect_options = ["Use Global", "Music Reactive", "Bass Pulse", "Breathing",
                               "Christmas Chase", "Rainbow", "Wave", "Ocean Wave", "Dual Wave",
                               "Comet", "Ripple", "Vertical Flow", "Sparkle Wave", "Color Sweep", "Twinkle"]
        self.strip_effects = ["Use Global" for _ in range(5)]
        self.christmas_effects = ["Use Global" for _ in range(5)]
        self.selected_set_type = tk.StringVar(value="LED Strip")
        self.selected_set_index = tk.StringVar(value="1")
        self.selected_set_effect = tk.StringVar(value="Use Global")
        # Individual emitter overrides: (kind, set_index, light_index) -> effect name.
        self.individual_effects = {}
        self.selected_light_index = tk.StringVar(value="All in set")
        self.selected_light_effect = tk.StringVar(value="Use Global")

        self._build_ui()
        self._draw_lights()
        self._update_loop()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(header, text="MUSIC LIGHT SYNC", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(header, text="Music-file beat analysis • vertical RGB LED strips • Christmas light strings",
                 font=("Segoe UI", 10), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=18, pady=8)
        # Scrollable settings panel: keeps individual LED/bulb controls visible on
        # smaller screens instead of letting lower controls disappear below the window.
        left_shell = tk.Frame(main, bg=PANEL, width=355)
        left_shell.pack(side="left", fill="y", padx=(0, 12))
        left_shell.pack_propagate(False)
        left_canvas = tk.Canvas(left_shell, bg=PANEL, highlightthickness=0, width=335)
        left_scrollbar = ttk.Scrollbar(left_shell, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        left_scrollbar.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)
        left = tk.Frame(left_canvas, bg=PANEL, width=335)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda e: left_canvas.itemconfigure(left_window, width=e.width))
        def _sidebar_wheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        left_canvas.bind("<Enter>", lambda _e: left_canvas.bind_all("<MouseWheel>", _sidebar_wheel))
        left_canvas.bind("<Leave>", lambda _e: left_canvas.unbind_all("<MouseWheel>"))
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        self._section(left, "MUSIC PLAYER")
        self.track_label = tk.Label(left, text="No track loaded", bg=PANEL, fg=MUTED,
                                    font=("Segoe UI", 10), wraplength=300, justify="left")
        self.track_label.pack(fill="x", padx=14, pady=(0, 10))
        row = tk.Frame(left, bg=PANEL)
        row.pack(fill="x", padx=12)
        self._button(row, "OPEN MUSIC", self.open_music, ACCENT).pack(side="left", fill="x", expand=True, padx=2)
        self._button(row, "PLAY", self.play, GREEN).pack(side="left", fill="x", expand=True, padx=2)
        self._button(row, "PAUSE", self.pause, GOLD).pack(side="left", fill="x", expand=True, padx=2)
        self._button(left, "STOP", self.stop, RED).pack(fill="x", padx=14, pady=(8, 4))
        self._button(left, "⚡ GENERATE .INO FOR MCU", self.open_export_dialog, "#9c5bff").pack(fill="x", padx=14, pady=(2, 8))
        self.seek_scale = ttk.Scale(left, from_=0, to=100, variable=self.seek_var,
                                    command=self._seek_changed)

        self.seek_scale.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(left, textvariable=self.time_var, bg=PANEL, fg=MUTED,
                 font=("Consolas", 9)).pack(anchor="e", padx=14)
        self._slider(left, "Music volume", self.volume_var, 0, 100, self._volume_changed)
        self._section(left, "LIGHT EFFECTS")
        ttk.Combobox(left, textvariable=self.mode_var, state="readonly",
                     values=["Music Reactive", "Bass Pulse", "Breathing", "Christmas Chase", "Rainbow",
                             "Wave", "Ocean Wave", "Dual Wave", "Comet", "Ripple",
                             "Vertical Flow", "Sparkle Wave", "Color Sweep", "Twinkle"],
                     font=("Segoe UI", 10)).pack(fill="x", padx=14, pady=(0, 9))
        tk.Label(left, text="COLOR PRESET", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(2, 3))
        ttk.Combobox(left, textvariable=self.color_var, state="readonly",
                     values=["Christmas", "Rainbow", "Warm White", "Blue Ice", "Red", "Green",
                             "Purple Neon", "Ocean", "Sunset", "Cotton Candy", "Custom Color", "Gradient"],
                     font=("Segoe UI", 10)).pack(fill="x", padx=14, pady=(0, 5))
        tk.Label(left, text="GRADIENT COMBINATION", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(3, 3))
        ttk.Combobox(left, textvariable=self.gradient_var, state="readonly",
                     values=["Red → Purple → Blue", "Sunset: Orange → Pink → Purple",
                             "Ocean: Cyan → Blue → Deep Blue", "Aurora: Green → Cyan → Violet",
                             "Fire: Red → Orange → Yellow", "Cotton Candy: Pink → Purple → Cyan",
                             "Gold → Warm White", "Emerald → Lime → Teal"],
                     font=("Segoe UI", 9)).pack(fill="x", padx=14, pady=(0, 5))
        self._button(left, "PICK CUSTOM COLOR", self.pick_custom_color, PANEL2).pack(fill="x", padx=14, pady=(0, 7))

        self._section(left, "DYNAMIC LIGHT SETS")
        tk.Label(left, text="Select which set to customize", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 3))
        set_row = tk.Frame(left, bg=PANEL)
        set_row.pack(fill="x", padx=14, pady=(0, 5))
        ttk.Combobox(set_row, textvariable=self.selected_set_type, state="readonly",
                     values=["LED Strip", "Christmas Lights"], width=17).pack(side="left", fill="x", expand=True)
        self.set_index_combo = ttk.Combobox(set_row, textvariable=self.selected_set_index,
                                            state="readonly", values=[str(i) for i in range(1, 6)], width=5)
        self.set_index_combo.pack(side="left", padx=(5, 0))
        tk.Label(left, text="Effect preset for selected set", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(2, 3))
        ttk.Combobox(left, textvariable=self.selected_set_effect, state="readonly",
                     values=self.effect_options, font=("Segoe UI", 9)).pack(fill="x", padx=14, pady=(0, 5))
        set_actions = tk.Frame(left, bg=PANEL)
        set_actions.pack(fill="x", padx=12, pady=(0, 7))
        self._button(set_actions, "APPLY TO SET", self.apply_set_effect, ACCENT).pack(side="left", fill="x", expand=True, padx=2)
        self._button(set_actions, "+ ADD SET", self.add_light_set, GREEN).pack(side="left", fill="x", expand=True, padx=2)
        self._button(set_actions, "− REMOVE", self.remove_light_set, RED).pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(left, text="INDIVIDUAL LIGHT EFFECTS", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(8, 4))
        tk.Label(left, text="Choose LED Strip or Christmas Lights above, then select a set and a specific light. Each light can keep its own effect.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=295,
                 justify="left").pack(anchor="w", padx=14, pady=(0, 6))
        emitter_row = tk.Frame(left, bg=PANEL)
        emitter_row.pack(fill="x", padx=14, pady=(0, 4))
        self.light_index_combo = ttk.Combobox(emitter_row, textvariable=self.selected_light_index,
                                              state="readonly", values=["All in set"] + [str(i) for i in range(1, 17)], width=10)
        self.light_index_combo.pack(side="left", fill="x", expand=True)
        self.light_effect_combo = ttk.Combobox(left, textvariable=self.selected_light_effect,
                     state="readonly", values=self.effect_options, font=("Segoe UI", 9))
        self.light_effect_combo.pack(fill="x", padx=14, pady=(0, 5))
        self._button(left, "APPLY EFFECT TO LIGHT(S)", self.apply_individual_effect, GREEN).pack(fill="x", padx=14, pady=(0, 4))
        self._button(left, "CHECKBOX: SELECT MULTIPLE / ALL", self.open_multi_light_selector, PANEL2).pack(fill="x", padx=14, pady=(0, 7))
        self.set_count_label = tk.Label(left, text="5 LED strips • 5 Christmas light sets",
                                        bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.set_count_label.pack(anchor="w", padx=14, pady=(0, 7))
        self.selected_set_type.trace_add("write", lambda *_: self._refresh_set_controls())
        self.selected_set_index.trace_add("write", lambda *_: self._refresh_set_controls())
        self.selected_light_index.trace_add("write", lambda *_: self._refresh_set_controls())
        self._slider(left, "Master brightness", self.brightness_var, 0, 100)
        self._slider(left, "Audio sensitivity", self.sensitivity_var, 0.5, 4.0)
        self._slider(left, "Breathing speed", self.breath_var, 0.1, 2.0)
        self._section(left, "OPTIONAL ESP32  •  NOT REQUIRED")
        conn = tk.Frame(left, bg=PANEL)
        conn.pack(fill="x", padx=14)
        tk.Label(conn, text="ESP32 IP", bg=PANEL, fg=MUTED).grid(row=0, column=0, sticky="w")
        tk.Entry(conn, textvariable=self.host_var, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                 relief="flat", width=18).grid(row=0, column=1, sticky="ew", padx=(8, 0), ipady=5)
        tk.Label(conn, text="UDP port", bg=PANEL, fg=MUTED).grid(row=1, column=0, sticky="w", pady=(7, 0))
        tk.Entry(conn, textvariable=self.port_var, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                 relief="flat", width=8).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(7, 0), ipady=5)
        conn.columnconfigure(1, weight=1)
        self._button(left, "TEST ESP32 CONNECTION", self.test_esp, PANEL2).pack(fill="x", padx=14, pady=10)
        tk.Label(left, text="Protocol: UDP JSON • 30 updates/sec", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=(0, 10))

        self._section(right, "LIVE LIGHT SIMULATION")
        self.canvas = tk.Canvas(right, bg="#0b1020", highlightthickness=0, height=380)
        self.canvas.pack(fill="both", expand=True, pady=(0, 10))
        meters = tk.Frame(right, bg=PANEL, padx=12, pady=12)
        meters.pack(fill="x")
        self.meter_bars = {}
        for key, label, color in [("bass", "BASS", "#ff5263"), ("mid", "MID", "#55d6be"),
                                  ("treble", "TREBLE", "#6fa8ff"), ("rms", "LEVEL", "#c792ea")]:
            box = tk.Frame(meters, bg=PANEL)
            box.pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(box, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            c = tk.Canvas(box, height=12, bg=PANEL2, highlightthickness=0)
            c.pack(fill="x", pady=(5, 0))
            self.meter_bars[key] = (c, color)
        bottom = tk.Frame(right, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        tk.Label(bottom, textvariable=self.status_var, bg=BG, fg=GREEN,
                 font=("Segoe UI", 9)).pack(side="left")
        self._button(bottom, "LIGHTS ON / OFF", self.toggle_lights, PANEL2).pack(side="right")


    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=parent["bg"], fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(15, 8))

    def _button(self, parent, text, command, color):
        return tk.Button(parent, text=text, command=command, bg=color, fg="#07111f" if color in (ACCENT, GREEN, GOLD) else TEXT,
                         activebackground=color, activeforeground=TEXT, relief="flat",
                         font=("Segoe UI", 9, "bold"), padx=9, pady=8, cursor="hand2")

    def _slider(self, parent, label, variable, low, high, command=None):
        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(fill="x", padx=14, pady=(4, 6))
        tk.Label(frame, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        scale = ttk.Scale(frame, from_=low, to=high, variable=variable, command=command)
        scale.pack(fill="x", pady=(2, 0))

    def open_music(self):
        path = filedialog.askopenfilename(title="Choose music",
            filetypes=[("Music files", "*.mp3 *.wav *.ogg *.flac"), ("All files", "*.*")])
        if not path:
            return
        try:
            pygame.mixer.music.load(path)
            self.track = path
            self.duration = self._get_duration(path)
            self.track_label.config(text=os.path.basename(path), fg=TEXT)
            self.seek_var.set(0)
            self.playback_offset = 0.0
            self.analysis_frames = []
            self.analysis_ready = False
            self.analysis_error = ""
            self._load_music_analysis(path)
            self.time_var.set(f"00:00 / {self._fmt(self.duration)}")
            if self.analysis_ready:
                self.status_var.set(f"Music loaded and analyzed — {len(self.analysis_frames)} analysis frames. Press PLAY")
            else:
                self.status_var.set("Analysis unavailable: " + (self.analysis_error or "install soundfile / use WAV or supported MP3"))
        except Exception as exc:
            messagebox.showerror("Could not load music", str(exc))

    def _get_duration(self, path):
        # pygame does not expose duration for every format; use sndinfo where possible.
        try:
            import mutagen
            audio = mutagen.File(path)
            if audio and audio.info:
                return float(audio.info.length)
        except Exception:
            pass
        return 0.0

    def _volume_changed(self, value=None):
        """Apply the Music volume slider to pygame playback immediately."""
        try:
            volume = max(0.0, min(1.0, float(self.volume_var.get()) / 100.0))
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(volume)
        except (ValueError, pygame.error):
            # Keep the UI responsive if the audio device is unavailable.
            pass

    def play(self):
        if not self.track:
            self.open_music()
            if not self.track:
                return
        try:
            if self.playing:
                return
            if self.playback_offset > 0 and self.playback_offset < self.duration:
                # pygame resumes a paused music stream with unpause(); for a fresh start use play().
                if getattr(self, "_was_paused", False) and pygame.mixer.music.get_busy():
                    pygame.mixer.music.unpause()
                else:
                    pygame.mixer.music.play(start=self.playback_offset)
            else:
                self.playback_offset = 0.0
                pygame.mixer.music.play()
            self.playback_started_at = time.monotonic()
            self.playing = True
            self._was_paused = False
            pygame.mixer.music.set_volume(self.volume_var.get() / 100)
            if self.analysis_ready:
                self.status_var.set("Playing loaded music file — analyzing its bass, mids, treble and beats (no mic)")
            else:
                self.status_var.set("NO AUDIO ANALYSIS — lights cannot follow music: " + (self.analysis_error or "unsupported audio format"))
        except Exception as exc:
            messagebox.showerror("Playback error", str(exc))

    def pause(self):
        if self.playing:
            self.playback_offset = self._playback_position()
            pygame.mixer.music.pause()
            self.playing = False
            self._was_paused = True
            self.status_var.set("Paused — music-file analysis paused too")

    def stop(self):
        pygame.mixer.music.stop()
        self.playing = False
        self._was_paused = False
        self.playback_offset = 0.0
        self.seek_var.set(0)
        self.bass = self.mid = self.treble = self.rms = self.beat = self.beat_strength = 0.0
        self.status_var.set("Stopped")

    def _playback_position(self):
        if self.playing:
            return max(0.0, self.playback_offset + (time.monotonic() - self.playback_started_at))
        return max(0.0, self.playback_offset)

    def _load_music_analysis(self, path):
        """Pre-analyze the selected music file itself. No mic, input device, or loopback."""
        if sf is None:
            self.analysis_error = "soundfile is not installed"
            return
        try:
            audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
            if audio.size == 0 or sample_rate <= 0:
                raise ValueError("The selected file contains no readable audio samples")

            mono = np.mean(audio, axis=1, dtype=np.float32)
            frame_size = 2048
            hop = max(512, int(sample_rate * 0.05))
            self.analysis_hop_seconds = hop / float(sample_rate)
            if len(mono) < frame_size:
                mono = np.pad(mono, (0, frame_size - len(mono)))

            window = np.hanning(frame_size).astype(np.float32)
            frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
            band_masks = (
                (frequencies >= 35) & (frequencies < min(180, sample_rate / 2)),
                (frequencies >= 180) & (frequencies < min(2500, sample_rate / 2)),
                (frequencies >= 2500) & (frequencies < min(10000, sample_rate / 2)),
            )
            frames = []
            fluxes = []
            previous_spectrum = None
            raw_bands = [[], [], []]
            raw_rms = []

            for start in range(0, max(1, len(mono)), hop):
                chunk = mono[start:start + frame_size]
                if len(chunk) < frame_size:
                    chunk = np.pad(chunk, (0, frame_size - len(chunk)))
                chunk = chunk - float(np.mean(chunk))
                rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
                spectrum = np.abs(np.fft.rfft(chunk * window))
                energies = []
                for mask in band_masks:
                    band = spectrum[mask]
                    energies.append(float(np.sqrt(np.mean(band * band) + 1e-12)) if len(band) else 0.0)
                flux = 0.0 if previous_spectrum is None else float(
                    np.mean(np.maximum(0.0, spectrum - previous_spectrum))
                )
                previous_spectrum = spectrum
                fluxes.append(flux)
                raw_rms.append(rms)
                for i, energy in enumerate(energies):
                    raw_bands[i].append(energy)
                frames.append({"time": start / float(sample_rate), "rms_raw": rms,
                               "bass_raw": energies[0], "mid_raw": energies[1],
                               "treble_raw": energies[2], "flux": flux})

            # Normalize each frequency band against this track's loud passages so
            # quiet/softly mastered files still produce visible light movement.
            def normalize_track(values):
                arr = np.asarray(values, dtype=np.float32)
                positive = arr[arr > 1e-8]
                scale = float(np.percentile(positive, 92)) if len(positive) else 1.0
                scale = max(scale, 1e-8)
                return np.clip(arr / scale, 0.0, 1.0)

            normalized_bands = [normalize_track(values) for values in raw_bands]
            normalized_rms = normalize_track(raw_rms)
            flux_array = np.asarray(fluxes, dtype=np.float32)
            positive_flux = flux_array[flux_array > 1e-8]
            flux_scale = float(np.percentile(positive_flux, 90)) if len(positive_flux) else 1.0
            flux_scale = max(flux_scale, 1e-8)

            for i, frame in enumerate(frames):
                frame["bass"] = float(normalized_bands[0][i])
                frame["mid"] = float(normalized_bands[1][i])
                frame["treble"] = float(normalized_bands[2][i])
                frame["rms"] = float(normalized_rms[i])
                frame["flux_norm"] = min(1.0, frame["flux"] / flux_scale)
                # Compare this onset against preceding frames only (not against itself).
                # Including the current frame in the baseline can suppress almost every beat.
                lo = max(0, i - 12)
                history = flux_array[lo:i]
                baseline = float(np.mean(history)) if len(history) else 0.0
                threshold = max(baseline * 1.12, flux_scale * 0.10, 1e-8)
                frame["beat"] = 1.0 if (i > 0 and frame["flux"] > threshold) else 0.0

            self.analysis_frames = frames
            self.analysis_ready = bool(frames)
            self.analysis_error = "" if self.analysis_ready else "No analysis frames were created"
        except Exception as exc:
            self.analysis_frames = []
            self.analysis_ready = False
            self.analysis_error = str(exc)

    def _seek_changed(self, value):
        if self.dragging_seek or not self.track or not self.duration:
            return
        # Seeking is performed when slider is released to avoid restarting playback continuously.
        # ttk.Scale has no portable release callback, so only update the preview display here.
        self.time_var.set(f"{self._fmt(float(value) / 100 * self.duration)} / {self._fmt(self.duration)}")

    def _analyze(self):
        """Read precomputed frequency/beat data at the current playback position."""
        if not self.analysis_ready or not self.analysis_frames:
            self.bass *= 0.92
            self.mid *= 0.92
            self.treble *= 0.92
            self.rms *= 0.92
            self.beat *= 0.80
            self.beat_strength *= 0.80
            return

        position = self._playback_position()
        # Use the actual frame spacing, including low sample-rate files.
        # Map playback time to the analyzed file timeline. Clamp to the final frame.
        index = min(len(self.analysis_frames) - 1,
                    max(0, int(position / max(self.analysis_hop_seconds, 1e-6))))
        frame = self.analysis_frames[index]
        sensitivity = max(0.1, float(self.sensitivity_var.get()))
        target_bass = min(1.0, frame["bass"] * sensitivity)
        target_mid = min(1.0, frame["mid"] * sensitivity)
        target_treble = min(1.0, frame["treble"] * sensitivity)

        # Follow the file's energy quickly enough for the changes to be visible.
        smoothing = 0.90 if self.playing else 0.25
        self._bass_smooth += (target_bass - self._bass_smooth) * smoothing
        self._mid_smooth += (target_mid - self._mid_smooth) * smoothing
        self._treble_smooth += (target_treble - self._treble_smooth) * smoothing
        self.bass = self._bass_smooth
        self.mid = self._mid_smooth
        self.treble = self._treble_smooth
        self.rms = frame["rms"]

        if self.playing and frame.get("beat", 0.0) > 0.5:
            self.beat = max(self.beat * 0.65, frame.get("flux_norm", 0.0), 0.85)
            self.beat_strength = self.beat
        else:
            self.beat *= 0.72
            self.beat_strength *= 0.72

    def test_esp(self):
        try:
            self._send_packet({"type": "ping", "app": "MusicLightSync"})
            self.status_var.set(f"Test packet sent to {self.host_var.get()}:{self.port_var.get()}")
        except Exception as exc:
            messagebox.showerror("ESP32 send failed", str(exc))

    def _send_packet(self, data):
        host = self.host_var.get().strip()
        port = int(self.port_var.get())
        self.udp_socket.sendto(__import__("json").dumps(data).encode("utf-8"), (host, port))

    def toggle_lights(self):
        self.leds_enabled = not self.leds_enabled
        self.status_var.set("Lights enabled" if self.leds_enabled else "Lights disabled")

    def pick_custom_color(self):
        chosen = colorchooser.askcolor(color=self.custom_color, title="Choose a custom light color")
        if chosen and chosen[1]:
            self.custom_color = chosen[1]
            self.color_var.set("Custom Color")
            self.status_var.set(f"Custom light color selected: {self.custom_color}")

    def _palette(self):
        name = self.color_var.get()
        palettes = {
            "Christmas": ["#ff334d", "#20e070", "#fff0c2", "#2675ff", "#ffbd32"],
            "Rainbow": ["#ff3b5c", "#ffb52e", "#f5f542", "#30e878", "#368bff", "#bc5cff"],
            "Warm White": ["#fff0cf", "#ffe1a0", "#fff8e8"],
            "Blue Ice": ["#8be9ff", "#348cff", "#e6faff", "#9ebcff"],
            "Red": ["#ff263f", "#ff6374", "#b50020"],
            "Green": ["#20ff70", "#00bd56", "#b0ffd0"],
            "Purple Neon": ["#ff45f6", "#a855f7", "#5b5bff", "#29106b"],
            "Ocean": ["#b7fff7", "#00d9ff", "#1677ff", "#082b65"],
            "Sunset": ["#fff06a", "#ff9f43", "#ff477e", "#8b3dff"],
            "Cotton Candy": ["#ff9bd2", "#d7a6ff", "#8cecff", "#fff0fa"],
            "Custom Color": [self.custom_color, "#ffffff", self.custom_color],
        }
        gradients = {
            "Red → Purple → Blue": ["#ff3158", "#d83bff", "#704bff", "#268cff", "#39d9ff"],
            "Sunset: Orange → Pink → Purple": ["#ffb02e", "#ff6b4a", "#ff3f87", "#c34cff", "#702cff"],
            "Ocean: Cyan → Blue → Deep Blue": ["#9afff4", "#21d9ff", "#168bff", "#2451d8", "#111b67"],
            "Aurora: Green → Cyan → Violet": ["#8dff7a", "#19f5b5", "#16d9ff", "#627bff", "#d15cff"],
            "Fire: Red → Orange → Yellow": ["#8f001b", "#ff2638", "#ff6b21", "#ffb52e", "#fff47a"],
            "Cotton Candy: Pink → Purple → Cyan": ["#ff8fcf", "#e58bff", "#ad8bff", "#80bfff", "#8cfff0"],
            "Gold → Warm White": ["#9c5b12", "#e6a83b", "#ffe19a", "#fff1cf", "#ffffff"],
            "Emerald → Lime → Teal": ["#006b55", "#00b878", "#65f26d", "#d8ff66", "#43f5c2"],
        }
        if name == "Gradient":
            return gradients.get(self.gradient_var.get(), gradients["Red → Purple → Blue"])
        return palettes.get(name, palettes["Christmas"])

    def _hex_rgb(self, color):
        return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))

    def _scale_color(self, color, factor):
        r, g, b = self._hex_rgb(color)
        factor = max(0.0, min(1.0, factor))
        return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"

    def _refresh_set_controls(self):
        """Keep set selector options and current preset synchronized."""
        items = self.strip_effects if self.selected_set_type.get() == "LED Strip" else self.christmas_effects
        values = [str(i) for i in range(1, len(items) + 1)]
        self.set_index_combo.configure(values=values)
        try:
            index = int(self.selected_set_index.get()) - 1
        except (ValueError, TypeError):
            index = 0
        if index < 0 or index >= len(items):
            self.selected_set_index.set(values[0])
            index = 0
        self.selected_set_effect.set(items[index])
        light_count = 16 if self.selected_set_type.get() == "LED Strip" else 8
        self.light_index_combo.configure(values=["All in set"] + [str(i) for i in range(1, light_count + 1)])
        try:
            light_num = int(self.selected_light_index.get())
        except (ValueError, TypeError):
            light_num = 0
        if light_num > light_count:
            self.selected_light_index.set("All in set")
        kind = "strip" if self.selected_set_type.get() == "LED Strip" else "bulb"
        emitter = self.selected_light_index.get()
        if emitter == "All in set":
            self.selected_light_effect.set("Use Global")
        else:
            self.selected_light_effect.set(self.individual_effects.get((kind, index, int(emitter)-1), "Use Global"))
        self.set_count_label.config(text=f"{len(self.strip_effects)} LED strips • {len(self.christmas_effects)} Christmas light sets")

    def apply_set_effect(self):
        items = self.strip_effects if self.selected_set_type.get() == "LED Strip" else self.christmas_effects
        try:
            index = int(self.selected_set_index.get()) - 1
        except (ValueError, TypeError):
            index = 0
        if not 0 <= index < len(items):
            return
        items[index] = self.selected_set_effect.get()
        self.status_var.set(f"Applied {items[index]} to {self.selected_set_type.get()} {index + 1}")
        self._draw_lights()

    def apply_individual_effect(self):
        """Apply an effect to one emitter, or all emitters in the selected set."""
        kind = "strip" if self.selected_set_type.get() == "LED Strip" else "bulb"
        try:
            set_index = int(self.selected_set_index.get()) - 1
        except (ValueError, TypeError):
            set_index = 0
        effect = self.selected_light_effect.get()
        emitter = self.selected_light_index.get()
        count = 16 if kind == "strip" else 8
        if emitter == "All in set":
            for light_index in range(count):
                key = (kind, set_index, light_index)
                if effect == "Use Global":
                    self.individual_effects.pop(key, None)
                else:
                    self.individual_effects[key] = effect
            message = f"Applied {effect} to all lights in {self.selected_set_type.get()} {set_index + 1}"
        else:
            light_index = int(emitter) - 1
            key = (kind, set_index, light_index)
            if effect == "Use Global":
                self.individual_effects.pop(key, None)
            else:
                self.individual_effects[key] = effect
            label = "LED" if kind == "strip" else "bulb"
            message = f"Applied {effect} to {self.selected_set_type.get()} {set_index + 1}, {label} {light_index + 1}"
        self.status_var.set(message)
        self._draw_lights()

    def open_multi_light_selector(self):
        """Open a checkbox dialog to apply an effect to any combination of lights."""
        kind = "strip" if self.selected_set_type.get() == "LED Strip" else "bulb"
        set_index = max(0, int(self.selected_set_index.get()) - 1)
        count = 16 if kind == "strip" else 8
        label = "LED" if kind == "strip" else "Christmas bulb"

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Select {label}s to edit")
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text=f"{self.selected_set_type.get()} {set_index + 1}: choose lights",
                 bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(dialog, text="Select one, several, or all lights, then apply an effect.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 8))

        choices = tk.Frame(dialog, bg=PANEL)
        choices.pack(padx=12, pady=2)
        selected = [tk.BooleanVar(value=False) for _ in range(count)]

        def set_all(value):
            for var in selected:
                var.set(value)

        all_var = tk.BooleanVar(value=False)
        def toggle_all():
            set_all(all_var.get())

        tk.Checkbutton(choices, text="Select all lights", variable=all_var, command=toggle_all,
                       bg=PANEL, fg=GOLD, selectcolor=PANEL2, activebackground=PANEL,
                       activeforeground=TEXT, font=("Segoe UI", 10, "bold")).grid(
                           row=0, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 7))

        columns = 4
        for i, var in enumerate(selected):
            tk.Checkbutton(choices, text=f"{label} {i + 1}", variable=var,
                           bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                           activebackground=PANEL, activeforeground=TEXT,
                           font=("Segoe UI", 9)).grid(row=1 + i // columns, column=i % columns,
                                                      sticky="w", padx=5, pady=3)

        effect_var = tk.StringVar(value=self.selected_light_effect.get())
        tk.Label(dialog, text="Effect to apply", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(10, 3))
        ttk.Combobox(dialog, textvariable=effect_var, state="readonly",
                     values=self.effect_options, font=("Segoe UI", 10), width=28).pack(
                         fill="x", padx=14, pady=(0, 10))

        buttons = tk.Frame(dialog, bg=PANEL)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        def apply_checked():
            chosen = [i for i, var in enumerate(selected) if var.get()]
            if not chosen:
                messagebox.showinfo("No lights selected", "Check at least one light, or choose Select all lights.", parent=dialog)
                return
            effect = effect_var.get()
            for light_index in chosen:
                key = (kind, set_index, light_index)
                if effect == "Use Global":
                    self.individual_effects.pop(key, None)
                else:
                    self.individual_effects[key] = effect
            self.selected_light_effect.set(effect)
            self.status_var.set(f"Applied {effect} to {len(chosen)} selected {label.lower()}(s) in set {set_index + 1}")
            self._draw_lights()
            dialog.destroy()

        self._button(buttons, "APPLY TO CHECKED LIGHTS", apply_checked, GREEN).pack(
            side="left", fill="x", expand=True, padx=3)
        self._button(buttons, "CANCEL", dialog.destroy, PANEL2).pack(side="left", padx=3)
        dialog.update_idletasks()
        dialog.geometry(f"{max(dialog.winfo_reqwidth(), 340)}x{dialog.winfo_reqheight()}")

    def open_export_dialog(self):
        """Open modal dialog to generate and export a standalone Arduino .ino sketch."""
        if not self.track:
            msg = messagebox.askyesno("No Music Loaded", "Please load a music file first before generating an .ino sketch.\n\nWould you like to open a music file now?")
            if msg:
                self.open_music()
            if not self.track:
                return

        dialog = tk.Toplevel(self.root)
        dialog.title("Export Standalone MCU .INO Sketch")
        dialog.configure(bg=PANEL)
        dialog.geometry("540x670")
        dialog.minsize(500, 600)
        dialog.transient(self.root)
        dialog.grab_set()

        hdr = tk.Frame(dialog, bg=PANEL)
        hdr.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(hdr, text="MCU .INO SKETCH GENERATOR", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(hdr, text="Bakes the music analysis & light choreography into a standalone Arduino sketch.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        card = tk.Frame(dialog, bg=PANEL2, padx=12, pady=10)
        card.pack(fill="x", padx=18, pady=(6, 12))
        track_name = os.path.basename(self.track)
        dur_str = self._fmt(self.duration)
        tk.Label(card, text=f"🎵 {track_name}", bg=PANEL2, fg=TEXT,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(card, text=f"Duration: {dur_str} ({self.duration:.1f} s) • Ready to bake to Flash ROM",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(2, 0))

        form = tk.Frame(dialog, bg=PANEL)
        form.pack(fill="both", expand=True, padx=18)

        export_mode_var = tk.StringVar(value="SD Card SHOW.DAT (No Re-flashing!)")
        tk.Label(form, text="EXPORT FORMAT", bg=PANEL, fg=GOLD,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 2))
        ttk.Combobox(form, textvariable=export_mode_var, state="readonly",
                     values=["SD Card SHOW.DAT (No Re-flashing!)",
                             "Standalone Arduino Sketch (.INO)"],
                     font=("Segoe UI", 9, "bold")).pack(fill="x", pady=(0, 6))

        def open_fw_sketch():
            fw_file = os.path.abspath("esp32_sd_music_player.ino")
            if os.path.exists(fw_file) and sys.platform == "win32":
                os.startfile(fw_file)

        self._button(form, "📄 FLASH ESP32 ONCE: OPEN PLAYER FIRMWARE (.INO)", open_fw_sketch, PANEL2).pack(fill="x", pady=(0, 8))

        mcu_var = tk.StringVar(value="ESP32 (Recommended)")
        tk.Label(form, text="TARGET MICROCONTROLLER (MCU)", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 2))
        ttk.Combobox(form, textvariable=mcu_var, state="readonly",
                     values=["ESP32 (Recommended)", "Arduino Uno / Nano / Mega", "Raspberry Pi Pico (RP2040)"],
                     font=("Segoe UI", 9)).pack(fill="x", pady=(0, 8))

        hw_var = tk.StringVar(value="Both (PWM MOSFETs + WS2812B NeoPixel)")
        tk.Label(form, text="LIGHTING HARDWARE", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 2))
        ttk.Combobox(form, textvariable=hw_var, state="readonly",
                     values=["Both (PWM MOSFETs + WS2812B NeoPixel)",
                             "Dual-Channel PWM MOSFETs Only (Strip & Xmas)",
                             "Addressable WS2812B NeoPixel Strip Only"],
                     font=("Segoe UI", 9)).pack(fill="x", pady=(0, 8))

        fx_row = tk.Frame(form, bg=PANEL)
        fx_row.pack(fill="x", pady=(0, 8))
        fx_col1 = tk.Frame(fx_row, bg=PANEL)
        fx_col1.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(fx_col1, text="EFFECT PRESET", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        effect_var = tk.StringVar(value=self.mode_var.get())
        ttk.Combobox(fx_col1, textvariable=effect_var, state="readonly",
                     values=self.effect_options[1:], font=("Segoe UI", 9)).pack(fill="x", pady=(2, 0))

        fx_col2 = tk.Frame(fx_row, bg=PANEL)
        fx_col2.pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(fx_col2, text="COLOR PALETTE", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        pal_var = tk.StringVar(value=self.color_var.get() if self.color_var.get() != "Gradient" else "Rainbow")
        ttk.Combobox(fx_col2, textvariable=pal_var, state="readonly",
                     values=["Christmas", "Rainbow", "Warm White", "Blue Ice", "Red", "Green",
                             "Purple Neon", "Ocean", "Sunset", "Fire", "Cotton Candy", "Custom Color"],
                     font=("Segoe UI", 9)).pack(fill="x", pady=(2, 0))

        fps_var = tk.StringVar(value="20 FPS (Recommended - 50ms)")
        tk.Label(form, text="TIMELINE RESOLUTION", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 2))
        ttk.Combobox(form, textvariable=fps_var, state="readonly",
                     values=["20 FPS (Recommended - 50ms)", "25 FPS (Ultra Smooth - 40ms)", "10 FPS (Compact - 100ms)"],
                     font=("Segoe UI", 9)).pack(fill="x", pady=(0, 8))

        pins_frame = tk.LabelFrame(form, text=" Hardware Pin Assignments ", bg=PANEL, fg=MUTED,
                                   font=("Segoe UI", 8, "bold"), padx=8, pady=6)
        pins_frame.pack(fill="x", pady=(0, 8))

        strip_pin_var = tk.StringVar(value="25")
        xmas_pin_var = tk.StringVar(value="26")
        pixel_pin_var = tk.StringVar(value="27")
        pixel_count_var = tk.StringVar(value="30")
        button_pin_var = tk.StringVar(value="0")

        tk.Label(pins_frame, text="Strip PWM Pin:", bg=PANEL, fg=TEXT, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", pady=2)
        tk.Entry(pins_frame, textvariable=strip_pin_var, bg=PANEL2, fg=TEXT, width=6, relief="flat", insertbackground=TEXT).grid(row=0, column=1, sticky="w", padx=6, pady=2)

        tk.Label(pins_frame, text="Xmas PWM Pin:", bg=PANEL, fg=TEXT, font=("Segoe UI", 8)).grid(row=0, column=2, sticky="w", pady=2)
        tk.Entry(pins_frame, textvariable=xmas_pin_var, bg=PANEL2, fg=TEXT, width=6, relief="flat", insertbackground=TEXT).grid(row=0, column=3, sticky="w", padx=6, pady=2)

        tk.Label(pins_frame, text="WS2812B Data Pin:", bg=PANEL, fg=TEXT, font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w", pady=2)
        tk.Entry(pins_frame, textvariable=pixel_pin_var, bg=PANEL2, fg=TEXT, width=6, relief="flat", insertbackground=TEXT).grid(row=1, column=1, sticky="w", padx=6, pady=2)

        tk.Label(pins_frame, text="WS2812B LED Count:", bg=PANEL, fg=TEXT, font=("Segoe UI", 8)).grid(row=1, column=2, sticky="w", pady=2)
        tk.Entry(pins_frame, textvariable=pixel_count_var, bg=PANEL2, fg=TEXT, width=6, relief="flat", insertbackground=TEXT).grid(row=1, column=3, sticky="w", padx=6, pady=2)

        tk.Label(pins_frame, text="Sync Button Pin:", bg=PANEL, fg=TEXT, font=("Segoe UI", 8)).grid(row=2, column=0, sticky="w", pady=2)
        tk.Entry(pins_frame, textvariable=button_pin_var, bg=PANEL2, fg=TEXT, width=6, relief="flat", insertbackground=TEXT).grid(row=2, column=1, sticky="w", padx=6, pady=2)

        tk.Label(pins_frame, text="(GPIO 0 = ESP32 BOOT button)", bg=PANEL, fg=MUTED, font=("Segoe UI", 7)).grid(row=2, column=2, columnspan=2, sticky="w", padx=6, pady=2)

        loop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(form, text="Auto-loop show when track ends", variable=loop_var,
                       bg=PANEL, fg=TEXT, selectcolor=PANEL2, activebackground=PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))

        note_lbl = tk.Label(form, text="Click below to choose where to save your file.",
                            bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        note_lbl.pack(anchor="w", pady=(0, 6))

        def do_export():
            try:
                hw_val = hw_var.get()
                if "PWM" in hw_val and "WS2812B" in hw_val:
                    hw_code = "both"
                elif "PWM" in hw_val:
                    hw_code = "pwm"
                else:
                    hw_code = "neopixel"

                fps_str = fps_var.get()
                fps_val = 20
                if "25" in fps_str:
                    fps_val = 25
                elif "10" in fps_str:
                    fps_val = 10

                is_sd = "SD Card" in export_mode_var.get()

                safe_default = os.path.splitext(os.path.basename(self.track))[0]
                import re
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', safe_default)
                if not safe_name or safe_name[0].isdigit():
                    safe_name = "Show_" + safe_name

                if is_sd:
                    save_path = filedialog.asksaveasfilename(
                        parent=dialog,
                        title="Save Show File for MicroSD Card",
                        initialfile="SHOW.DAT",
                        filetypes=[("SD Card Show File", "*.DAT"), ("All Files", "*.*")]
                    )
                else:
                    default_filename = f"{safe_name}_LightShow.ino"
                    save_path = filedialog.asksaveasfilename(
                        parent=dialog,
                        title="Save Arduino Sketch (.ino)",
                        initialfile=default_filename,
                        filetypes=[("Arduino Sketch", "*.ino"), ("All Files", "*.*")]
                    )

                if not save_path:
                    return

                note_lbl.config(text="Analyzing track and generating file, please wait...", fg=GOLD)
                dialog.update()

                from generate_ino import export_ino_file, export_sd_show_file

                if is_sd:
                    res = export_sd_show_file(
                        self.track,
                        output_path=save_path,
                        fps=fps_val,
                        hardware=hw_code,
                        effect_mode=effect_var.get(),
                        palette=pal_var.get(),
                        custom_color=self.custom_color,
                        auto_loop=loop_var.get()
                    )
                    dialog.destroy()
                    self._show_sd_export_success(res)
                else:
                    res = export_ino_file(
                        self.track,
                        output_path=save_path,
                        fps=fps_val,
                        hardware=hw_code,
                        effect_mode=effect_var.get(),
                        palette=pal_var.get(),
                        custom_color=self.custom_color,
                        strip_pwm_pin=int(strip_pin_var.get()),
                        xmas_pwm_pin=int(xmas_pin_var.get()),
                        neopixel_pin=int(pixel_pin_var.get()),
                        neopixel_count=int(pixel_count_var.get()),
                        button_pin=int(button_pin_var.get()),
                        auto_loop=loop_var.get()
                    )
                    dialog.destroy()
                    self._show_export_success(res)

            except Exception as exc:
                messagebox.showerror("Export Failed", f"Could not generate file:\n\n{exc}", parent=dialog)
                note_lbl.config(text=f"Error: {exc}", fg=RED)

        btn_row = tk.Frame(form, bg=PANEL)
        btn_row.pack(fill="x", pady=(4, 10))
        self._button(btn_row, "CANCEL", dialog.destroy, PANEL2).pack(side="left", padx=(0, 6))
        self._button(btn_row, "⚡ GENERATE SHOW FILE", do_export, "#9c5bff").pack(side="right", fill="x", expand=True, padx=(6, 0))

    def _show_sd_export_success(self, res):
        """Displays completion dialog for SD card show file."""
        succ = tk.Toplevel(self.root)
        succ.title("SD Card Show File Ready!")
        succ.configure(bg=PANEL)
        succ.geometry("560x390")
        succ.transient(self.root)
        succ.grab_set()

        tk.Label(succ, text="💾 SHOW.DAT READY FOR SD CARD!", bg=PANEL, fg=GREEN,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
        tk.Label(succ, text=f"Generated show file for '{res['title']}'. No re-flashing needed!",
                 bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(0, 10))

        info_box = tk.Frame(succ, bg=PANEL2, padx=12, pady=10)
        info_box.pack(fill="x", padx=18, pady=(0, 14))

        tk.Label(info_box, text=f"• Total Duration: {self._fmt(res['duration'])} ({res['duration']:.1f} s)",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(info_box, text=f"• Keyframes: {res['frames']} baked @ {res['fps']:.1f} FPS",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(info_box, text=f"• File Size: {res['total_bytes']:,} bytes (~{res['total_bytes']//1024} KB)",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(info_box, text=f"• Saved File:\n  {res['dat_path']}",
                 bg=PANEL2, fg=ACCENT, font=("Segoe UI", 8, "bold"), justify="left", wraplength=500).pack(anchor="w", pady=(4, 0))

        tk.Label(succ, text="How to play on ESP32:\n1. Copy 'SHOW.DAT' directly to your MicroSD card root directory.\n2. Insert the card into your ESP32 running 'esp32_sd_music_player.ino'.\n3. The ESP32 starts playing immediately!",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=18, pady=(0, 12))

        btn_row = tk.Frame(succ, bg=PANEL)
        btn_row.pack(fill="x", padx=18, pady=(0, 12))

        def open_folder():
            folder = res["dat_dir"]
            if os.path.exists(folder):
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.run(["open", folder])
                else:
                    import subprocess
                    subprocess.run(["xdg-open", folder])

        def open_esp_firmware():
            fw_path = os.path.abspath("esp32_sd_music_player.ino")
            if os.path.exists(fw_path) and sys.platform == "win32":
                os.startfile(fw_path)

        self._button(btn_row, "📂 OPEN FILE FOLDER", open_folder, ACCENT).pack(side="left", padx=(0, 6))
        self._button(btn_row, "⚡ OPEN ESP32 FIRMWARE", open_esp_firmware, GOLD).pack(side="left", padx=(0, 6))
        self._button(btn_row, "CLOSE", succ.destroy, PANEL2).pack(side="right")


    def _show_export_success(self, res):
        """Displays a polished completion dialog with file paths and open folder shortcut."""
        succ = tk.Toplevel(self.root)
        succ.title("Arduino .INO Sketch Generated Successfully!")
        succ.configure(bg=PANEL)
        succ.geometry("540x360")
        succ.transient(self.root)
        succ.grab_set()

        tk.Label(succ, text="🎉 .INO SKETCH READY TO FLASH!", bg=PANEL, fg=GREEN,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
        tk.Label(succ, text=f"Generated standalone light show sketch for '{res['title']}'.",
                 bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(0, 10))

        info_box = tk.Frame(succ, bg=PANEL2, padx=12, pady=10)
        info_box.pack(fill="x", padx=18, pady=(0, 14))

        tk.Label(info_box, text=f"• Total Duration: {self._fmt(res['duration'])} ({res['duration']:.1f} s)",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(info_box, text=f"• Keyframes: {res['frames']} baked @ {res['fps']:.1f} FPS",
                 bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(info_box, text=f"• Sketch Location:\n  {res['ino_path']}",
                 bg=PANEL2, fg=ACCENT, font=("Segoe UI", 8, "bold"), justify="left", wraplength=480).pack(anchor="w", pady=(4, 0))

        tk.Label(succ, text="How to flash:\n1. Open the .ino file in Arduino IDE.\n2. Select your Board (e.g. ESP32 Dev Module) and COM port.\n3. Click 'Upload' to flash directly to your MCU!",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=18, pady=(0, 12))

        btn_row = tk.Frame(succ, bg=PANEL)
        btn_row.pack(fill="x", padx=18, pady=(0, 12))

        def open_folder():
            folder = res["sketch_dir"]
            if os.path.exists(folder):
                if sys.platform == "win32":
                    os.startfile(folder)
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.run(["open", folder])
                else:
                    import subprocess
                    subprocess.run(["xdg-open", folder])

        self._button(btn_row, "📂 OPEN FOLDER IN FILE EXPLORER", open_folder, ACCENT).pack(side="left", padx=(0, 6))
        self._button(btn_row, "CLOSE", succ.destroy, PANEL2).pack(side="right")


    def add_light_set(self):
        items = self.strip_effects if self.selected_set_type.get() == "LED Strip" else self.christmas_effects
        if len(items) >= 20:
            messagebox.showinfo("Set limit", "The simulator supports up to 20 sets of each type.")
            return
        items.append("Use Global")
        self.selected_set_index.set(str(len(items)))
        self.selected_set_effect.set("Use Global")
        self._refresh_set_controls()
        self.status_var.set(f"Added {self.selected_set_type.get()} {len(items)}")
        self._draw_lights()

    def remove_light_set(self):
        items = self.strip_effects if self.selected_set_type.get() == "LED Strip" else self.christmas_effects
        if len(items) <= 1:
            messagebox.showinfo("Cannot remove", "Keep at least one set of each type.")
            return
        try:
            index = int(self.selected_set_index.get()) - 1
        except (ValueError, TypeError):
            index = len(items) - 1
        index = max(0, min(index, len(items) - 1))
        items.pop(index)
        self.selected_set_index.set(str(min(index + 1, len(items))))
        self._refresh_set_controls()
        self.status_var.set(f"Removed a {self.selected_set_type.get()} set")
        self._draw_lights()

    def _draw_lights(self):
        """Render five vertical LED-strip sets and five vertical Christmas-light strings.

        Each set contains many individually animated emitters. Their brightness responds
        to bass/mid/treble energy and transient beat strength, rather than showing only
        five individual dots.
        """
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 500)
        h = max(c.winfo_height(), 360)
        t = time.monotonic()
        mode = self.mode_var.get()
        palette = self._palette()
        master = self.brightness_var.get() / 100 if self.leds_enabled else 0.0
        breath = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * self.breath_var.get() * 2.2))
        reactive = min(1.0, self.bass * 0.82 + self.mid * 0.62 + self.treble * 0.48 + self.beat * 0.90)
        pulse = min(1.0, self.beat)

        def blend(color, factor):
            return self._scale_color(color, max(0.0, min(1.0, factor)))

        def light_factor(index, kind="strip", position=0):
            # Individual emitter overrides take priority over the set and global effects.
            presets = self.strip_effects if kind == "strip" else self.christmas_effects
            local_mode = self.individual_effects.get((kind, index, position), "Use Global")
            if local_mode == "Use Global":
                local_mode = presets[index] if index < len(presets) else "Use Global"
            if local_mode == "Use Global":
                local_mode = mode
            # Spatially distinct wave patterns; position is the pixel/bulb along a strand.
            phase = t * 5.0 - position * 0.55 + index * 0.42
            wave = 0.5 + 0.5 * math.sin(phase)
            wave2 = 0.5 + 0.5 * math.sin(t * 4.0 + position * 0.72 - index * 0.8)
            if local_mode == "Breathing":
                value = breath
            elif local_mode == "Bass Pulse":
                value = 0.05 + self.bass * 0.95 + self.beat * 0.35
            elif local_mode == "Music Reactive":
                value = 0.05 + reactive * 0.90 + self.beat * 0.65
            elif local_mode == "Christmas Chase":
                count = 5
                active = int(t * (2.5 if kind == "strip" else 1.7)) % count
                distance = min((index - active) % count, (active - index) % count)
                value = (1.0 if distance == 0 else 0.45 if distance == 1 else 0.18) * (0.45 + 0.55 * wave)
            elif local_mode == "Wave":
                value = 0.10 + 0.90 * (wave ** 1.8)
            elif local_mode == "Ocean Wave":
                value = 0.12 + 0.88 * (0.5 + 0.5 * math.sin(t * 3.0 - position * 0.85 + math.sin(index * 0.7)))
            elif local_mode == "Dual Wave":
                value = 0.08 + 0.92 * max(wave, wave2 * 0.88)
            elif local_mode == "Comet":
                head = (t * 7.0 + index * 3.0) % max(2, (16 if kind == "strip" else 8))
                distance = abs(position - head)
                value = 0.06 + 0.94 * math.exp(-distance * 0.65)
            elif local_mode == "Ripple":
                value = 0.08 + 0.92 * (0.5 + 0.5 * math.sin(t * 7.0 - abs(position - (3.5 + 3.5 * math.sin(t * 1.5))) * 1.8))
            elif local_mode == "Vertical Flow":
                value = 0.12 + 0.88 * (0.5 + 0.5 * math.sin(position * 0.95 - t * 6.0 + index * 0.35))
            elif local_mode == "Sparkle Wave":
                sparkle = 0.32 if math.sin(t * 17 + position * 9.3 + index * 13.1) > 0.82 else 0.0
                value = min(1.0, 0.12 + wave * 0.72 + sparkle)
            elif local_mode == "Color Sweep":
                value = 0.18 + 0.82 * (0.5 + 0.5 * math.sin(t * 2.8 - position * 0.65 + index * 0.5))
            elif local_mode == "Twinkle":
                twinkle = 0.5 + 0.5 * math.sin(t * (5.0 + index * 0.23) + position * 4.7)
                value = 0.12 + 0.88 * (twinkle ** 3)
            elif local_mode == "Rainbow" or self.color_var.get() == "Rainbow":
                value = 0.55 + 0.25 * breath + 0.20 * wave
            else:
                value = 0.35 + 0.30 * breath + 0.25 * wave + 0.10 * pulse
            audio_boost = 0.12 + reactive * 0.25 + pulse * 0.20
            return max(0.015, min(1.0, master * value * (0.72 + audio_boost)))

        c.create_rectangle(0, 0, w, h, fill="#080d18", outline="")
        c.create_rectangle(0, int(h * 0.82), w, h, fill="#101a2a", outline="")
        c.create_line(0, int(h * 0.82), w, int(h * 0.82), fill="#273750", width=2)
        c.create_text(18, 12, anchor="nw", text="MUSIC LIGHT SYNC  /  VERTICAL LIVE PULSE VIEW",
                      fill="#a9bad4", font=("Segoe UI", 9, "bold"))

        # Dynamic vertical LED strip sets. Width adapts to the configured set count.
        left_margin = 24
        section_gap = 24
        half_w = (w - left_margin * 2 - section_gap) / 2
        strip_left = left_margin
        strip_right = strip_left + half_w
        strip_top = 62
        strip_bottom = int(h * 0.72)
        strip_height = max(100, strip_bottom - strip_top)
        c.create_text(strip_left, 37, anchor="w", text=f"{len(self.strip_effects)} VERTICAL RGB LED STRIP SETS",
                      fill="#a9bad4", font=("Segoe UI", 9, "bold"))
        strip_count = len(self.strip_effects)
        strip_set_w = max(4, (half_w - 24) / max(1, strip_count))
        pixel_count = 16
        for i in range(strip_count):
            x_center = strip_left + 12 + strip_set_w * (i + 0.5)
            channel_w = max(3, min(20, strip_set_w * 0.48))
            # Diffuser channel and aluminium border.
            c.create_rectangle(x_center-channel_w/2-4, strip_top-5,
                               x_center+channel_w/2+4, strip_bottom+5,
                               fill="#111b2a", outline="#43536c", width=1)
            c.create_rectangle(x_center-channel_w/2, strip_top,
                               x_center+channel_w/2, strip_bottom,
                               fill="#253247", outline="#61728b", width=1)
            for j in range(pixel_count):
                y = strip_bottom - (j + 0.5) * strip_height / pixel_count
                pos = j / max(1, pixel_count-1)
                if mode in ("Rainbow", "Color Sweep", "Ocean Wave", "Vertical Flow", "Comet", "Ripple") or self.color_var.get() == "Rainbow":
                    speed = 2 if mode in ("Ocean Wave", "Ripple") else 4
                    color = palette[(j + i * 2 + int(t * speed)) % len(palette)]
                elif mode in ("Wave", "Dual Wave", "Sparkle Wave"):
                    color = palette[(i + (j // 2) + int(t * 1.5)) % len(palette)]
                elif mode == "Christmas Chase":
                    color = palette[(i + j + int(t * 3)) % len(palette)]
                else:
                    color = palette[(i + (j // 3)) % len(palette)]
                # Brightness is a mix of frequency-band energy and the beat envelope.
                band = self.bass if pos < 0.34 else self.mid if pos < 0.68 else self.treble
                # Lift low-level signals and let each frequency band visibly affect pixels.
                factor = light_factor(i, "strip", j) * (0.10 + 0.90 * max(band, pulse * 0.95))
                px = channel_w * 0.34
                py = max(2.5, strip_height / pixel_count * 0.28)
                # Layered bloom: broad dim spill, tighter saturated glow, bright LED core.
                # Tkinter has no alpha blending, so stacked darkened color layers simulate bloom.
                for radius, alpha in ((9, .13), (6, .24), (3.5, .42)):
                    c.create_oval(x_center-px-radius, y-py-radius,
                                  x_center+px+radius, y+py+radius,
                                  fill=blend(color, factor * alpha), outline="")
                c.create_oval(x_center-px, y-py, x_center+px, y+py,
                              fill=blend(color, factor), outline=blend("#e9f4ff", factor * .34))
                # Small pale center makes each addressable pixel look like a lens, not a flat block.
                core_w, core_h = px * .34, py * .48
                c.create_oval(x_center-core_w, y-py*.52,
                              x_center+core_w, y-py*.52+core_h,
                              fill=blend("#ffffff", factor * .72), outline="")
            c.create_text(x_center, strip_bottom+17, text=f"STRIP {i+1}",
                          fill="#8ea4c6", font=("Segoe UI", 7, "bold"))

        # Dynamic vertical Christmas-light sets; each strand has several glass bulbs.
        x0 = strip_right + section_gap
        c.create_text(x0, 37, anchor="w", text=f"{len(self.christmas_effects)} VERTICAL CHRISTMAS LIGHT SETS",
                      fill="#a9bad4", font=("Segoe UI", 9, "bold"))
        x_room = w - 24 - x0
        christmas_count = len(self.christmas_effects)
        strand_gap = max(4, x_room / max(1, christmas_count))
        bulb_count = 8
        x_top = strip_top + 5
        x_bottom = strip_bottom - 5
        for i in range(christmas_count):
            x = x0 + strand_gap * (i + 0.5)
            # Vertical hanging wire with subtle bends.
            wire_points = []
            for k in range(9):
                yy = x_top + (x_bottom-x_top) * k / 8
                xx = x + math.sin(k * 0.85 + i * 0.7) * min(4, strand_gap * 0.12)
                wire_points.extend((xx, yy))
            c.create_line(*wire_points, fill="#0b111c", width=5, smooth=True)
            c.create_line(*wire_points, fill="#526078", width=2, smooth=True)
            for j in range(bulb_count):
                y = x_top + (j + 0.5) * (x_bottom-x_top) / bulb_count
                xx = x + math.sin((j + 0.5) * 0.85 + i * 0.7) * min(4, strand_gap * 0.12)
                if mode in ("Rainbow", "Color Sweep", "Ocean Wave", "Vertical Flow", "Comet", "Ripple") or self.color_var.get() == "Rainbow":
                    color = palette[(i + j + int(t * 3)) % len(palette)]
                elif mode in ("Wave", "Dual Wave", "Sparkle Wave"):
                    color = palette[(i + j + int(t * 1.5)) % len(palette)]
                else:
                    color = palette[(i + j) % len(palette)]
                factor = light_factor(i, "bulb", j)
                # Let high-frequency energy sparkle the bulbs while bass drives the main pulse.
                factor *= 0.12 + 0.68 * max(self.bass, self.mid, self.treble) + 0.40 * pulse
                radius = max(3.2, min(5.5, strand_gap * 0.18))
                # Three bloom rings give the bulb a soft colored aura against the dark room.
                for halo, alpha in ((radius*4.2, .09), (radius*3.1, .17),
                                    (radius*2.2, .30), (radius*1.45, .48)):
                    c.create_oval(xx-halo, y-halo, xx+halo, y+halo,
                                  fill=blend(color, factor * alpha), outline="")
                c.create_line(xx, y-radius-5, xx, y-radius,
                              fill="#8b9bb0", width=2)
                c.create_oval(xx-radius, y-radius, xx+radius, y+radius,
                              fill=blend(color, factor), outline=blend("#fff8e8", factor*.7), width=1)
                # Curved-looking specular glint and a tiny reflected highlight.
                c.create_oval(xx-radius*.42, y-radius*.62,
                              xx+radius*.04, y-radius*.12,
                              fill=blend("#ffffff", factor*.98), outline="")
                c.create_oval(xx+radius*.18, y+radius*.12,
                              xx+radius*.38, y+radius*.32,
                              fill=blend("#ffffff", factor*.42), outline="")
            c.create_text(x, strip_bottom+17, text=f"TREE {i+1}",
                          fill="#8ea4c6", font=("Segoe UI", 7, "bold"))

        # Bottom pulse meter: audio intensity moves with music energy.
        meter_y = int(h * 0.80)
        meter_x1, meter_x2 = 24, w - 24
        c.create_text(meter_x1, meter_y-12, anchor="w", text="LIVE MUSIC PULSE / FREQUENCY ENERGY",
                      fill="#a9bad4", font=("Segoe UI", 8, "bold"))
        c.create_rectangle(meter_x1, meter_y, meter_x2, meter_y+10,
                           fill="#18263b", outline="#3b4b64")
        level = min(1.0, self.bass * 0.48 + self.mid * 0.27 + self.treble * 0.10 + pulse * 0.45)
        if level > 0:
            c.create_rectangle(meter_x1+2, meter_y+2,
                               meter_x1+2+(meter_x2-meter_x1-4)*level, meter_y+8,
                               fill=blend("#42d7ff", master), outline="")
        c.create_text(meter_x1, meter_y+24,
                      text=f"BASS {self.bass:.2f}   MID {self.mid:.2f}   TREBLE {self.treble:.2f}   BEAT {pulse:.2f}",
                      anchor="w", fill="#8ea4c6", font=("Consolas", 8, "bold"))
        status_color = "#47d18c" if self.leds_enabled else "#ff647c"
        c.create_oval(w-28, 12, w-18, 22, fill=status_color, outline="")
        c.create_text(w-34, 17, anchor="e", text="ON" if self.leds_enabled else "OFF",
                      fill=status_color, font=("Segoe UI", 8, "bold"))

    def _update_meters(self):
        vals = {"bass": self.bass, "mid": self.mid, "treble": self.treble, "rms": min(1, self.rms*5)}
        for key, value in vals.items():
            canvas, color = self.meter_bars[key]
            canvas.delete("bar")
            w = canvas.winfo_width()
            canvas.create_rectangle(0, 0, max(0, w*value), 12, fill=color, outline="", tags="bar")

    def _update_loop(self):
        self._analyze()
        self._draw_lights()
        self._update_meters()
        if self.playing:
            if self.duration > 0:
                elapsed = min(self.duration, max(0, self._playback_position()))
                if not self.dragging_seek:
                    self.seek_var.set(elapsed / self.duration * 100)
                self.time_var.set(f"{self._fmt(elapsed)} / {self._fmt(self.duration)}")
            # Optional hardware output. The virtual lights work independently of this UDP path.
            try:
                if self.leds_enabled:
                    self._send_packet({
                        "type": "lights",
                        "mode": self.mode_var.get(),
                        "brightness": round(self.brightness_var.get() / 100, 3),
                        "bass": round(self.bass, 3),
                        "mid": round(self.mid, 3),
                        "treble": round(self.treble, 3),
                        "level": round(min(1, self.rms * 5), 3),
                        "beat": round(self.beat, 3),
                        "breath_speed": round(self.breath_var.get(), 3),
                        "color": self.color_var.get(),
                        "gradient": self.gradient_var.get(),
                        "custom_color": self.custom_color
                    })
            except Exception:
                pass
        self.root.after(33, self._update_loop)

    def _fmt(self, seconds):
        seconds = max(0, int(seconds))
        return f"{seconds//60:02d}:{seconds%60:02d}"

    def close(self):
        try:
            self._send_packet({"type": "lights", "brightness": 0, "mode": "Off"})
        except Exception:
            pass
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
            self.udp_socket.close()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MusicLightSimulator(root)
    root.mainloop()

if __name__ == "__main__":
    main()
