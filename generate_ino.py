"""
Standalone Microcontroller .INO Generator for Music Light Sync.

Analyzes an uploaded music track (MP3, WAV, FLAC, OGG) and exports a complete,
self-contained Arduino (.ino) sketch that can be flashed directly to an ESP32 or
Arduino microcontroller to drive music-synchronized lights without requiring a PC.

Supports:
- Dual-channel / Multi-channel PWM (MOSFET drivers for 12V/24V LED strips & Christmas lights)
- Addressable RGB LEDs (WS2812B / NeoPixel) with reactive wave and beat-burst choreography
- Hardware sync button (e.g. ESP32 on-board BOOT button GPIO 0)
- Serial Monitor control & live visual progress bar
"""

import argparse
import math
import os
import re
import sys
import numpy as np

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import mutagen
except ImportError:
    mutagen = None


# Standard color palettes (matching the desktop simulator)
COLOR_PALETTES = {
    "Christmas": ["0xFF334D", "0x20E070", "0xFFF0C2", "0x2675FF", "0xFFBD32"],
    "Rainbow": ["0xFF3B5C", "0xFFB52E", "0xF5F542", "0x30E878", "0x368BFF", "0xBC5CFF"],
    "Warm White": ["0xFFF0CF", "0xFFE1A0", "0xFFF8E8"],
    "Blue Ice": ["0x8BE9FF", "0x348CFF", "0xE6FAFF", "0x9EBCFF"],
    "Red": ["0xFF263F", "0xFF6374", "0xB50020"],
    "Green": ["0x20FF70", "0x00BD56", "0xB0FFD0"],
    "Purple Neon": ["0xFF45F6", "0xA855F7", "0x5B5BFF", "0x29106B"],
    "Ocean": ["0xB7FFF7", "0x00D9FF", "0x1677FF", "0x082B65"],
    "Sunset": ["0xFFF06A", "0xFF9F43", "0xFF477E", "0x8B3DFF"],
    "Fire": ["0x8F001B", "0xFF2638", "0xFF6B21", "0xFFB52E", "0xFFF47A"],
    "Cotton Candy": ["0xFF9BD2", "0xD7A6FF", "0x8CECFF", "0xFFF0FA"],
    "Emerald": ["0x006B55", "0x00B878", "0x65F26D", "0xD8FF66", "0x43F5C2"],
}


def hex_to_c_rgb(hex_str):
    """Converts '#RRGGBB' to '0xRRGGBB'."""
    hex_clean = hex_str.strip().lstrip("#")
    if len(hex_clean) == 6:
        return f"0x{hex_clean.upper()}"
    return "0x58A6FF"


