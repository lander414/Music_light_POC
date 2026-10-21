/*
  =============================================================================
  Universal ESP32 SD-Card Music Light Show Player
  =============================================================================
  Flash this sketch ONCE to your ESP32.
  After flashing, you never need to re-flash the ESP32 when you change music!

  HOW IT WORKS:
  1. Flash this .ino sketch to your ESP32 using the Arduino IDE.
  2. Upload your music in the Python app ("music_light_sync.py") and click
     "GENERATE .DAT FOR SD CARD".
  3. Copy the generated "SHOW.DAT" file to your MicroSD card.
  4. Insert the MicroSD card into the ESP32's SD card reader.
  5. The ESP32 loads the show and starts playing the lights automatically!
  
  (Fallback: If no SD card is found, it will look for /SHOW.DAT in internal LittleFS)

  HARDWARE PIN ASSIGNMENTS (ESP32):
  - MicroSD Card SPI:
      CS:   GPIO 5
      MOSI: GPIO 23
      MISO: GPIO 19
      SCK:  GPIO 18
  - Lighting Hardware:
      LED Strip MOSFET PWM:    GPIO 25
      Christmas Lights PWM:    GPIO 26
      WS2812B NeoPixel Data:   GPIO 27
  - Controls:
      Sync / Restart Button:   GPIO 0  (ESP32 built-in "BOOT" button)
  =============================================================================
*/

#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include <FS.h>
#include <LittleFS.h>
#include <Adafruit_NeoPixel.h>

// --- PIN DEFINITIONS ---
const int SD_CS_PIN        = 5;   // MicroSD SPI Chip Select
const int STRIP_PWM_PIN    = 25;  // Low-voltage MOSFET driver: LED Strip
const int XMAS_PWM_PIN     = 26;  // Low-voltage MOSFET driver: Christmas Lights
const int NEOPIXEL_PIN     = 27;  // WS2812B Addressable LED data pin
const int SYNC_BUTTON_PIN  = 0;   // Built-in ESP32 BOOT button (GPIO 0 to GND)

// --- PWM & NEOPIXEL CONFIGURATION ---
const int NEOPIXEL_COUNT   = 30;  // Adjust to your WS2812B strip length
const int PWM_FREQ         = 5000;
const int PWM_RESOLUTION   = 8;
const int PWM_CHAN_STRIP   = 0;
const int PWM_CHAN_XMAS    = 1;

