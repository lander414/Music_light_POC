/*
  Music Light Sync - ESP32 UDP receiver example
  Receives JSON control packets from the Python app on UDP port 4210.
  IMPORTANT: PWM pins here are for LOW-VOLTAGE MOSFET driver inputs only.
  Do not connect mains Christmas lights directly to ESP32 pins.
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>

const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

WiFiUDP udp;
const uint16_t UDP_PORT = 4210;

// Example low-voltage MOSFET channels. Adjust to your board and driver.
const int LED_STRIP_PWM_PIN = 25;
const int CHRISTMAS_PWM_PIN = 26;
const int PWM_FREQ = 5000;
const int PWM_RESOLUTION = 8;
const int PWM_CHANNEL_STRIP = 0;
const int PWM_CHANNEL_XMAS = 1;

float brightness = 0.0f;
float bassLevel = 0.0f;
float midLevel = 0.0f;
float trebleLevel = 0.0f;
float audioLevel = 0.0f;
float beatLevel = 0.0f;
float breathSpeed = 0.45f;
String effectMode = "Music Reactive";
unsigned long lastPacketMs = 0;
unsigned long lastUpdateMs = 0;

float clamp01(float v) { return constrain(v, 0.0f, 1.0f); }

void setup() {
  Serial.begin(115200);
  ledcSetup(PWM_CHANNEL_STRIP, PWM_FREQ, PWM_RESOLUTION);
  ledcSetup(PWM_CHANNEL_XMAS, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(LED_STRIP_PWM_PIN, PWM_CHANNEL_STRIP);
  ledcAttachPin(CHRISTMAS_PWM_PIN, PWM_CHANNEL_XMAS);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
  udp.begin(UDP_PORT);
  Serial.printf("Listening for UDP JSON on %u\n", UDP_PORT);
}

void loop() {
  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    char packet[512];
    int len = udp.read(packet, sizeof(packet) - 1);
    if (len > 0) {
      packet[len] = '\0';
      StaticJsonDocument<512> doc;
      DeserializationError err = deserializeJson(doc, packet);
      if (!err) {
        String type = doc["type"] | "";
        if (type == "lights") {
          brightness = clamp01(doc["brightness"] | 0.0f);
          bassLevel = clamp01(doc["bass"] | 0.0f);
          midLevel = clamp01(doc["mid"] | 0.0f);
          trebleLevel = clamp01(doc["treble"] | 0.0f);
          audioLevel = clamp01(doc["level"] | 0.0f);
          beatLevel = clamp01(doc["beat"] | 0.0f);
          breathSpeed = constrain((float)(doc["breath_speed"] | 0.45f), 0.1f, 2.0f);
          effectMode = String((const char*)(doc["mode"] | "Music Reactive"));
          lastPacketMs = millis();
        } else if (type == "ping") {
          udp.beginPacket(udp.remoteIP(), udp.remotePort());
          udp.print("{\"type\":\"pong\",\"device\":\"ESP32\"}");
          udp.endPacket();
          lastPacketMs = millis();
        }
      }
    }
  }

  // Fail safe: fade to off if the PC stops sending packets for 2 seconds.
  if (millis() - lastPacketMs > 2000) {
    brightness = 0.0f;
  }

  if (millis() - lastUpdateMs >= 20) {
    lastUpdateMs = millis();
    float t = millis() / 1000.0f;
    float breath = 0.15f + 0.85f * (0.5f + 0.5f * sinf(t * breathSpeed * 2.2f));
    float strip = 0.0f;
    float xmas = 0.0f;

    if (effectMode == "Breathing") {
      strip = brightness * breath;
      xmas = brightness * breath;
    } else if (effectMode == "Bass Pulse") {
      strip = brightness * (0.12f + 0.88f * bassLevel);
      xmas = brightness * (0.12f + 0.88f * bassLevel);
    } else if (effectMode == "Music Reactive") {
      strip = brightness * (0.18f + 0.55f * bassLevel + 0.20f * midLevel + 0.25f * beatLevel);
      xmas = brightness * (0.15f + 0.65f * audioLevel + 0.25f * beatLevel);
    } else if (effectMode == "Off") {
      strip = 0.0f;
      xmas = 0.0f;
    } else {
      // Chase/rainbow need addressable RGB LEDs for full color effects.
      strip = brightness * (0.5f + 0.5f * breath);
      xmas = brightness * (0.5f + 0.5f * breath);
    }

    int stripDuty = (int)(255.0f * clamp01(strip));
    int xmasDuty = (int)(255.0f * clamp01(xmas));
    ledcWrite(PWM_CHANNEL_STRIP, stripDuty);
    ledcWrite(PWM_CHANNEL_XMAS, xmasDuty);
  }
}