def analyze_audio_file(audio_path, target_fps=20):
    """
    Analyzes an audio file and produces timeline frames with normalized
    RMS energy, Bass, Mid, Treble frequency bands, and beat detection.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Fallback to pygame audio loading if soundfile cannot read format directly
    audio = None
    sample_rate = 44100

    if sf is not None:
        try:
            audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        except Exception:
            audio = None

    if audio is None:
        # Try pygame mixer Sound extraction or wave module
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        sound = pygame.mixer.Sound(audio_path)
        raw_array = pygame.sndarray.array(sound)
        sample_rate = pygame.mixer.get_init()[0]
        if raw_array.ndim == 1:
            audio = (raw_array.astype(np.float32) / 32768.0)[:, np.newaxis]
        else:
            audio = raw_array.astype(np.float32) / 32768.0

    if audio.size == 0 or sample_rate <= 0:
        raise ValueError("Audio file has no readable samples.")

    mono = np.mean(audio, axis=1, dtype=np.float32)
    duration = len(mono) / float(sample_rate)

    # Frame and hop configuration
    frame_size = 2048
    hop = max(256, int(sample_rate / target_fps))
    actual_hop_seconds = hop / float(sample_rate)
    actual_fps = 1.0 / actual_hop_seconds

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

        frames.append({
            "time": start / float(sample_rate),
            "rms_raw": rms,
            "bass_raw": energies[0],
            "mid_raw": energies[1],
            "treble_raw": energies[2],
            "flux": flux
        })

    # Track normalization
    def normalize_series(values, percentile=92):
        arr = np.asarray(values, dtype=np.float32)
        pos = arr[arr > 1e-8]
        scale = float(np.percentile(pos, percentile)) if len(pos) else 1.0
        scale = max(scale, 1e-8)
        return np.clip(arr / scale, 0.0, 1.0)

    normalized_bands = [normalize_series(v, 92) for v in raw_bands]
    normalized_rms = normalize_series(raw_rms, 90)
    flux_arr = np.asarray(fluxes, dtype=np.float32)
    pos_flux = flux_arr[flux_arr > 1e-8]
    flux_scale = float(np.percentile(pos_flux, 90)) if len(pos_flux) else 1.0
    flux_scale = max(flux_scale, 1e-8)

    for i, frame in enumerate(frames):
        frame["bass"] = float(normalized_bands[0][i])
        frame["mid"] = float(normalized_bands[1][i])
        frame["treble"] = float(normalized_bands[2][i])
        frame["rms"] = float(normalized_rms[i])
        frame["flux_norm"] = min(1.0, frame["flux"] / flux_scale)

        # Beat detection: compare against prior window
        lo = max(0, i - 12)
        history = flux_arr[lo:i]
        baseline = float(np.mean(history)) if len(history) else 0.0
        threshold = max(baseline * 1.15, flux_scale * 0.12, 1e-8)
        is_beat = 1.0 if (i > 0 and frame["flux"] > threshold) else 0.0
        frame["beat"] = is_beat
        frame["beat_strength"] = frame["flux_norm"] if is_beat > 0.5 else 0.0

    return {
        "title": os.path.splitext(os.path.basename(audio_path))[0],
        "duration": duration,
        "sample_rate": sample_rate,
        "hop_seconds": actual_hop_seconds,
        "fps": actual_fps,
        "frames": frames
    }


def bake_choreography(analysis, effect_mode="Music Reactive", brightness=1.0, sensitivity=1.5):
    """
    Bakes audio analysis frames into discrete light channels:
    - strip_pwm: 0-255
    - xmas_pwm:  0-255
    - bass:      0-255
    - mid:       0-255
    - treble:    0-255
    - beat:      0-255
    """
    frames = analysis["frames"]
    baked = []
    breath_speed = 0.45

    def clamp255(v):
        if v <= 0.0:
            return 0
        if v >= 1.0:
            return 255
        return int(v * 255.0)

    for frame in frames:
        t = frame["time"]
        b = min(1.0, frame["bass"] * sensitivity)
        m = min(1.0, frame["mid"] * sensitivity)
        tr = min(1.0, frame["treble"] * sensitivity)
        rms = min(1.0, frame["rms"] * sensitivity)
        beat = frame.get("beat_strength", frame.get("beat", 0.0))

        # Calculate effect-based PWM values
        breath = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(t * breath_speed * 2.2))

        if effect_mode == "Bass Pulse":
            strip_val = brightness * (0.12 + 0.88 * b)
            xmas_val = brightness * (0.12 + 0.88 * b)
        elif effect_mode == "Breathing":
            strip_val = brightness * breath
            xmas_val = brightness * breath
        elif effect_mode == "Christmas Chase":
            chase_phase = 0.5 + 0.5 * math.sin(t * 3.0)
            strip_val = brightness * (0.2 + 0.6 * chase_phase + 0.2 * b)
            xmas_val = brightness * (0.2 + 0.6 * (1.0 - chase_phase) + 0.2 * beat)
        elif effect_mode == "Rainbow" or effect_mode == "Wave":
            wave_val = 0.5 + 0.5 * math.sin(t * 2.0)
            strip_val = brightness * (0.25 + 0.55 * wave_val + 0.20 * b)
            xmas_val = brightness * (0.25 + 0.55 * (1.0 - wave_val) + 0.20 * m)
        else:
            # Default "Music Reactive"
            strip_val = brightness * (0.18 + 0.55 * b + 0.20 * m + 0.25 * beat)
            xmas_val = brightness * (0.15 + 0.65 * rms + 0.25 * beat)

        baked.append((
            clamp255(strip_val),
            clamp255(xmas_val),
            clamp255(b),
            clamp255(m),
            clamp255(tr),
            clamp255(beat)
        ))

    return baked



def generate_ino_sketch(analysis, baked_frames, options=None):
    """
    Generates a production-ready Arduino C++ .ino sketch text.
    """
    opts = {
        "hardware": "both",              # "both", "pwm", "neopixel"
        "target_mcu": "ESP32",            # "ESP32", "Arduino"
        "strip_pwm_pin": 25,              # ESP32 pin 25 (or 5 on Arduino)
        "xmas_pwm_pin": 26,               # ESP32 pin 26 (or 6 on Arduino)
        "neopixel_pin": 27,               # WS2812B data pin
        "neopixel_count": 30,             # Number of addressable LEDs
        "button_pin": 0,                  # Sync restart push-button pin (0 = ESP32 BOOT button)
        "auto_loop": True,                # Loop show when finished
        "palette": "Christmas",           # Palette name
        "custom_color": "#58a6ff",        # Custom hex color if chosen
        "effect_mode": "Music Reactive",
    }
    if options:
        opts.update(options)

    title = analysis["title"]
    duration = analysis["duration"]
    duration_ms = int(duration * 1000)
    fps = round(analysis["fps"], 2)
    interval_ms = max(1, int(round(analysis["hop_seconds"] * 1000)))
    frame_count = len(baked_frames)
    total_sec = int(duration)
    duration_fmt = f"{total_sec // 60:02d}:{total_sec % 60:02d}"

    enable_pwm = 1 if opts["hardware"] in ("both", "pwm") else 0
    enable_neopixel = 1 if opts["hardware"] in ("both", "neopixel") else 0

    # Build color palette C array
    palette_name = opts["palette"]
    if palette_name == "Custom Color":
        raw_colors = [hex_to_c_rgb(opts["custom_color"]), "0xFFFFFF", hex_to_c_rgb(opts["custom_color"])]
    else:
        raw_colors = COLOR_PALETTES.get(palette_name, COLOR_PALETTES["Christmas"])
    palette_c_list = ", ".join(raw_colors)
    palette_count = len(raw_colors)

    # Format timeline table in compact PROGMEM chunks
    lines = []
    chunk_size = 4  # 4 frames per line
    for i in range(0, frame_count, chunk_size):
        chunk = baked_frames[i:i + chunk_size]
        items = [f"{{{s}, {x}, {b}, {m}, {tr}, {bt}}}" for (s, x, b, m, tr, bt) in chunk]
        row_str = "    " + ", ".join(items)
        if i + chunk_size < frame_count:
            row_str += ","
        # Add timestamp comment every 200 frames (approx 10s at 20fps)
        if i % 200 == 0:
            sec_at = int(i * interval_ms / 1000)
            row_str += f"  // {sec_at//60:02d}:{sec_at%60:02d} (frame {i})"
        lines.append(row_str)

    timeline_data_str = "\n".join(lines)

    sketch = f"""/*
  =============================================================================
  Standalone Music Light Show
  Generated by Music Light Sync System
  =============================================================================
  Track:           {title}
  Total Duration:  {duration_fmt} ({duration:.2f} seconds)
  Timeline Frames: {frame_count} keyframes @ {fps} FPS ({interval_ms} ms/frame)
  Color Palette:   {palette_name}
  Effect Preset:   {opts['effect_mode']}
  Hardware Mode:   {opts['hardware'].upper()} (PWM={enable_pwm}, WS2812B={enable_neopixel})

  FEATURES & CONTROLS:
  1. Auto-Start & Smooth Playback:
     - Begins playback immediately upon MCU power-up.
     - Sub-frame linear interpolation (lerp) ensures silky smooth 60+ FPS LED transitions.
  2. Physical Sync Push-Button (GPIO {opts['button_pin']}):
     - Uses ESP32 built-in BOOT button (GPIO 0) or an external button wired to GND.
     - Press the button to instantly restart the light show from 00:00 (perfect for
       syncing when pressing Play on your speaker/phone!).
  3. Interactive Serial Monitor (115200 baud):
     - Displays live ASCII progress bar with time, %, and audio frequency levels.
     - Type 'r' to restart from 00:00
     - Type ' ' (space) to pause/resume
     - Type 'l' to toggle auto-looping
     - Type '+' or '-' to adjust master brightness
     - Type 'm' to cycle addressable LED animation mode (Beat Ripple / Spectrum / Wave)
     - Type '?' to print help menu

  ARDUINO IDE INSTRUCTIONS:
  1. Open this file in the Arduino IDE.
  2. Select your board under Tools -> Board (e.g. "ESP32 Dev Module").
  3. If WS2812B Addressable LEDs are enabled, make sure "Adafruit NeoPixel" is
     installed via Tools -> Manage Libraries.
  4. Select your COM Port and click Upload (Flash)!
  =============================================================================
*/