Adafruit_NeoPixel pixelStrip(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

// --- BINARY SHOW FILE FORMAT (.DAT) ---
#pragma pack(push, 1)
struct ShowHeader {
  char magic[4];        // 'MLS1'
  uint8_t version;      // 1
  uint8_t fps;          // e.g. 20
  uint8_t paletteId;    // Color palette index (0 - 11)
  uint8_t flags;        // Bit 0: PWM, Bit 1: NeoPixel, Bit 2: Loop
  uint32_t frameCount;  // Total keyframes
  uint32_t durationMs;  // Total duration in milliseconds
  uint8_t reserved[4];  // Reserved padding
};

struct LightFrame {
  uint8_t strip;    // Strip PWM duty (0-255)
  uint8_t xmas;     // Xmas lights PWM duty (0-255)
  uint8_t bass;     // Bass energy (0-255)
  uint8_t mid;      // Mid energy (0-255)
  uint8_t treble;   // Treble energy (0-255)
  uint8_t beat;     // Beat intensity (0 or 255)
};
#pragma pack(pop)

// --- 12 STANDARD COLOR PALETTES ---
const uint32_t PALETTES[][6] = {
  // 0: Christmas
  {0xFF334D, 0x20E070, 0xFFF0C2, 0x2675FF, 0xFFBD32, 0},
  // 1: Rainbow
  {0xFF3B5C, 0xFFB52E, 0xF5F542, 0x30E878, 0x368BFF, 0xBC5CFF},
  // 2: Warm White
  {0xFFF0CF, 0xFFE1A0, 0xFFF8E8, 0, 0, 0},
  // 3: Blue Ice
  {0x8BE9FF, 0x348CFF, 0xE6FAFF, 0x9EBCFF, 0, 0},
  // 4: Red
  {0xFF263F, 0xFF6374, 0xB50020, 0, 0, 0},
  // 5: Green
  {0x20FF70, 0x00BD56, 0xB0FFD0, 0, 0, 0},
  // 6: Purple Neon
  {0xFF45F6, 0xA855F7, 0x5B5BFF, 0x29106B, 0, 0},
  // 7: Ocean
  {0xB7FFF7, 0x00D9FF, 0x1677FF, 0x082B65, 0, 0},
  // 8: Sunset
  {0xFFF06A, 0xFF9F43, 0xFF477E, 0x8B3DFF, 0, 0},
  // 9: Fire
  {0x8F001B, 0xFF2638, 0xFF6B21, 0xFFB52E, 0xFFF47A, 0},
  // 10: Cotton Candy
  {0xFF9BD2, 0xD7A6FF, 0x8CECFF, 0xFFF0FA, 0, 0},
  // 11: Emerald
  {0x006B55, 0x00B878, 0x65F26D, 0xD8FF66, 0x43F5C2, 0}
};

const uint8_t PALETTE_LENS[] = {5, 6, 3, 4, 3, 3, 4, 4, 4, 5, 4, 5};

// --- RUNTIME STATE ---
ShowHeader currentHeader;
LightFrame* showFrames = nullptr;
bool showLoaded = false;
bool isStreamingMode = false;
File activeShowFile;
const size_t STREAM_CACHE_SIZE = 128;
LightFrame streamCache[STREAM_CACHE_SIZE];
uint32_t streamCacheStart = 0xFFFFFFFF;

bool sdActive = false;
bool littlefsActive = false;

bool autoLoop = true;
bool isPaused = false;
unsigned long showStartMs = 0;
unsigned long pauseStartMs = 0;
uint8_t masterBrightness = 255;
uint8_t neoMode = 0; // 0: Center Beat Ripple, 1: Spectrum Meter, 2: Palette Wave

int lastButtonReading = HIGH;
unsigned long lastButtonDebounceMs = 0;
unsigned long lastStatusPrintMs = 0;

// Helper: blend two 24-bit RGB colors with factor (0.0 to 1.0)
uint32_t blendRGB(uint32_t c1, uint32_t c2, float t) {
  t = constrain(t, 0.0f, 1.0f);
  uint8_t r1 = (c1 >> 16) & 0xFF, g1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
  uint8_t r2 = (c2 >> 16) & 0xFF, g2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
  uint8_t r = (uint8_t)(r1 + (r2 - r1) * t);
  uint8_t g = (uint8_t)(g1 + (g2 - g1) * t);
  uint8_t b = (uint8_t)(b1 + (b2 - b1) * t);
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

uint32_t scaleRGB(uint32_t color, float factor) {
  factor = constrain(factor, 0.0f, 1.0f);
  uint8_t r = (uint8_t)(((color >> 16) & 0xFF) * factor);
  uint8_t g = (uint8_t)(((color >> 8) & 0xFF) * factor);
  uint8_t b = (uint8_t)((color & 0xFF) * factor);
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

void restartShow() {
  showStartMs = millis();
  isPaused = false;
  streamCacheStart = 0xFFFFFFFF;
}

void printHelp() {
  Serial.println(F("\n--- Universal ESP32 SD-Card Player Controls ---"));
  Serial.println(F(" 'r' : Restart show from 00:00"));
  Serial.println(F(" ' ' : Pause / Resume show"));
  Serial.println(F(" 'l' : Toggle auto-loop"));
  Serial.println(F(" '+' : Increase master brightness"));
  Serial.println(F(" '-' : Decrease master brightness"));
  Serial.println(F(" 'm' : Cycle WS2812B pattern (Ripple / Spectrum / Wave)"));
  Serial.println(F(" '?' : Show this help message\n"));
}

bool loadShowFile(fs::FS &fs, const char* path) {
  Serial.printf("Checking for show file: %s ...\n", path);
  if (!fs.exists(path)) {
    Serial.printf("File %s not found.\n", path);
    return false;
  }

  File f = fs.open(path, "r");
  if (!f) {
    Serial.println("Failed to open show file for reading.");
    return false;
  }

  size_t readHeader = f.read((uint8_t*)&currentHeader, sizeof(ShowHeader));
  if (readHeader != sizeof(ShowHeader)) {
    Serial.println("Show file corrupted: incomplete header.");
    f.close();
    return false;
  }

  if (memcmp(currentHeader.magic, "MLS1", 4) != 0) {
    Serial.println("Invalid show file: missing MLS1 signature.");
    f.close();
    return false;
  }

  Serial.println(">>> Valid SHOW.DAT header found! <<<");
  Serial.printf("  Version:      %u\n", currentHeader.version);
  Serial.printf("  Duration:     %lu ms (%02lu:%02lu)\n",
                currentHeader.durationMs,
                (currentHeader.durationMs / 1000) / 60,
                (currentHeader.durationMs / 1000) % 60);
  Serial.printf("  Keyframes:    %lu @ %u FPS\n", currentHeader.frameCount, currentHeader.fps);
  Serial.printf("  Palette ID:   %u\n", currentHeader.paletteId);
  Serial.printf("  Flags:        0x%02X\n", currentHeader.flags);

  // Free any previous RAM frame buffer
  if (showFrames != nullptr) {
    free(showFrames);
    showFrames = nullptr;
  }
  if (activeShowFile) {
    activeShowFile.close();
  }

  size_t dataBytes = currentHeader.frameCount * sizeof(LightFrame);
  size_t maxAlloc = ESP.getMaxAllocHeap();
  Serial.printf("  Show data size: %u bytes (Max free RAM block: %u bytes)\n", dataBytes, maxAlloc);

  // If track fits in RAM, buffer entirely into RAM. Otherwise stream from SD card!
  if (dataBytes < (maxAlloc - 20000)) {
    showFrames = (LightFrame*)malloc(dataBytes);
  }

  if (showFrames != nullptr) {
    size_t readBytes = f.read((uint8_t*)showFrames, dataBytes);
    f.close();
    isStreamingMode = false;
    Serial.printf(">>> Entire show (%u bytes) loaded into RAM! <<<\n", readBytes);
  } else {
    // Large file (e.g. 35+ minute track) -> stream from SD Card using 128-frame cache block!
    isStreamingMode = true;
    activeShowFile = f; // Keep file open for playback streaming
    streamCacheStart = 0xFFFFFFFF;
    Serial.println(">>> Large show detected (30+ mins): Streaming directly from SD Card! <<<");
  }

  autoLoop = (currentHeader.flags & 0x04) != 0;
  showLoaded = true;
  restartShow();
  return true;
}

LightFrame fetchSingleFrame(uint32_t index) {
  if (index >= currentHeader.frameCount) {
    index = currentHeader.frameCount - 1;
  }

  if (!isStreamingMode && showFrames != nullptr) {
    return showFrames[index];
  }

  // Streaming mode: check 128-frame buffer
  if (index < streamCacheStart || index >= streamCacheStart + STREAM_CACHE_SIZE) {
    streamCacheStart = index;
    uint32_t count = min((uint32_t)STREAM_CACHE_SIZE, currentHeader.frameCount - index);
    if (activeShowFile) {
      activeShowFile.seek(sizeof(ShowHeader) + index * sizeof(LightFrame));
      activeShowFile.read((uint8_t*)streamCache, count * sizeof(LightFrame));
    }
  }

  return streamCache[index - streamCacheStart];
}

void getInterpolatedFrame(unsigned long elapsedMs, LightFrame &out) {
  if (!showLoaded || currentHeader.frameCount == 0) {
    out = {0, 0, 0, 0, 0, 0};
    return;
  }

  unsigned long totalDur = currentHeader.durationMs;
  if (elapsedMs >= totalDur) {
    if (autoLoop) {
      elapsedMs = elapsedMs % totalDur;
    } else {
      elapsedMs = totalDur - 1;
    }
  }

  uint32_t intervalMs = max(1, 1000 / currentHeader.fps);
  uint32_t frameIdx = elapsedMs / intervalMs;

  if (frameIdx >= currentHeader.frameCount - 1) {
    out = fetchSingleFrame(currentHeader.frameCount - 1);
    return;
  }

  uint32_t remainderMs = elapsedMs % intervalMs;
  float alpha = (float)remainderMs / (float)intervalMs;

  LightFrame f0 = fetchSingleFrame(frameIdx);
  LightFrame f1 = fetchSingleFrame(frameIdx + 1);

  out.strip  = (uint8_t)(f0.strip  + (f1.strip  - (int)f0.strip)  * alpha);
  out.xmas   = (uint8_t)(f0.xmas   + (f1.xmas   - (int)f0.xmas)   * alpha);
  out.bass   = (uint8_t)(f0.bass   + (f1.bass   - (int)f0.bass)   * alpha);
  out.mid    = (uint8_t)(f0.mid    + (f1.mid    - (int)f0.mid)    * alpha);
  out.treble = (uint8_t)(f0.treble + (f1.treble - (int)f0.treble) * alpha);
  out.beat   = (f1.beat > f0.beat) ? f1.beat : (uint8_t)(f0.beat * (1.0f - alpha));
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println(F("\n========================================================"));
  Serial.println(F("  ESP32 UNIVERSAL SD-CARD MUSIC LIGHT SHOW PLAYER"));
  Serial.println(F("========================================================"));

  // 1. PWM Setup
  ledcSetup(PWM_CHAN_STRIP, PWM_FREQ, PWM_RESOLUTION);
  ledcSetup(PWM_CHAN_XMAS, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(STRIP_PWM_PIN, PWM_CHAN_STRIP);
  ledcAttachPin(XMAS_PWM_PIN, PWM_CHAN_XMAS);
  ledcWrite(PWM_CHAN_STRIP, 0);
  ledcWrite(PWM_CHAN_XMAS, 0);

  // 2. NeoPixel Setup
  pixelStrip.begin();
  pixelStrip.setBrightness(masterBrightness);
  pixelStrip.clear();
  pixelStrip.show();

  // 3. Hardware Sync Button (GPIO 0)
  if (SYNC_BUTTON_PIN >= 0) {
    pinMode(SYNC_BUTTON_PIN, INPUT_PULLUP);
  }

  // 4. Try mounting MicroSD Card
  Serial.print("Mounting MicroSD Card (CS Pin ");
  Serial.print(SD_CS_PIN);
  Serial.print(")... ");

  if (SD.begin(SD_CS_PIN)) {
    sdActive = true;
    uint8_t cardType = SD.cardType();
    Serial.println("OK!");
    Serial.printf("SD Card Size: %llu MB\n", SD.cardSize() / (1024 * 1024));

    // Try loading /SHOW.DAT from SD card
    if (loadShowFile(SD, "/SHOW.DAT")) {
      Serial.println("Playing from MicroSD card: /SHOW.DAT");
    }
  } else {
    Serial.println("FAILED (No SD card detected).");
  }

  // 5. Fallback to LittleFS internal flash if SD card had no show
  if (!showLoaded) {
    Serial.print("Checking internal LittleFS Flash memory... ");
    if (LittleFS.begin(true)) {
      littlefsActive = true;
      Serial.println("OK!");
      if (loadShowFile(LittleFS, "/SHOW.DAT")) {
        Serial.println("Playing from internal Flash: /SHOW.DAT");
      }
    } else {
      Serial.println("LittleFS Mount Failed.");
    }
  }

  if (!showLoaded) {
    Serial.println(F("\n[STATUS: WAITING FOR SHOW FILE]"));
    Serial.println(F("-> Please copy 'SHOW.DAT' generated by the Python app onto"));
    Serial.println(F("   your MicroSD card and insert it into the ESP32."));
  }

  printHelp();
}

void loop() {
  // --- Check Physical Sync Push-Button (GPIO 0) ---
  if (SYNC_BUTTON_PIN >= 0) {
    int reading = digitalRead(SYNC_BUTTON_PIN);
    if (reading == LOW && lastButtonReading == HIGH && (millis() - lastButtonDebounceMs > 250)) {
      lastButtonDebounceMs = millis();
      restartShow();
      Serial.println(F("\n>>> [SYNC BUTTON] Show restarted at 00:00! <<<"));
    }
    lastButtonReading = reading;
  }

  // --- Check Serial Commands ---
  if (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'r' || cmd == 'R') {
      restartShow();
      Serial.println(F("\n>>> [RESTART] Show restarted at 00:00 <<<"));
    } else if (cmd == ' ') {
      isPaused = !isPaused;
      if (isPaused) {
        pauseStartMs = millis();
        Serial.println(F("\n>>> [PAUSED] Press SPACE to resume <<<"));
      } else {
        showStartMs += (millis() - pauseStartMs);
        Serial.println(F("\n>>> [RESUMED] <<<"));
      }
    } else if (cmd == 'l' || cmd == 'L') {
      autoLoop = !autoLoop;
      Serial.printf("\n>>> [LOOP] Auto-loop is now %s <<<\n", autoLoop ? "ENABLED" : "DISABLED");
    } else if (cmd == '+' || cmd == '=') {
      if (masterBrightness <= 235) masterBrightness += 20; else masterBrightness = 255;
      pixelStrip.setBrightness(masterBrightness);
      Serial.printf("\n>>> Brightness: %u%% <<<\n", (masterBrightness * 100) / 255);
    } else if (cmd == '-' || cmd == '_') {
      if (masterBrightness >= 35) masterBrightness -= 20; else masterBrightness = 15;
      pixelStrip.setBrightness(masterBrightness);
      Serial.printf("\n>>> Brightness: %u%% <<<\n", (masterBrightness * 100) / 255);
    } else if (cmd == 'm' || cmd == 'M') {
      neoMode = (neoMode + 1) % 3;
      const char* modeNames[] = {"Center Beat Ripple", "Spectrum Meter", "Palette Wave"};
      Serial.printf("\n>>> WS2812B Pattern: %s <<<\n", modeNames[neoMode]);
    } else if (cmd == '?') {
      printHelp();
    }
  }

  // Idle waiting if no show is loaded
  if (!showLoaded) {
    // Gentle breathing yellow pulse to indicate standby
    float t = millis() / 1000.0f;
    float breathe = 0.5f + 0.5f * sinf(t * 2.0f);
    int duty = (int)(breathe * 40.0f);
    ledcWrite(PWM_CHAN_STRIP, duty);
    ledcWrite(PWM_CHAN_XMAS, duty);

    pixelStrip.clear();
    for (int i = 0; i < NEOPIXEL_COUNT; i++) {
      pixelStrip.setPixelColor(i, scaleRGB(0xFFB52E, breathe * 0.25f));
    }
    pixelStrip.show();
    delay(20);
    return;
  }

  // --- Calculate Show Position ---
  unsigned long now = millis();
  unsigned long currentElapsedMs = 0;

  if (isPaused) {
    currentElapsedMs = pauseStartMs - showStartMs;
  } else {
    currentElapsedMs = now - showStartMs;
  }

  // If show ended and not looping, turn off lights
  if (currentElapsedMs >= currentHeader.durationMs && !autoLoop) {
    ledcWrite(PWM_CHAN_STRIP, 0);
    ledcWrite(PWM_CHAN_XMAS, 0);
    pixelStrip.clear();
    pixelStrip.show();
    delay(20);
    return;
  }

  // Get smooth interpolated frame for this exact millisecond
  LightFrame frame;
  getInterpolatedFrame(currentElapsedMs, frame);

  // Apply master brightness
  float masterScale = (float)masterBrightness / 255.0f;
  uint8_t stripDuty = (uint8_t)(frame.strip * masterScale);
  uint8_t xmasDuty  = (uint8_t)(frame.xmas  * masterScale);

  // 1. Output to PWM MOSFET Channels
  ledcWrite(PWM_CHAN_STRIP, stripDuty);
  ledcWrite(PWM_CHAN_XMAS, xmasDuty);

  // 2. Output to Addressable WS2812B NeoPixels
  uint8_t pId = currentHeader.paletteId % 12;
  const uint32_t* palette = PALETTES[pId];
  uint8_t paletteSize = PALETTE_LENS[pId];

  float tSec = (float)currentElapsedMs / 1000.0f;
  float bassNorm = (float)frame.bass / 255.0f;
  float midNorm  = (float)frame.mid / 255.0f;
  float trbNorm  = (float)frame.treble / 255.0f;
  float beatNorm = (float)frame.beat / 255.0f;

  int halfCount = NEOPIXEL_COUNT / 2;

  for (int i = 0; i < NEOPIXEL_COUNT; i++) {
    uint32_t pixelColor = 0;

    if (neoMode == 0) {
      // Center-out Beat Ripple
      int dist = abs(i - halfCount);
      float phase = fmod(tSec * 4.0f - (float)dist * 0.25f, 1.0f);
      if (phase < 0.0f) phase += 1.0f;

      uint32_t c0 = palette[i % paletteSize];
      uint32_t c1 = palette[(i + 1) % paletteSize];
      uint32_t base = blendRGB(c0, c1, phase);

      float intensity = 0.15f + 0.60f * bassNorm + 0.50f * beatNorm;
      if (dist < (int)(beatNorm * halfCount + 1)) {
        base = blendRGB(base, 0xFFFFFF, 0.45f * beatNorm);
      }
      pixelColor = scaleRGB(base, intensity);

    } else if (neoMode == 1) {
      // Spectrum VU-meter
      float pos = (float)i / (float)NEOPIXEL_COUNT;
      float level = (pos < 0.33f) ? bassNorm : ((pos < 0.66f) ? midNorm : trbNorm);
      uint32_t c = palette[(int)(pos * paletteSize) % paletteSize];
      pixelColor = scaleRGB(c, 0.10f + 0.90f * level);

    } else {
      // Flowing Palette Wave
      float wave = 0.5f + 0.5f * sinf(tSec * 2.5f + (float)i * 0.35f);
      int pIdx = (int)(tSec * 1.5f + (float)i * 0.2f) % paletteSize;
      uint32_t c = palette[pIdx];
      pixelColor = scaleRGB(c, 0.20f + 0.55f * wave + 0.35f * bassNorm);
    }

    pixelStrip.setPixelColor(i, pixelColor);
  }
  pixelStrip.show();

  // 3. Live Serial Progress Bar (updates every 250ms)
  if (millis() - lastStatusPrintMs >= 250 && !isPaused) {
    lastStatusPrintMs = millis();
    unsigned long activeMs = currentElapsedMs % currentHeader.durationMs;
    unsigned long curSec = activeMs / 1000;
    unsigned long totSec = currentHeader.durationMs / 1000;
    int percent = (int)((activeMs * 100) / currentHeader.durationMs);

    char bar[17];
    int filled = (percent * 16) / 100;
    for (int b = 0; b < 16; b++) {
      bar[b] = (b < filled) ? '=' : ((b == filled) ? '>' : ' ');
    }
    bar[16] = '\0';

    Serial.printf("\r[%02lu:%02lu / %02lu:%02lu] [%s] %3d%% | Bass:%3u Mid:%3u Trb:%3u Beat:%s ",
                  curSec / 60, curSec % 60, totSec / 60, totSec % 60,
                  bar, percent,
                  frame.bass, frame.mid, frame.treble,
                  (frame.beat > 100) ? "BEAT!" : "     ");
  }

  delay(5);
}
