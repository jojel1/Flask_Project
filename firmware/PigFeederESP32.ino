
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <DHT.h>

// ==================================================
// 1. WIFI AND FLASK SERVER
// ==================================================

const char* WIFI_SSID = "ZX-2.4G-fiyv_EXT";
const char* WIFI_PASSWORD = "rome2024";

// Use your computer's actual IPv4 address.
// Keep port 5050.
const char* SERVER_URL = "http://192.168.1.6:5050";

// Use the NEW token that matches your .env file.
const char* DEVICE_TOKEN = "CjMThlLXS00yJFvyGWPcheqs-ty5J9CO9_uHOoz6oUM";

const char* DEVICE_ID = "pig-feeder-01";

// ==================================================
// 2. PIN CONFIGURATION
// ==================================================

#define DHT_PIN 4
#define DHT_TYPE DHT22

#define HOPPER_SERVO_PIN 25
#define TRAY_SERVO_PIN 26

DHT dht(DHT_PIN, DHT_TYPE);

Servo hopperServo;
Servo trayServo;

// ==================================================
// 3. SERVO POSITIONS
// Adjust after testing your mechanism safely.
// ==================================================

const int HOPPER_CLOSED_ANGLE = 0;
const int HOPPER_OPEN_ANGLE = 30;

const int TRAY_READY_ANGLE = 0;
const int TRAY_DUMP_ANGLE = 90;

// ==================================================
// 4. TIMING
// ==================================================

const unsigned long TELEMETRY_INTERVAL_MS = 5000;
const unsigned long COMMAND_POLL_INTERVAL_MS = 2000;

const unsigned long MINIMUM_COMMAND_DURATION_MS = 60000;

const float ESTIMATED_SECONDS_PER_100G = 1.0;
const unsigned long MAX_HOPPER_OPEN_MS = 15000;

// ==================================================
// 5. VARIABLES
// ==================================================

unsigned long lastTelemetryTime = 0;
unsigned long lastCommandPollTime = 0;

bool feedingInProgress = false;

// ==================================================
// 6. AUTHENTICATION
// ==================================================

void addAuthorizationHeader(HTTPClient &http) {
  http.addHeader(
    "Authorization",
    String("Bearer ") + DEVICE_TOKEN
  );
}

// ==================================================
// 7. WIFI CONNECTION
// ==================================================

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected!");

  Serial.print("ESP32 IP address: ");
  Serial.println(WiFi.localIP());

  Serial.print("Flask server: ");
  Serial.println(SERVER_URL);
}

// ==================================================
// 8. SEND TELEMETRY TO FLASK
// ==================================================

void sendTelemetry() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Telemetry skipped: WiFi disconnected.");
    return;
  }

  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();

  bool dhtValid =
      !isnan(temperature) && !isnan(humidity);

  Serial.print("Temperature: ");
  if (dhtValid) {
    Serial.print(temperature);
    Serial.println(" C");
  } else {
    Serial.println("Sensor read failed");
  }

  Serial.print("Humidity: ");
  if (dhtValid) {
    Serial.print(humidity);
    Serial.println(" %");
  } else {
    Serial.println("Sensor read failed");
  }

  JsonDocument doc;

  doc["device_id"] = DEVICE_ID;

  if (dhtValid) {
    doc["temperature_c"] = temperature;
    doc["humidity_pct"] = humidity;
  } else {
    doc["temperature_c"] = nullptr;
    doc["humidity_pct"] = nullptr;
  }

  // Load cells are not being used.
  // Do not report fake weight measurements.
  doc["pig_weight_kg"] = nullptr;
  doc["weigh_bin_weight_g"] = nullptr;
  doc["target_feed_g"] = nullptr;

  doc["feeder_status"] =
      feedingInProgress ? "feeding" : "idle";

  String payload;
  serializeJson(doc, payload);

  HTTPClient http;

  String url =
      String(SERVER_URL) + "/api/telemetry";

  http.begin(url);
  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  http.addHeader(
      "Content-Type",
      "application/json"
  );

  addAuthorizationHeader(http);

  int httpCode = http.POST(payload);

  Serial.print("Telemetry HTTP status: ");
  Serial.println(httpCode);

  if (httpCode >= 200 && httpCode < 300) {
    Serial.println("Telemetry sent successfully.");
  } else if (httpCode > 0) {
    Serial.println(http.getString());
  } else {
    Serial.println("Cannot connect to Flask.");
  }

  http.end();
}

// ==================================================
// 9. SERVO FEEDING SEQUENCE
// ==================================================

bool dispenseFeed(float targetGrams) {
  if (feedingInProgress) {
    Serial.println("Feeding already in progress.");
    return false;
  }

  if (targetGrams < 1 || targetGrams > 5000) {
    Serial.println("Invalid feed amount.");
    return false;
  }

  feedingInProgress = true;

  Serial.println();
  Serial.println("===== FEEDING STARTED =====");

  Serial.print("Requested feed: ");
  Serial.print(targetGrams);
  Serial.println(" g");

  // Make sure tray is ready.
  trayServo.write(TRAY_READY_ANGLE);
  delay(500);

  // Open hopper.
  hopperServo.write(HOPPER_OPEN_ANGLE);

  unsigned long hopperDuration =
      (unsigned long)(
        (targetGrams / 100.0) *
        ESTIMATED_SECONDS_PER_100G *
        1000.0
      );

  if (hopperDuration > MAX_HOPPER_OPEN_MS) {
    hopperDuration = MAX_HOPPER_OPEN_MS;
  }

  Serial.print("Hopper open time: ");
  Serial.print(hopperDuration);
  Serial.println(" ms");

  delay(hopperDuration);

  // Close hopper.
  hopperServo.write(HOPPER_CLOSED_ANGLE);
  delay(700);

  // Release feed from tray.
  trayServo.write(TRAY_DUMP_ANGLE);
  delay(1200);

  // Return tray to ready position.
  trayServo.write(TRAY_READY_ANGLE);
  delay(700);

  feedingInProgress = false;

  Serial.println("Servo sequence finished.");
  Serial.println("Actual feed mass is unverified.");
  Serial.println("===========================");

  return true;
}