#include <Arduino.h>

// --- FEATURE FLAGS ---
#define ENABLE_PWM_MOSFET     {enable_pwm}
#define ENABLE_NEOPIXEL_LEDS  {enable_neopixel}

// --- PIN ASSIGNMENTS ---
#if defined(ESP32)
  const int STRIP_PWM_PIN    = {opts['strip_pwm_pin']};   // Low-voltage MOSFET driver: LED Strip
  const int XMAS_PWM_PIN     = {opts['xmas_pwm_pin']};    // Low-voltage MOSFET driver: Christmas Lights
  const int NEOPIXEL_PIN     = {opts['neopixel_pin']};    // WS2812B / NeoPixel Data Pin
  const int SYNC_BUTTON_PIN  = {opts['button_pin']};      // Push-button to GND (GPIO 0 is ESP32 BOOT button)
#else
  const int STRIP_PWM_PIN    = 5;    // Arduino PWM pin
  const int XMAS_PWM_PIN     = 6;    // Arduino PWM pin
  const int NEOPIXEL_PIN     = 7;    // WS2812B Data pin
  const int SYNC_BUTTON_PIN  = 2;    // Button pin
#endif

// --- HARDWARE CONFIGURATION ---
const int NEOPIXEL_COUNT     = {opts['neopixel_count']};  // Total addressable LEDs
const int PWM_FREQ           = 5000; // 5 kHz PWM frequency (flicker-free)
const int PWM_RESOLUTION     = 8;    // 8-bit resolution (0-255)
const int PWM_CHAN_STRIP     = 0;    // ESP32 LEDC channel 0
const int PWM_CHAN_XMAS      = 1;    // ESP32 LEDC channel 1

