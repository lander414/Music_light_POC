# Music Light Sync & Microcontroller Show Player

A complete system to synchronize music with hardware lighting (ESP32 / Arduino):

1. **Interactive Desktop App (`music_light_sync.py`)**: Load any music file, preview virtual lights, and generate MCU files with one click.
2. **Universal SD-Card Show Player (`esp32_sd_music_player.ino`)**: Flash your ESP32 **ONCE**. After that, whenever you upload new music, you just save `SHOW.DAT` to the SD card and the ESP32 plays it without ever re-flashing!
3. **Standalone Arduino Sketch Generator (`generate_ino.py`)**: Bakes the music choreography into a standalone `.ino` file if you prefer direct compiling.
4. **Live Wi-Fi Receiver (`esp32_music_light_receiver.ino`)**: For real-time streaming over UDP if desired.

---

## ⚡ Two Standalone Playback Workflows

### Workflow 1: SD Card Mode (Flash ONCE, No Re-flashing!) ⭐ *Recommended*

```
1. Flash ESP32 ONCE               2. Upload Music & Generate           3. Play from SD Card
[ esp32_sd_music_player.ino ]      [ Python App -> SHOW.DAT ]          [ Insert SD -> Lights Run! ]
(Firmware stays on ESP32)          (Takes 1 second to save)            (No Arduino IDE needed!)
```

1. **Flash Firmware ONCE**: Open [esp32_sd_music_player.ino](file:///c:/Users/sophi/OneDrive/Documents/GitHub/Music_light_POC/esp32_sd_music_player.ino) in Arduino IDE and upload it to your ESP32.
2. **Upload Music**: Open `music_light_sync.py`, click **"OPEN MUSIC"**, and select your track.
3. **Generate Show**: Click **"⚡ GENERATE .INO FOR MCU"**, select **"SD Card SHOW.DAT (No Re-flashing!)"**, and click **"GENERATE SHOW FILE"**.
4. **Copy to SD Card**: Save or copy `SHOW.DAT` directly to your MicroSD card.
5. **Insert & Play**: Insert the SD card into your ESP32's SD card module. The ESP32 immediately loads the show into RAM and starts controlling the lights!
*(Note: If no SD card is detected, the ESP32 will automatically look for `/SHOW.DAT` in its internal LittleFS flash memory!)*

---

### Workflow 2: Direct .INO Sketch Mode (Compile & Flash into MCU ROM)

```
1. Upload Music               2. Generate .INO Sketch            3. Flash to ESP32
[ .mp3 / .wav / etc ]  ──►   [ PROGMEM C++ Keyframes ]   ──►   [ Arduino IDE -> Upload ]
```

1. Open `music_light_sync.py`, load music, and click **"⚡ GENERATE .INO FOR MCU"**.
2. Select **"Standalone Arduino Sketch (.INO)"**.
3. Open the generated `<TrackName>_LightShow.ino` file in the Arduino IDE and flash it to the board.

---

## 🎛️ Microcontroller Playback Controls

Both player sketches run completely standalone:
- **Auto-Start**: Playback begins immediately upon power-up.
- **Sub-frame Linear Interpolation (`lerp`)**: Interpolates between keyframes to drive the LEDs at buttery 60+ FPS without stepping.
- **Hardware Sync Button (GPIO 0)**: Press the ESP32 built-in **BOOT button** (or external button to GND) to instantly restart the light show from `00:00:00` (perfect for syncing when hitting play on your phone or speaker!).
- **Serial Monitor HUD (115200 baud)**: Live ASCII progress bar:
  ```
  [01:14 / 03:25] [==========>      ] 36% | Bass: 198 Mid: 82 Trb: 41 Beat: BEAT!
  ```
  Commands: `'r'` (restart), `' '` (pause/resume), `'l'` (loop toggle), `'+'/'-'` (brightness), `'m'` (cycle WS2812B pattern).

---

## 🔌 Hardware Wiring Guide (ESP32)

| Function | ESP32 GPIO | Description |
|---|---|---|
| **LED Strip PWM** | GPIO 25 | Gate of MOSFET driver (12V/24V single-color/strip) |
| **Christmas Lights PWM** | GPIO 26 | Gate of MOSFET driver (12V/24V string) |
| **WS2812B Data Pin** | GPIO 27 | 330Ω resistor to DIN pin of addressable strip |
| **Sync Restart Button** | GPIO 0 | Built-in ESP32 BOOT button (or switch to GND) |
| **MicroSD SPI CS** | GPIO 5 | Chip Select pin of MicroSD module |
| **MicroSD SPI MOSI** | GPIO 23 | MOSI pin of MicroSD module |
| **MicroSD SPI MISO** | GPIO 19 | MISO pin of MicroSD module |
| **MicroSD SPI SCK** | GPIO 18 | SCK / Clock pin of MicroSD module |
| **GND** | GND | Common ground between ESP32 and power supplies |