// ==================================================
// 10. REPORT COMMAND RESULT
// ==================================================

void reportCommandResult(
    int commandId,
    bool success,
    const char* message
) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(
        "Cannot report command result: WiFi disconnected."
    );
    return;
  }

  JsonDocument doc;

  doc["status"] =
      success ? "completed" : "failed";

  doc["result"] = message;

  String payload;
  serializeJson(doc, payload);

  HTTPClient http;

  String url =
      String(SERVER_URL) +
      "/api/device/commands/" +
      String(commandId) +
      "/result";

  http.begin(url);
  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  http.addHeader(
      "Content-Type",
      "application/json"
  );

  addAuthorizationHeader(http);

  int httpCode = http.POST(payload);

  Serial.print("Result report HTTP status: ");
  Serial.println(httpCode);

  if (httpCode >= 200 && httpCode < 300) {
    Serial.println("Command result saved.");
  } else if (httpCode > 0) {
    Serial.println(http.getString());
  } else {
    Serial.println("Failed to send command result.");
  }

  http.end();
}

// ==================================================
// 11. CHECK FOR DASHBOARD COMMANDS
// ==================================================

void checkForCommands() {
  if (WiFi.status() != WL_CONNECTED ||
      feedingInProgress) {
    return;
  }

  HTTPClient http;

  String url =
      String(SERVER_URL) +
      "/api/device/commands/next";

  http.begin(url);
  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  addAuthorizationHeader(http);

  int httpCode = http.GET();

  if (httpCode != 200) {
    Serial.print("Command poll HTTP status: ");
    Serial.println(httpCode);

    if (httpCode > 0) {
      Serial.println(http.getString());
    }

    http.end();
    return;
  }

  String response = http.getString();
  http.end();

  JsonDocument doc;

  DeserializationError error =
      deserializeJson(doc, response);

  if (error) {
    Serial.print("JSON parsing error: ");
    Serial.println(error.c_str());
    return;
  }

  if (doc["command"].isNull()) {
    return;
  }

  JsonObject command = doc["command"];

  int commandId = command["id"] | -1;

  const char* commandName =
      command["command"] | "";

  float targetGrams =
      command["target_grams"] | 0.0;

  if (commandId < 0) {
    Serial.println("Invalid command ID.");
    return;
  }

  Serial.print("Received command: ");
  Serial.println(commandName);

  if (strcmp(commandName, "dispense") != 0) {
    reportCommandResult(
        commandId,
        false,
        "Unsupported command."
    );
    return;
  }

  if (targetGrams < 1 || targetGrams > 5000) {
    reportCommandResult(
        commandId,
        false,
        "Invalid target feed amount."
    );
    return;
  }

  // Start the one-minute status timer.
  unsigned long feedStartTime = millis();

  bool success = dispenseFeed(targetGrams);

  if (success) {
    unsigned long elapsed =
        millis() - feedStartTime;

    // Keep the command running until 60 seconds
    // have elapsed from the start of feeding.
    if (elapsed < MINIMUM_COMMAND_DURATION_MS) {
      unsigned long remaining =
          MINIMUM_COMMAND_DURATION_MS - elapsed;

      Serial.print("Waiting before completion report: ");
      Serial.print(remaining / 1000);
      Serial.println(" seconds");

      delay(remaining);
    }

    reportCommandResult(
        commandId,
        true,
        "success"
    );
  } else {
    reportCommandResult(
        commandId,
        false,
        "Feeding sequence could not start."
    );
  }

  Serial.println("Command processing finished.");
}

// ==================================================
// 12. SETUP
// ==================================================

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Pig Feeding IoT - ESP32-WROOM");
  Serial.println("Flask port: 5050");

  dht.begin();

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  hopperServo.setPeriodHertz(50);
  trayServo.setPeriodHertz(50);

  hopperServo.attach(
      HOPPER_SERVO_PIN, 500, 2500
  );

  trayServo.attach(
      TRAY_SERVO_PIN, 500, 2500
  );

  hopperServo.write(HOPPER_CLOSED_ANGLE);
  trayServo.write(TRAY_READY_ANGLE);

  connectWiFi();

  Serial.println("Setup complete.");
}

// ==================================================
// 13. MAIN LOOP
// ==================================================

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected. Reconnecting...");

    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    delay(1000);
    return;
  }

  unsigned long now = millis();

  if (!feedingInProgress &&
      now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryTime = now;
    sendTelemetry();
  }

  if (!feedingInProgress &&
      now - lastCommandPollTime >= COMMAND_POLL_INTERVAL_MS) {
    lastCommandPollTime = now;
    checkForCommands();
  }

  delay(20);
}