// --- SHOW METADATA & TIMELINE ---
const uint16_t FRAME_COUNT        = {frame_count};
const uint16_t FRAME_INTERVAL_MS  = {interval_ms};
const unsigned long SHOW_DURATION_MS = {duration_ms}UL;

struct LightFrame {{
  uint8_t strip;    // Strip PWM duty (0-255)
  uint8_t xmas;     // Xmas lights PWM duty (0-255)
  uint8_t bass;     // Bass band energy (0-255)
  uint8_t mid;      // Mid band energy (0-255)
  uint8_t treble;   // Treble band energy (0-255)
  uint8_t beat;     // Beat intensity (0 or 1-255)
}};

// Timeline baked into Flash ROM (PROGMEM)
static const LightFrame SHOW_TIMELINE[FRAME_COUNT] PROGMEM = {{
{timeline_data_str}
}};

// --- COLOR PALETTE DEFINITION ---
const uint32_t COLOR_PALETTE[] = {{ {palette_c_list} }};
const uint8_t PALETTE_COUNT = {palette_count};

#if ENABLE_NEOPIXEL_LEDS
#include <Adafruit_NeoPixel.h>
Adafruit_NeoPixel pixelStrip(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);
#endif

// --- RUNTIME STATE ---
bool autoLoop = {'true' if opts['auto_loop'] else 'false'};
bool isPaused = false;
unsigned long showStartMs = 0;
unsigned long pauseStartMs = 0;
uint8_t masterBrightness = 255;  // 0 - 255
uint8_t neoMode = 0;             // 0: Beat Ripple Wave, 1: Spectrum Meter, 2: Palette Wave
int lastButtonReading = HIGH;
unsigned long lastButtonDebounceMs = 0;
unsigned long lastStatusPrintMs = 0;

// Helper: read a frame safely across ESP32 and AVR architectures
LightFrame readFrame(size_t index) {{
  LightFrame f;
#if defined(ARDUINO_ARCH_AVR)
  memcpy_P(&f, &SHOW_TIMELINE[index], sizeof(LightFrame));
#else
  f = SHOW_TIMELINE[index];
#endif
  return f;
}}

// Helper: linearly interpolate between keyframes for ultra-smooth 60+ FPS playback
void getInterpolatedFrame(unsigned long elapsedMs, LightFrame &out) {{
  if (FRAME_COUNT == 0) return;

  if (elapsedMs >= SHOW_DURATION_MS) {{
    if (autoLoop) {{
      elapsedMs = elapsedMs % SHOW_DURATION_MS;
    }} else {{
      elapsedMs = SHOW_DURATION_MS - 1;
    }}
  }}

  uint32_t frameIdx = elapsedMs / FRAME_INTERVAL_MS;
  if (frameIdx >= FRAME_COUNT - 1) {{
    out = readFrame(FRAME_COUNT - 1);
    return;
  }}

  uint32_t remainderMs = elapsedMs % FRAME_INTERVAL_MS;
  float alpha = (float)remainderMs / (float)FRAME_INTERVAL_MS;

  LightFrame f0 = readFrame(frameIdx);
  LightFrame f1 = readFrame(frameIdx + 1);

  out.strip  = (uint8_t)(f0.strip  + (f1.strip  - (int)f0.strip)  * alpha);
  out.xmas   = (uint8_t)(f0.xmas   + (f1.xmas   - (int)f0.xmas)   * alpha);
  out.bass   = (uint8_t)(f0.bass   + (f1.bass   - (int)f0.bass)   * alpha);
  out.mid    = (uint8_t)(f0.mid    + (f1.mid    - (int)f0.mid)    * alpha);
  out.treble = (uint8_t)(f0.treble + (f1.treble - (int)f0.treble) * alpha);
  out.beat   = (f1.beat > f0.beat) ? f1.beat : (uint8_t)(f0.beat * (1.0f - alpha));
}}

// Helper: blend two 24-bit RGB colors with factor (0.0 to 1.0)
uint32_t blendRGB(uint32_t c1, uint32_t c2, float t) {{
  t = constrain(t, 0.0f, 1.0f);
  uint8_t r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
  uint8_t r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
  uint8_t r = (uint8_t)(r1 + (r2 - r1) * t);
  uint8_t g = (uint8_t)(g1 + (g2 - g1) * t);
  uint8_t b = (uint8_t)(b1 + (b2 - b1) * t);
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}}

uint32_t scaleRGB(uint32_t color, float factor) {{
  factor = constrain(factor, 0.0f, 1.0f);
  uint8_t r = (uint8_t)(((color >> 16) & 0xFF) * factor);
  uint8_t g = (uint8_t)(((color >> 8) & 0xFF) * factor);
  uint8_t b = (uint8_t)((color & 0xFF) * factor);
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}}

void restartShow() {{
  showStartMs = millis();
  isPaused = false;
}}

void printHelp() {{
  Serial.println(F("\\n--- Standalone Music Light Show Controls ---"));
  Serial.println(F(" 'r' : Restart show from 00:00"));
  Serial.println(F(" ' ' : Pause / Resume playback"));
  Serial.println(F(" 'l' : Toggle auto-looping on/off"));
  Serial.println(F(" '+' : Increase master brightness"));
  Serial.println(F(" '-' : Decrease master brightness"));
  Serial.println(F(" 'm' : Cycle WS2812B NeoPixel pattern"));
  Serial.println(F(" '?' : Show this help message\\n"));
}}

void setup() {{
  Serial.begin(115200);
  delay(200);
  Serial.println(F("\\n=================================================="));
  Serial.println(F(" Standalone Music Light Show Starting..."));
  Serial.printf (" Track: %s\\n", "{title}");
  Serial.printf (" Duration: %s (%lu ms, %u frames)\\n", "{duration_fmt}", SHOW_DURATION_MS, FRAME_COUNT);
  Serial.println(F("=================================================="));

#if ENABLE_PWM_MOSFET
#if defined(ESP32)
  ledcSetup(PWM_CHAN_STRIP, PWM_FREQ, PWM_RESOLUTION);
  ledcSetup(PWM_CHAN_XMAS, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(STRIP_PWM_PIN, PWM_CHAN_STRIP);
  ledcAttachPin(XMAS_PWM_PIN, PWM_CHAN_XMAS);
#else
  pinMode(STRIP_PWM_PIN, OUTPUT);
  pinMode(XMAS_PWM_PIN, OUTPUT);
#endif
#endif

#if ENABLE_NEOPIXEL_LEDS
  pixelStrip.begin();
  pixelStrip.setBrightness(masterBrightness);
  pixelStrip.show();
#endif

  if (SYNC_BUTTON_PIN >= 0) {{
    pinMode(SYNC_BUTTON_PIN, INPUT_PULLUP);
  }}

  printHelp();
  restartShow();
}}

void loop() {{
  // --- Check Physical Sync Push-Button ---
  if (SYNC_BUTTON_PIN >= 0) {{
    int reading = digitalRead(SYNC_BUTTON_PIN);
    if (reading == LOW && lastButtonReading == HIGH && (millis() - lastButtonDebounceMs > 250)) {{
      lastButtonDebounceMs = millis();
      restartShow();
      Serial.println(F("\\n>>> [BUTTON] Hardware sync restart triggered! <<<"));
    }}
    lastButtonReading = reading;
  }}

  // --- Check Serial Commands ---
  if (Serial.available()) {{
    char cmd = Serial.read();
    if (cmd == 'r' || cmd == 'R') {{
      restartShow();
      Serial.println(F("\\n>>> [RESTART] Show restarted at 00:00 <<<"));
    }} else if (cmd == ' ') {{
      isPaused = !isPaused;
      if (isPaused) {{
        pauseStartMs = millis();
        Serial.println(F("\\n>>> [PAUSED] Press SPACE to resume <<<"));
      }} else {{
        showStartMs += (millis() - pauseStartMs);
        Serial.println(F("\\n>>> [RESUMED] <<<"));
      }}
    }} else if (cmd == 'l' || cmd == 'L') {{
      autoLoop = !autoLoop;
      Serial.printf("\\n>>> [LOOP] Auto-loop is now %s <<<\\n", autoLoop ? "ENABLED" : "DISABLED");
    }} else if (cmd == '+' || cmd == '=') {{
      if (masterBrightness <= 235) masterBrightness += 20; else masterBrightness = 255;
#if ENABLE_NEOPIXEL_LEDS
      pixelStrip.setBrightness(masterBrightness);
#endif
      Serial.printf("\\n>>> Brightness: %u%% <<<\\n", (masterBrightness * 100) / 255);
    }} else if (cmd == '-' || cmd == '_') {{
      if (masterBrightness >= 35) masterBrightness -= 20; else masterBrightness = 15;
#if ENABLE_NEOPIXEL_LEDS
      pixelStrip.setBrightness(masterBrightness);
#endif
      Serial.printf("\\n>>> Brightness: %u%% <<<\\n", (masterBrightness * 100) / 255);
    }} else if (cmd == 'm' || cmd == 'M') {{
      neoMode = (neoMode + 1) % 3;
      const char* modeNames[] = {{"Center Beat Ripple", "Spectrum Meter", "Palette Wave"}};
      Serial.printf("\\n>>> NeoPixel Mode: %s <<<\\n", modeNames[neoMode]);
    }} else if (cmd == '?') {{
      printHelp();
    }}
  }}

  // --- Calculate Timeline Position ---
  unsigned long now = millis();
  unsigned long currentElapsedMs = 0;

  if (isPaused) {{
    currentElapsedMs = pauseStartMs - showStartMs;
  }} else {{
    currentElapsedMs = now - showStartMs;
  }}

  // If show ended and not looping, turn off lights
  if (currentElapsedMs >= SHOW_DURATION_MS && !autoLoop) {{
#if ENABLE_PWM_MOSFET
#if defined(ESP32)
    ledcWrite(PWM_CHAN_STRIP, 0);
    ledcWrite(PWM_CHAN_XMAS, 0);
#else
    analogWrite(STRIP_PWM_PIN, 0);
    analogWrite(XMAS_PWM_PIN, 0);
#endif
#endif
#if ENABLE_NEOPIXEL_LEDS
    pixelStrip.clear();
    pixelStrip.show();
#endif
    delay(20);
    return;
  }}

  // Fetch interpolated frame for current millisecond
  LightFrame frame;
  getInterpolatedFrame(currentElapsedMs, frame);

  // Apply master brightness scaling
  float masterScale = (float)masterBrightness / 255.0f;
  uint8_t stripDuty = (uint8_t)(frame.strip * masterScale);
  uint8_t xmasDuty  = (uint8_t)(frame.xmas  * masterScale);

  // --- 1. Drive Low-Voltage PWM Channels ---
#if ENABLE_PWM_MOSFET
#if defined(ESP32)
  ledcWrite(PWM_CHAN_STRIP, stripDuty);
  ledcWrite(PWM_CHAN_XMAS, xmasDuty);
#else
  analogWrite(STRIP_PWM_PIN, stripDuty);
  analogWrite(XMAS_PWM_PIN, xmasDuty);
#endif
#endif

  // --- 2. Drive Addressable WS2812B LEDs ---
#if ENABLE_NEOPIXEL_LEDS
  float tSec = (float)currentElapsedMs / 1000.0f;
  float bassNorm = (float)frame.bass / 255.0f;
  float midNorm  = (float)frame.mid / 255.0f;
  float trbNorm  = (float)frame.treble / 255.0f;
  float beatNorm = (float)frame.beat / 255.0f;

  int halfCount = NEOPIXEL_COUNT / 2;

  for (int i = 0; i < NEOPIXEL_COUNT; i++) {{
    uint32_t pixelColor = 0;

    if (neoMode == 0) {{
      // Mode 0: Center Beat Ripple + Bass Glow
      int distFromCenter = abs(i - halfCount);
      float ripplePhase = fmod(tSec * 4.0f - (float)distFromCenter * 0.25f, 1.0f);
      if (ripplePhase < 0.0f) ripplePhase += 1.0f;

      uint32_t c0 = COLOR_PALETTE[i % PALETTE_COUNT];
      uint32_t c1 = COLOR_PALETTE[(i + 1) % PALETTE_COUNT];
      uint32_t base = blendRGB(c0, c1, ripplePhase);

      float intensity = 0.15f + 0.60f * bassNorm + 0.50f * beatNorm;
      if (distFromCenter < (int)(beatNorm * halfCount + 1)) {{
        base = blendRGB(base, 0xFFFFFF, 0.45f * beatNorm);
      }}
      pixelColor = scaleRGB(base, intensity);

    }} else if (neoMode == 1) {{
      // Mode 1: Multi-Band Spectrum Meter across the strip
      float pos = (float)i / (float)NEOPIXEL_COUNT;
      float level = (pos < 0.33f) ? bassNorm : ((pos < 0.66f) ? midNorm : trbNorm);
      uint32_t c = COLOR_PALETTE[(int)(pos * PALETTE_COUNT) % PALETTE_COUNT];
      pixelColor = scaleRGB(c, 0.10f + 0.90f * level);

    }} else {{
      // Mode 2: Flowing Palette Wave modulated by music
      float wave = 0.5f + 0.5f * sinf(tSec * 2.5f + (float)i * 0.35f);
      int pIdx = (int)(tSec * 1.5f + (float)i * 0.2f) % PALETTE_COUNT;
      uint32_t c = COLOR_PALETTE[pIdx];
      pixelColor = scaleRGB(c, 0.20f + 0.55f * wave + 0.35f * bassNorm);
    }}

    pixelStrip.setPixelColor(i, pixelColor);
  }}
  pixelStrip.show();
#endif

  // --- 3. Live Serial Progress Bar (updates 4 times per second) ---
  if (millis() - lastStatusPrintMs >= 250 && !isPaused) {{
    lastStatusPrintMs = millis();
    unsigned long activeMs = currentElapsedMs % SHOW_DURATION_MS;
    unsigned long curSec = activeMs / 1000;
    unsigned long totSec = SHOW_DURATION_MS / 1000;
    int percent = (int)((activeMs * 100) / SHOW_DURATION_MS);

    char bar[17];
    int filled = (percent * 16) / 100;
    for (int b = 0; b < 16; b++) {{
      bar[b] = (b < filled) ? '=' : ((b == filled) ? '>' : ' ');
    }}
    bar[16] = '\\0';

    Serial.printf("\\r[%02lu:%02lu / %02lu:%02lu] [%s] %3d%% | Bass:%3u Mid:%3u Trb:%3u Beat:%s ",
                  curSec / 60, curSec % 60, totSec / 60, totSec % 60,
                  bar, percent,
                  frame.bass, frame.mid, frame.treble,
                  (frame.beat > 100) ? "BEAT!" : "     ");
  }}

  // Small delay for MCU loop yield
  delay(5);
}}
"""
    return sketch


def export_ino_file(audio_path, output_path=None, precomputed_analysis=None, **options):
    """
    High-level export function:
    1. Analyzes audio file (or uses precomputed analysis).
    2. Bakes choreography frames.
    3. Generates Arduino .ino source.
    4. Writes file according to Arduino IDE folder conventions.
    Returns dict with summary and saved file paths.
    """
    if precomputed_analysis is not None:
        analysis = precomputed_analysis
    else:
        fps = options.get("fps", 20)
        analysis = analyze_audio_file(audio_path, target_fps=fps)

    effect_mode = options.get("effect_mode", "Music Reactive")
    brightness = options.get("brightness", 1.0)
    sensitivity = options.get("sensitivity", 1.5)

    baked = bake_choreography(analysis, effect_mode=effect_mode, brightness=brightness, sensitivity=sensitivity)
    sketch_code = generate_ino_sketch(analysis, baked, options)

    # Determine output path
    safe_title = re.sub(r'[^a-zA-Z0-9_]', '_', analysis["title"])
    if not safe_title or safe_title[0].isdigit():
        safe_title = "Show_" + safe_title

    if output_path is None:
        sketch_name = f"{safe_title}_LightShow"
        base_dir = os.path.dirname(os.path.abspath(audio_path))
        sketch_dir = os.path.join(base_dir, sketch_name)
        os.makedirs(sketch_dir, exist_ok=True)
        final_ino_path = os.path.join(sketch_dir, f"{sketch_name}.ino")
    else:
        # Check if output_path is directory or file
        if output_path.endswith(".ino"):
            # Check if it has a matching folder name
            file_dir, file_name = os.path.split(output_path)
            name_without_ext = os.path.splitext(file_name)[0]
            if os.path.basename(file_dir) != name_without_ext:
                # Create standard Arduino IDE subfolder
                sketch_dir = os.path.join(file_dir, name_without_ext)
                os.makedirs(sketch_dir, exist_ok=True)
                final_ino_path = os.path.join(sketch_dir, file_name)
            else:
                os.makedirs(file_dir, exist_ok=True)
                final_ino_path = output_path
        else:
            # It's a folder
            sketch_name = f"{safe_title}_LightShow"
            sketch_dir = os.path.join(output_path, sketch_name)
            os.makedirs(sketch_dir, exist_ok=True)
            final_ino_path = os.path.join(sketch_dir, f"{sketch_name}.ino")

    with open(final_ino_path, "w", encoding="utf-8") as f:
        f.write(sketch_code)

    return {
        "title": analysis["title"],
        "duration": analysis["duration"],
        "frames": len(baked),
        "fps": analysis["fps"],
        "ino_path": final_ino_path,
        "sketch_dir": os.path.dirname(final_ino_path),
        "code_bytes": len(sketch_code.encode("utf-8"))
    }


PALETTE_NAME_TO_ID = {
    "Christmas": 0, "Rainbow": 1, "Warm White": 2, "Blue Ice": 3,
    "Red": 4, "Green": 5, "Purple Neon": 6, "Ocean": 7,
    "Sunset": 8, "Fire": 9, "Cotton Candy": 10, "Emerald": 11
}


def export_sd_show_file(audio_path, output_path=None, precomputed_analysis=None, **options):
    """
    Exports a binary SHOW.DAT file for the ESP32 SD-Card Player.
    Once the ESP32 is flashed with esp32_sd_music_player.ino,
    you never need to re-flash it when you change songs!
    """
    import struct
    if precomputed_analysis is not None:
        analysis = precomputed_analysis
    else:
        fps = options.get("fps", 20)
        analysis = analyze_audio_file(audio_path, target_fps=fps)

    effect_mode = options.get("effect_mode", "Music Reactive")
    brightness = options.get("brightness", 1.0)
    sensitivity = options.get("sensitivity", 1.5)

    baked = bake_choreography(analysis, effect_mode=effect_mode, brightness=brightness, sensitivity=sensitivity)

    palette_name = options.get("palette", "Christmas")
    palette_id = PALETTE_NAME_TO_ID.get(palette_name, 0)

    hardware = options.get("hardware", "both")
    enable_pwm = 1 if hardware in ("both", "pwm") else 0
    enable_neo = 1 if hardware in ("both", "neopixel") else 0
    auto_loop = 1 if options.get("auto_loop", True) else 0

    flags = (enable_pwm & 0x01) | ((enable_neo & 0x01) << 1) | ((auto_loop & 0x01) << 2)
    duration_ms = int(analysis["duration"] * 1000)
    frame_count = len(baked)

    header = struct.pack('<4sBBBBI I 4s', b'MLS1', 1, int(round(analysis["fps"])), palette_id, flags, frame_count, duration_ms, b'\x00'*4)

    frame_data = bytearray()
    for f in baked:
        frame_data.extend(f)

    if output_path is None:
        base_dir = os.path.dirname(os.path.abspath(audio_path))
        output_path = os.path.join(base_dir, "SHOW.DAT")
    elif os.path.isdir(output_path):
        output_path = os.path.join(output_path, "SHOW.DAT")

    with open(output_path, "wb") as fp:
        fp.write(header)
        fp.write(frame_data)

    return {
        "title": analysis["title"],
        "duration": analysis["duration"],
        "frames": frame_count,
        "fps": analysis["fps"],
        "dat_path": output_path,
        "dat_dir": os.path.dirname(output_path),
        "total_bytes": len(header) + len(frame_data)
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate Arduino .ino sketch or SD-Card SHOW.DAT directly from music."
    )
    parser.add_argument("audio", help="Path to input audio file (.mp3, .wav, .flac, .ogg)")
    parser.add_argument("-o", "--output", help="Output .ino file, .DAT file, or directory", default=None)
    parser.add_argument("--sd-card", action="store_true", help="Generate binary SHOW.DAT file for ESP32 SD player (no re-flashing needed)")
    parser.add_argument("--hardware", choices=["both", "pwm", "neopixel"], default="both",
                        help="Lighting hardware to control (default: both)")
    parser.add_argument("--fps", type=int, default=20, help="Timeline frame rate (default: 20 FPS)")
    parser.add_argument("--effect", default="Music Reactive", help="Effect preset mode (default: 'Music Reactive')")
    parser.add_argument("--palette", default="Christmas", help="Color palette (default: 'Christmas')")
    parser.add_argument("--custom-color", default="#58a6ff", help="Custom color hex (default: '#58a6ff')")
    parser.add_argument("--strip-pin", type=int, default=25, help="LED Strip PWM pin (default: 25)")
    parser.add_argument("--xmas-pin", type=int, default=26, help="Christmas lights PWM pin (default: 26)")
    parser.add_argument("--pixel-pin", type=int, default=27, help="WS2812B NeoPixel data pin (default: 27)")
    parser.add_argument("--pixel-count", type=int, default=30, help="Number of WS2812B LEDs (default: 30)")
    parser.add_argument("--button-pin", type=int, default=0, help="Sync restart button pin (default: 0 for ESP32 BOOT)")
    parser.add_argument("--no-loop", action="store_true", help="Disable auto-looping when show completes")

    args = parser.parse_args()

    options = {
        "hardware": args.hardware,
        "fps": args.fps,
        "effect_mode": args.effect,
        "palette": args.palette,
        "custom_color": args.custom_color,
        "strip_pwm_pin": args.strip_pin,
        "xmas_pwm_pin": args.xmas_pin,
        "neopixel_pin": args.pixel_pin,
        "neopixel_count": args.pixel_count,
        "button_pin": args.button_pin,
        "auto_loop": not args.no_loop,
    }

    print(f"Analyzing audio file: {args.audio} ...")
    if args.sd_card or (args.output and args.output.upper().endswith(".DAT")):
        res = export_sd_show_file(args.audio, args.output, **options)
        print("\nSUCCESS! SD-Card SHOW.DAT generated.")
        print(f"  Title:       {res['title']}")
        print(f"  Duration:    {res['duration']:.2f} s")
        print(f"  Keyframes:   {res['frames']} @ {res['fps']:.1f} FPS")
        print(f"  Show file:   {res['dat_path']} ({res['total_bytes']} bytes)")
        print("\nCopy 'SHOW.DAT' to your MicroSD card and insert into the ESP32 (no re-flashing needed)!")
    else:
        res = export_ino_file(args.audio, args.output, **options)
        print("\nSUCCESS! Arduino .ino sketch generated.")
        print(f"  Title:       {res['title']}")
        print(f"  Duration:    {res['duration']:.2f} s")
        print(f"  Keyframes:   {res['frames']} @ {res['fps']:.1f} FPS")
        print(f"  Sketch file: {res['ino_path']}")
        print(f"  Sketch dir:  {res['sketch_dir']}")
        print("\nYou can now open this folder in Arduino IDE and flash directly to your MCU!")


if __name__ == "__main__":
    main()

