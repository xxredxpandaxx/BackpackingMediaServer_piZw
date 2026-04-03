#include <Arduino.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <FS.h>
#include <LittleFS.h>
#include <SD_MMC.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp32-hal-psram.h>
#include <esp32-hal-rgb-led.h>
#include <esp_heap_caps.h>
#include <freertos/semphr.h>
#include <qrcode.h>
#include <algorithm>
#include <cctype>
#include <vector>

namespace config {
constexpr char kDeviceName[] = "Nomad Screen";
constexpr char kMdnsHost[] = "nomadscreen";
constexpr char kAccessPointSsid[] = "NomadScreen";
constexpr char kAccessPointPassword[] = "backpackingmedia";
constexpr size_t kLibraryCapacity = 96;
constexpr char kSdMountPoint[] = "/sdcard";
constexpr char kMediaRoot[] = "/media";
constexpr char kMetadataRoot[] = "/media/.nomadscreen";
constexpr char kMetadataIndexPath[] = "/media/.nomadscreen/library.json";
constexpr char kRuntimeConfigPath[] = "/nomadscreen.config.json";
constexpr char kAppPath[] = "/app";
constexpr size_t kStreamChunkSize = 4 * 1024;
constexpr bool kSdOneBitMode = false;
constexpr int kSdClkPin = 14;
constexpr int kSdCmdPin = 15;
constexpr int kSdD0Pin = 16;
constexpr int kSdD1Pin = 18;
constexpr int kSdD2Pin = 17;
constexpr int kSdD3Pin = 21;
constexpr int kLcdMosiPin = 45;
constexpr int kLcdSclkPin = 40;
constexpr int kLcdCsPin = 42;
constexpr int kLcdDcPin = 41;
constexpr int kLcdResetPin = 39;
constexpr int kLcdBacklightPin = 48;
constexpr int kBootButtonPin = 0;
constexpr int kStatusLedPin = 38;
constexpr uint16_t kLcdWidth = 172;
constexpr uint16_t kLcdHeight = 320;
constexpr uint16_t kLcdColOffset = 34;
constexpr uint16_t kLcdRowOffset = 0;
constexpr uint8_t kBacklightPwmChannel = 0;
constexpr uint16_t kBacklightPwmFrequency = 5000;
constexpr uint8_t kBacklightPwmResolution = 8;
constexpr uint8_t kBacklightDutyBright = 255;
constexpr uint8_t kBacklightDutyDim = 72;
constexpr unsigned long kButtonDebounceMs = 30;
constexpr unsigned long kButtonDoublePressMs = 325;
constexpr unsigned long kButtonScreenOffHoldMs = 1000;
constexpr unsigned long kButtonQuietOffHoldMs = 3000;
constexpr uint8_t kStatusLedBrightness = 1;
constexpr uint8_t kSoftApChannel = 1;
constexpr uint8_t kSoftApMaxConnections = 6;  // Enough headroom for 3-4 viewers plus setup/admin.
constexpr uint16_t kMediaStreamPort = 81;
constexpr uint8_t kMediaStreamMaxTasks = 12;  // Browsers often open multiple range requests per player.
constexpr uint16_t kMediaStreamTaskStackWords = 8192;
constexpr size_t kMediaStreamTaskChunkSize = 2 * 1024;
constexpr unsigned long kMediaRequestHeaderTimeoutMs = 2000;
constexpr uint32_t kMediaStreamSocketTimeoutMs = 15000;
constexpr uint8_t kMediaWriteStallRetries = 4;
constexpr unsigned long kMediaWriteStallDelayMs = 25;
constexpr TickType_t kMediaStreamYieldTicks = 1;
constexpr UBaseType_t kMediaStreamTaskPriority = 1;
constexpr BaseType_t kMediaStreamTaskCore = ARDUINO_RUNNING_CORE;
constexpr uint8_t kQrVersion = 3;
constexpr uint8_t kQrBorder = 2;
constexpr uint8_t kQrModuleSize = 4;
}  // namespace config

WebServer server(80);
WiFiServer mediaStreamServer(config::kMediaStreamPort);
SPIClass displaySpi(FSPI);
const char* kCollectedRequestHeaders[] = {"Range"};
uint8_t streamTransferBuffer[config::kStreamChunkSize];

class PreferPsramJsonAllocator : public Allocator {
 public:
  void* allocate(size_t size) override {
    return heap_caps_malloc(size, allocationCaps());
  }

  void deallocate(void* ptr) override {
    heap_caps_free(ptr);
  }

  void* reallocate(void* ptr, size_t new_size) override {
    return heap_caps_realloc(ptr, new_size, allocationCaps());
  }

 private:
  static uint32_t allocationCaps() {
    if (psramFound()) {
      return MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    }
    return MALLOC_CAP_8BIT;
  }
};

PreferPsramJsonAllocator jsonAllocator;

class WaveshareST7789 : public Adafruit_ST7789 {
 public:
  WaveshareST7789(SPIClass* spiClass, int8_t cs, int8_t dc, int8_t rst)
      : Adafruit_ST7789(spiClass, cs, dc, rst) {}

  void setOffsets(uint8_t colStart, uint8_t rowStart, uint8_t colStart2,
                  uint8_t rowStart2) {
    _colstart = colStart;
    _rowstart = rowStart;
    _colstart2 = colStart2;
    _rowstart2 = rowStart2;
  }
};

WaveshareST7789* display = nullptr;
GFXcanvas16* displayCanvas = nullptr;
Adafruit_GFX* displaySurface = nullptr;

enum class DisplayPage : uint8_t {
  kConnectQr = 0,
  kWifiInfo = 1,
  kStatus = 2,
};

enum class StatusLedState : uint8_t {
  kOff = 0,
  kGreen = 1,
  kBlue = 2,
  kRed = 3,
};

struct MediaItem {
  String title;
  String path;
  String type;
  String section;
  String extension;
  String sortTitle;
  String overview;
  String tagline;
  String year;
  String releaseDate;
  String genres;
  String contentRating;
  String artist;
  String album;
  String posterPath;
  String backdropPath;
  String metadataSource;
  float tmdbRating = 0;
  float runtimeMinutes = 0;
  float matchConfidence = 0;
  String showTitle;
  String showSlug;
  String seasonLabel;
  int seasonNumber = 0;
  int episodeNumber = 0;
  bool hasMetadata = false;
  size_t bytes = 0;
};

struct SeasonView {
  String label;
  int number = 0;
  std::vector<const MediaItem*> episodes;
};

struct ShowView {
  String title;
  String slug;
  String year;
  String overview;
  String genres;
  String contentRating;
  String posterPath;
  String backdropPath;
  String metadataSource;
  float tmdbRating = 0;
  float matchConfidence = 0;
  std::vector<SeasonView> seasons;
};

struct ItemMetadata {
  String path;
  String title;
  String sortTitle;
  String overview;
  String tagline;
  String year;
  String releaseDate;
  String genres;
  String contentRating;
  String artist;
  String album;
  String posterPath;
  String backdropPath;
  String metadataSource;
  float tmdbRating = 0;
  String showTitle;
  String showSlug;
  String seasonLabel;
  float runtimeMinutes = 0;
  float matchConfidence = 0;
  int seasonNumber = 0;
  int episodeNumber = 0;
};

struct ShowMetadata {
  String slug;
  String title;
  String year;
  String overview;
  String genres;
  String contentRating;
  String posterPath;
  String backdropPath;
  String metadataSource;
  float tmdbRating = 0;
  float matchConfidence = 0;
};

std::vector<MediaItem> mediaLibrary;
std::vector<ItemMetadata> itemMetadataLibrary;
std::vector<ShowMetadata> showMetadataLibrary;
bool sdMounted = false;
bool displayReady = false;
bool displayDirty = true;
bool mdnsReady = false;
bool metadataAvailable = false;
int lastDisplayClientCount = -1;
DisplayPage activeDisplayPage = DisplayPage::kWifiInfo;
bool displayBacklightEnabled = true;
bool displayBacklightDimmed = true;
bool screenOffIndicatorMuted = false;
bool bootButtonPressed = false;
bool bootButtonLastReading = false;
uint8_t bootButtonHoldStage = 0;
uint8_t bootButtonPendingClicks = 0;
unsigned long bootButtonLastTransitionMs = 0;
unsigned long bootButtonPressedAtMs = 0;
unsigned long bootButtonReleasedAtMs = 0;
String displayHeadline = "Booting media hub";
String displayDetail = "Preparing Wi-Fi access point";
String lastPlaybackTitle;
String lastPlaybackType;
unsigned long lastPlaybackAtMs = 0;
String metadataGeneratedAt;
String metadataGenerator;
bool runtimeConfigError = false;
bool littleFsError = false;
bool metadataError = false;
bool responseOverflowError = false;
bool softApError = false;
bool mdnsError = false;
StatusLedState statusLedState = StatusLedState::kOff;
bool statusLedInitialized = false;
volatile uint8_t activeMediaStreamTasks = 0;
portMUX_TYPE mediaStreamTaskMux = portMUX_INITIALIZER_UNLOCKED;
SemaphoreHandle_t sdIoMutex = nullptr;
String runtimeDeviceName = config::kDeviceName;
String runtimeMdnsHost = config::kMdnsHost;
String runtimeAccessPointSsid = config::kAccessPointSsid;
String runtimeAccessPointPassword = config::kAccessPointPassword;
String runtimeConfigSource = "defaults";

namespace colors {
constexpr uint16_t kBackground = 0x0843;
constexpr uint16_t kPanel = 0x10A5;
constexpr uint16_t kPanelAlt = 0x18E8;
constexpr uint16_t kOutline = 0x31CC;
constexpr uint16_t kText = 0xFFFF;
constexpr uint16_t kMuted = 0xA534;
constexpr uint16_t kAccent = 0x3DFF;
constexpr uint16_t kWarm = 0xF3E6;
constexpr uint16_t kSuccess = 0x97F2;
constexpr uint16_t kDanger = 0xF186;
}  // namespace colors

void markDisplayDirty() {
  displayDirty = true;
}

Adafruit_GFX* activeDisplaySurface() {
  if (displaySurface != nullptr) {
    return displaySurface;
  }
  return display;
}

uint8_t currentBacklightDuty() {
  return displayBacklightDimmed ? config::kBacklightDutyDim : config::kBacklightDutyBright;
}

bool hasSystemError() {
  return runtimeConfigError || littleFsError || metadataError || responseOverflowError ||
         softApError || mdnsError;
}

void writeStatusLed(uint8_t red, uint8_t green, uint8_t blue) {
  neopixelWrite(config::kStatusLedPin, red, green, blue);
}

StatusLedState desiredStatusLedState() {
  if (displayBacklightEnabled || screenOffIndicatorMuted) {
    return StatusLedState::kOff;
  }
  if (!sdMounted) {
    return StatusLedState::kBlue;
  }
  if (hasSystemError()) {
    return StatusLedState::kRed;
  }
  return StatusLedState::kGreen;
}

void applyStatusLed() {
  const StatusLedState nextState = desiredStatusLedState();
  if (statusLedInitialized && nextState == statusLedState) {
    return;
  }

  statusLedInitialized = true;
  statusLedState = nextState;
  switch (statusLedState) {
    case StatusLedState::kOff:
      writeStatusLed(0, 0, 0);
      break;
    case StatusLedState::kGreen:
      writeStatusLed(0, config::kStatusLedBrightness, 0);
      break;
    case StatusLedState::kBlue:
      writeStatusLed(0, 0, config::kStatusLedBrightness);
      break;
    case StatusLedState::kRed:
      writeStatusLed(config::kStatusLedBrightness, 0, 0);
      break;
  }
}

void applyBacklightLevel() {
  const uint8_t duty = displayBacklightEnabled ? currentBacklightDuty() : 0;
  ledcWrite(config::kBacklightPwmChannel, duty);
  applyStatusLed();
}

const char* displayPageLabel(DisplayPage page) {
  switch (page) {
    case DisplayPage::kConnectQr:
      return "QR";
    case DisplayPage::kWifiInfo:
      return "WIFI";
    case DisplayPage::kStatus:
      return "STATUS";
  }
  return "SCREEN";
}

void cycleDisplayPage() {
  if (!displayBacklightEnabled) {
    return;
  }

  switch (activeDisplayPage) {
    case DisplayPage::kConnectQr:
      activeDisplayPage = DisplayPage::kStatus;
      break;
    case DisplayPage::kWifiInfo:
      activeDisplayPage = DisplayPage::kConnectQr;
      break;
    case DisplayPage::kStatus:
      activeDisplayPage = DisplayPage::kWifiInfo;
      break;
  }

  Serial.printf("Display page changed to %s\n", displayPageLabel(activeDisplayPage));
  markDisplayDirty();
}

void setDisplayPowerState(bool enabled, bool muteIndicatorWhenOff) {
  displayBacklightEnabled = enabled;
  screenOffIndicatorMuted = !enabled && muteIndicatorWhenOff;
  applyBacklightLevel();
  Serial.printf("Display turned %s%s\n", displayBacklightEnabled ? "on" : "off",
                (!displayBacklightEnabled && screenOffIndicatorMuted) ? " with indicators muted"
                                                                      : "");
  if (enabled) {
    markDisplayDirty();
  }
}

void toggleDisplayBacklightPower() {
  setDisplayPowerState(!displayBacklightEnabled, false);
}

void enterQuietScreenOffMode() {
  setDisplayPowerState(false, true);
}

void toggleDisplayBrightnessPreset() {
  displayBacklightDimmed = !displayBacklightDimmed;
  displayBacklightEnabled = true;
  screenOffIndicatorMuted = false;
  applyBacklightLevel();
  Serial.printf("Display brightness set to %s (%u)\n",
                displayBacklightDimmed ? "dim" : "bright",
                static_cast<unsigned>(currentBacklightDuty()));
  markDisplayDirty();
}

String trimForDisplay(const String& text, size_t maxLength) {
  if (text.length() <= maxLength) {
    return text;
  }
  return text.substring(0, maxLength - 3) + "...";
}

String normalizeDeviceName(const String& value) {
  String normalized;
  normalized.reserve(value.length());
  bool previousSpace = false;

  for (size_t index = 0; index < value.length(); ++index) {
    const unsigned char character = static_cast<unsigned char>(value[index]);
    if (isspace(character)) {
      if (!previousSpace && !normalized.isEmpty()) {
        normalized += ' ';
      }
      previousSpace = true;
      continue;
    }

    if (character < 32) {
      continue;
    }

    normalized += static_cast<char>(character);
    previousSpace = false;
  }

  normalized.trim();
  return normalized;
}

String deriveCompactDeviceToken(const String& deviceName, bool lowercase) {
  const String normalized = normalizeDeviceName(deviceName);
  String derived;
  derived.reserve(normalized.length());
  bool capitalizeNext = true;

  for (size_t index = 0; index < normalized.length(); ++index) {
    const unsigned char character = static_cast<unsigned char>(normalized[index]);
    if (isalnum(character)) {
      char output = static_cast<char>(character);
      if (lowercase) {
        output = static_cast<char>(tolower(character));
      } else if (capitalizeNext) {
        output = static_cast<char>(toupper(character));
      } else {
        output = static_cast<char>(tolower(character));
      }

      derived += output;
      capitalizeNext = false;
      continue;
    }

    if (!derived.isEmpty()) {
      capitalizeNext = true;
    }
  }

  return derived;
}

String sanitizeMdnsHost(const String& value) {
  String sanitized;
  sanitized.reserve(value.length());
  bool previousDash = false;

  for (size_t index = 0; index < value.length(); ++index) {
    const char character = value[index];
    if (isalnum(static_cast<unsigned char>(character))) {
      sanitized += static_cast<char>(tolower(static_cast<unsigned char>(character)));
      previousDash = false;
      continue;
    }

    if ((character == ' ' || character == '-' || character == '_' || character == '.') &&
        !previousDash && !sanitized.isEmpty()) {
      sanitized += '-';
      previousDash = true;
    }
  }

  while (sanitized.endsWith("-")) {
    sanitized.remove(sanitized.length() - 1);
  }

  if (sanitized.length() > 63) {
    sanitized.remove(63);
    while (sanitized.endsWith("-")) {
      sanitized.remove(sanitized.length() - 1);
    }
  }

  return sanitized;
}

String configuredDeviceName() {
  return runtimeDeviceName.isEmpty() ? String(config::kDeviceName) : runtimeDeviceName;
}

String derivedAccessPointSsid(const String& deviceName) {
  const String derived = deriveCompactDeviceToken(deviceName, false);
  return derived.isEmpty() ? String(config::kAccessPointSsid) : derived;
}

String derivedMdnsHost(const String& deviceName) {
  const String compact = deriveCompactDeviceToken(deviceName, true);
  if (!compact.isEmpty()) {
    return compact;
  }

  const String fallback = sanitizeMdnsHost(deviceName);
  return fallback.isEmpty() ? String(config::kMdnsHost) : fallback;
}

String configuredSsid() {
  return runtimeAccessPointSsid.isEmpty() ? String(config::kAccessPointSsid)
                                          : runtimeAccessPointSsid;
}

String configuredPassword() {
  return runtimeAccessPointPassword;
}

String currentAccessPointIp() {
  IPAddress ip = WiFi.softAPIP();
  if (ip == IPAddress(0, 0, 0, 0)) {
    return "starting";
  }
  return ip.toString();
}

String mdnsHostName() {
  return (runtimeMdnsHost.isEmpty() ? String(config::kMdnsHost) : runtimeMdnsHost) + ".local";
}

String ipAppUrl() {
  return "http://" + currentAccessPointIp() + config::kAppPath;
}

String mdnsAppUrl() {
  return "http://" + mdnsHostName() + config::kAppPath;
}

String preferredAppUrl() {
  return mdnsReady ? mdnsAppUrl() : ipAppUrl();
}

String wifiJoinQrPayload() {
  const String ssid = configuredSsid();
  const String password = configuredPassword();
  return password.isEmpty()
             ? "WIFI:T:nopass;S:" + ssid + ";;"
             : "WIFI:T:WPA;S:" + ssid + ";P:" + password + ";;";
}

String urlEncode(const String& value) {
  static const char hex[] = "0123456789ABCDEF";
  String encoded;
  encoded.reserve(value.length() * 3);

  for (size_t index = 0; index < value.length(); ++index) {
    const unsigned char character = static_cast<unsigned char>(value[index]);
    const bool isUnreserved =
        isalnum(character) || character == '-' || character == '_' ||
        character == '.' || character == '~';

    if (isUnreserved) {
      encoded += static_cast<char>(character);
      continue;
    }

    encoded += '%';
    encoded += hex[(character >> 4) & 0x0F];
    encoded += hex[character & 0x0F];
  }

  return encoded;
}

int hexDigitValue(char character) {
  if (character >= '0' && character <= '9') {
    return character - '0';
  }
  if (character >= 'A' && character <= 'F') {
    return character - 'A' + 10;
  }
  if (character >= 'a' && character <= 'f') {
    return character - 'a' + 10;
  }
  return -1;
}

String urlDecode(const String& value) {
  String decoded;
  decoded.reserve(value.length());

  for (size_t index = 0; index < value.length(); ++index) {
    const char character = value[index];
    if (character == '+' ) {
      decoded += ' ';
      continue;
    }

    if (character == '%' && (index + 2) < value.length()) {
      const int high = hexDigitValue(value[index + 1]);
      const int low = hexDigitValue(value[index + 2]);
      if (high >= 0 && low >= 0) {
        decoded += static_cast<char>((high << 4) | low);
        index += 2;
        continue;
      }
    }

    decoded += character;
  }

  return decoded;
}

String lowercaseCopy(const String& value) {
  String lowered = value;
  lowered.toLowerCase();
  return lowered;
}

bool lockSdIo(TickType_t waitTicks = portMAX_DELAY) {
  return sdIoMutex == nullptr || xSemaphoreTake(sdIoMutex, waitTicks) == pdTRUE;
}

void unlockSdIo() {
  if (sdIoMutex != nullptr) {
    xSemaphoreGive(sdIoMutex);
  }
}

uint8_t* allocateMediaStreamBuffer(size_t size) {
  uint8_t* buffer = nullptr;
  if (psramFound()) {
    buffer = static_cast<uint8_t*>(
        heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  }
  if (buffer == nullptr) {
    buffer = static_cast<uint8_t*>(heap_caps_malloc(size, MALLOC_CAP_8BIT));
  }
  return buffer;
}

String normalizeSdPath(const String& rawPath);
bool sdPathExists(const String& rawPath);
File openSdPath(const String& rawPath, const char* mode = nullptr);
void updatePlaybackStateForPath(const String& path);

String configStringValue(JsonVariantConst primary) {
  const char* primaryValue = primary.isNull() ? nullptr : primary.as<const char*>();
  if (primaryValue != nullptr && primaryValue[0] != '\0') {
    return String(primaryValue);
  }

  return "";
}

String configStringValue(JsonVariantConst primary, JsonVariantConst secondary) {
  const String primaryValue = configStringValue(primary);
  if (!primaryValue.isEmpty()) {
    return primaryValue;
  }

  return configStringValue(secondary);
}

void resetRuntimeConfig() {
  runtimeDeviceName = normalizeDeviceName(config::kDeviceName);
  if (runtimeDeviceName.isEmpty()) {
    runtimeDeviceName = config::kDeviceName;
  }
  runtimeMdnsHost = derivedMdnsHost(runtimeDeviceName);
  runtimeAccessPointSsid = derivedAccessPointSsid(runtimeDeviceName);
  runtimeAccessPointPassword = config::kAccessPointPassword;
  runtimeConfigSource = "defaults";
  runtimeConfigError = false;
}

void loadRuntimeConfigFromSd() {
  resetRuntimeConfig();

  if (!sdPathExists(config::kRuntimeConfigPath)) {
    Serial.println("Runtime config not found on SD card, using built-in defaults");
    return;
  }

  File file = openSdPath(config::kRuntimeConfigPath, "r");
  if (!file) {
    Serial.println("Runtime config exists but could not be opened");
    return;
  }

  JsonDocument doc(&jsonAllocator);
  const DeserializationError error = deserializeJson(doc, file);
  file.close();
  if (error) {
    Serial.print("Runtime config parse failed: ");
    Serial.println(error.c_str());
    runtimeConfigError = true;
    applyStatusLed();
    return;
  }

  runtimeConfigError = false;
  const String configuredName = normalizeDeviceName(configStringValue(doc["deviceName"], doc["serverName"]));
  if (!configuredName.isEmpty()) {
    runtimeDeviceName = configuredName;
  }

  runtimeAccessPointSsid = derivedAccessPointSsid(runtimeDeviceName);
  runtimeMdnsHost = derivedMdnsHost(runtimeDeviceName);

  const String configuredPasswordValue =
      configStringValue(doc["wifiPassword"], doc["wifi"]["password"]);
  if (!configuredPasswordValue.isEmpty()) {
    runtimeAccessPointPassword = configuredPasswordValue;
  }

  if (runtimeAccessPointPassword.length() > 0 && runtimeAccessPointPassword.length() < 8) {
    Serial.println("Configured Wi-Fi password is shorter than 8 characters; using built-in default");
    runtimeAccessPointPassword = config::kAccessPointPassword;
  }

  runtimeConfigSource = String(config::kRuntimeConfigPath);
  Serial.printf("Runtime config loaded: device '%s', derived SSID '%s', derived mDNS '%s'\n",
                runtimeDeviceName.c_str(), runtimeAccessPointSsid.c_str(),
                runtimeMdnsHost.c_str());
  applyStatusLed();
}

String classifyMediaType(const String& lowerPath);

String normalizeSdPath(const String& rawPath) {
  String normalized = rawPath;
  normalized.trim();
  normalized.replace('\\', '/');

  if (normalized.isEmpty()) {
    return "";
  }
  if (!normalized.startsWith("/")) {
    normalized = "/" + normalized;
  }
  while (normalized.indexOf("//") >= 0) {
    normalized.replace("//", "/");
  }

  const String mountPoint = String(config::kSdMountPoint);
  const String lowered = lowercaseCopy(normalized);
  const String loweredMountPoint = lowercaseCopy(mountPoint);
  if (lowered == loweredMountPoint) {
    return "/";
  }
  if (lowered.startsWith(loweredMountPoint + "/")) {
    normalized = normalized.substring(mountPoint.length());
    if (normalized.isEmpty()) {
      normalized = "/";
    }
  }

  return normalized;
}

String mountedSdPath(const String& rawPath) {
  const String normalized = normalizeSdPath(rawPath);
  if (normalized.isEmpty()) {
    return "";
  }
  if (normalized == "/") {
    return String(config::kSdMountPoint);
  }
  return String(config::kSdMountPoint) + normalized;
}

bool sdPathExists(const String& rawPath) {
  const String normalized = normalizeSdPath(rawPath);
  if (normalized.isEmpty()) {
    return false;
  }

  if (!lockSdIo()) {
    return false;
  }

  const bool existsNormalized = SD_MMC.exists(normalized);
  bool existsMounted = false;
  if (!existsNormalized) {
    const String mounted = mountedSdPath(normalized);
    existsMounted = mounted != normalized && SD_MMC.exists(mounted);
  }
  unlockSdIo();

  if (existsNormalized) {
    return true;
  }
  return existsMounted;
}

File openSdPath(const String& rawPath, const char* mode) {
  const String normalized = normalizeSdPath(rawPath);
  if (normalized.isEmpty()) {
    return File();
  }

  if (!lockSdIo()) {
    return File();
  }

  File file;
  if (mode == nullptr || mode[0] == '\0') {
    file = SD_MMC.open(normalized);
  } else {
    file = SD_MMC.open(normalized, mode);
  }
  if (file) {
    unlockSdIo();
    return file;
  }

  const String mounted = mountedSdPath(normalized);
  if (mounted != normalized) {
    if (mode == nullptr || mode[0] == '\0') {
      file = SD_MMC.open(mounted);
    } else {
      file = SD_MMC.open(mounted, mode);
    }
  }
  unlockSdIo();

  return file;
}

String normalizeSpacing(const String& value) {
  String cleaned = value;
  cleaned.trim();

  String normalized;
  normalized.reserve(cleaned.length());

  bool previousSpace = false;
  for (size_t index = 0; index < cleaned.length(); ++index) {
    char character = cleaned[index];
    const bool isBreak = character == ' ' || character == '_' || character == '-' ||
                         character == '.';
    if (isBreak) {
      if (!previousSpace && !normalized.isEmpty()) {
        normalized += ' ';
      }
      previousSpace = true;
      continue;
    }

    normalized += character;
    previousSpace = false;
  }

  normalized.trim();
  return normalized;
}

String prettifyName(const String& value) {
  String pretty = normalizeSpacing(value);
  return pretty.isEmpty() ? value : pretty;
}

String mediaStreamBaseUrl() {
  const String host = mdnsReady ? mdnsHostName() : currentAccessPointIp();
  return "http://" + host + ":" + String(config::kMediaStreamPort);
}

String streamUrlForPath(const String& path) {
  if (path.isEmpty()) {
    return "";
  }
  return mediaStreamBaseUrl() + "/api/stream?path=" + urlEncode(normalizeSdPath(path));
}

String assetUrlForPath(const String& path) {
  if (path.isEmpty()) {
    return "";
  }
  return "/api/asset?path=" + urlEncode(normalizeSdPath(path));
}

bool isMetadataPath(const String& path) {
  String lowered = lowercaseCopy(normalizeSdPath(path));
  const String metadataRoot = lowercaseCopy(String(config::kMetadataRoot));
  return lowered == metadataRoot || lowered.startsWith(metadataRoot + "/");
}

void mergeStringField(String& target, const String& value) {
  if (!value.isEmpty()) {
    target = value;
  }
}

bool sendJsonDocument(int statusCode, JsonDocument& doc) {
  if (doc.overflowed()) {
    Serial.println("JSON response overflowed before it could be sent");
    responseOverflowError = true;
    applyStatusLed();
    server.send(500, "application/json",
                "{\"error\":\"Response was too large to serialize\"}");
    return false;
  }

  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(measureJson(doc));
  server.send(statusCode, "application/json", "");
  WiFiClient client = server.client();
  serializeJson(doc, client);
  return true;
}

std::vector<String> splitPath(const String& path) {
  std::vector<String> segments;
  int start = 0;

  while (start < path.length()) {
    while (start < path.length() && path[start] == '/') {
      ++start;
    }

    if (start >= path.length()) {
      break;
    }

    int slash = path.indexOf('/', start);
    if (slash < 0) {
      segments.push_back(path.substring(start));
      break;
    }

    segments.push_back(path.substring(start, slash));
    start = slash + 1;
  }

  return segments;
}

String slugify(const String& title) {
  String slug;
  bool previousDash = false;

  for (size_t index = 0; index < title.length(); ++index) {
    char character = title[index];
    if (isalnum(static_cast<unsigned char>(character))) {
      slug += static_cast<char>(tolower(static_cast<unsigned char>(character)));
      previousDash = false;
      continue;
    }

    if (!previousDash && !slug.isEmpty()) {
      slug += '-';
      previousDash = true;
    }
  }

  while (slug.endsWith("-")) {
    slug.remove(slug.length() - 1);
  }

  return slug.isEmpty() ? "library-item" : slug;
}

String sectionFromPath(const std::vector<String>& segments, const String& mediaType) {
  if (segments.size() >= 2 && segments[0] == "media") {
    String section = lowercaseCopy(segments[1]);
    if (section == "photos") {
      return "documents";
    }
    if (section == "movies" || section == "tv" || section == "music" ||
        section == "audiobooks" || section == "documents") {
      return section;
    }
  }

  if (mediaType == "video") {
    return "movies";
  }
  if (mediaType == "audio") {
    return "music";
  }
  if (mediaType == "image") {
    return "documents";
  }
  if (mediaType == "document") {
    return "documents";
  }
  return "library";
}

int parseSeasonNumber(const String& label) {
  String lowered = lowercaseCopy(label);
  if (lowered.startsWith("special")) {
    return 0;
  }

  String digits;
  for (size_t index = 0; index < lowered.length(); ++index) {
    if (isdigit(static_cast<unsigned char>(lowered[index]))) {
      digits += lowered[index];
    }
  }

  if (digits.isEmpty()) {
    return 1;
  }
  return digits.toInt();
}

int parseEpisodeNumber(const String& title) {
  String upper = title;
  upper.toUpperCase();

  for (size_t index = 0; index + 1 < upper.length(); ++index) {
    if (upper[index] == 'E' && isdigit(static_cast<unsigned char>(upper[index + 1]))) {
      String digits;
      for (size_t cursor = index + 1; cursor < upper.length(); ++cursor) {
        if (!isdigit(static_cast<unsigned char>(upper[cursor]))) {
          break;
        }
        digits += upper[cursor];
      }
      if (!digits.isEmpty()) {
        return digits.toInt();
      }
    }
  }

  String leadingDigits;
  for (size_t index = 0; index < upper.length(); ++index) {
    if (!isdigit(static_cast<unsigned char>(upper[index]))) {
      break;
    }
    leadingDigits += upper[index];
  }

  return leadingDigits.isEmpty() ? 0 : leadingDigits.toInt();
}

String titleFromPath(const String& path) {
  int slash = path.lastIndexOf('/');
  String name = slash >= 0 ? path.substring(slash + 1) : path;
  int dot = name.lastIndexOf('.');
  if (dot > 0) {
    name = name.substring(0, dot);
  }
  return prettifyName(name);
}

String fileNameFromPath(const String& path) {
  int slash = path.lastIndexOf('/');
  return slash >= 0 ? path.substring(slash + 1) : path;
}

String sectionForMetadataPath(const String& path) {
  const String normalizedPath = normalizeSdPath(path);
  String loweredPath = normalizedPath;
  loweredPath.toLowerCase();
  return sectionFromPath(splitPath(normalizedPath), classifyMediaType(loweredPath));
}

const ItemMetadata* findItemMetadata(const String& path) {
  const String normalizedPath = normalizeSdPath(path);
  const String loweredPath = lowercaseCopy(normalizedPath);
  const String targetFileName = lowercaseCopy(fileNameFromPath(normalizedPath));
  const String targetSection = sectionForMetadataPath(normalizedPath);
  const ItemMetadata* fallback = nullptr;

  for (const ItemMetadata& item : itemMetadataLibrary) {
    if (lowercaseCopy(item.path) == loweredPath) {
      return &item;
    }

    if (targetFileName.isEmpty()) {
      continue;
    }

    if (lowercaseCopy(fileNameFromPath(item.path)) != targetFileName) {
      continue;
    }

    if (!targetSection.isEmpty() && sectionForMetadataPath(item.path) != targetSection) {
      continue;
    }

    if (fallback != nullptr) {
      return nullptr;
    }
    fallback = &item;
  }

  return fallback;
}

const ShowMetadata* findShowMetadata(const String& slug) {
  for (const ShowMetadata& show : showMetadataLibrary) {
    if (show.slug == slug) {
      return &show;
    }
  }
  return nullptr;
}

void clearMetadataLibrary() {
  itemMetadataLibrary.clear();
  showMetadataLibrary.clear();
  metadataAvailable = false;
  metadataGeneratedAt = "";
  metadataGenerator = "";
  metadataError = false;
}

void loadMetadataLibrary() {
  clearMetadataLibrary();

  if (!sdPathExists(config::kMetadataIndexPath)) {
    return;
  }

  File file = openSdPath(config::kMetadataIndexPath, "r");
  if (!file) {
    Serial.println("Metadata index exists but could not be opened");
    metadataError = true;
    applyStatusLed();
    return;
  }

  JsonDocument doc(&jsonAllocator);
  DeserializationError error = deserializeJson(doc, file);
  file.close();

  if (error) {
    Serial.print("Metadata parse failed: ");
    Serial.println(error.c_str());
    metadataError = true;
    applyStatusLed();
    return;
  }

  metadataError = false;
  metadataGeneratedAt = String(doc["generatedAt"] | "");
  metadataGenerator = String(doc["generator"] | "");

  JsonArrayConst showArray = doc["shows"].as<JsonArrayConst>();
  size_t showArtCount = 0;
  for (JsonObjectConst entry : showArray) {
    ShowMetadata show;
    show.slug = String(entry["slug"] | "");
    show.title = String(entry["title"] | "");
    show.year = String(entry["year"] | "");
    show.overview = String(entry["overview"] | "");
    show.genres = String(entry["genres"] | "");
    show.contentRating = String(entry["contentRating"] | "");
    show.posterPath = normalizeSdPath(String(entry["posterPath"] | ""));
    show.backdropPath = normalizeSdPath(String(entry["backdropPath"] | ""));
    show.metadataSource = String(entry["source"] | "");
    show.tmdbRating = entry["tmdbRating"] | 0.0f;
    show.matchConfidence = entry["matchConfidence"] | 0.0f;
    if (!show.posterPath.isEmpty() || !show.backdropPath.isEmpty()) {
      ++showArtCount;
    }

    if (!show.slug.isEmpty()) {
      showMetadataLibrary.push_back(show);
    }
  }

  JsonArrayConst itemArray = doc["items"].as<JsonArrayConst>();
  size_t itemArtCount = 0;
  for (JsonObjectConst entry : itemArray) {
    ItemMetadata item;
    item.path = normalizeSdPath(String(entry["path"] | ""));
    item.title = String(entry["title"] | "");
    item.sortTitle = String(entry["sortTitle"] | "");
    item.overview = String(entry["overview"] | "");
    item.tagline = String(entry["tagline"] | "");
    item.year = String(entry["year"] | "");
    item.releaseDate = String(entry["releaseDate"] | "");
    item.genres = String(entry["genres"] | "");
    item.contentRating = String(entry["contentRating"] | "");
    item.artist = String(entry["artist"] | "");
    item.album = String(entry["album"] | "");
    item.posterPath = normalizeSdPath(String(entry["posterPath"] | ""));
    item.backdropPath = normalizeSdPath(String(entry["backdropPath"] | ""));
    item.metadataSource = String(entry["source"] | "");
    item.tmdbRating = entry["tmdbRating"] | 0.0f;
    item.showTitle = String(entry["showTitle"] | "");
    item.showSlug = String(entry["showSlug"] | "");
    item.seasonLabel = String(entry["seasonLabel"] | "");
    item.runtimeMinutes = entry["runtimeMinutes"] | 0.0f;
    item.matchConfidence = entry["matchConfidence"] | 0.0f;
    item.seasonNumber = entry["seasonNumber"] | 0;
    item.episodeNumber = entry["episodeNumber"] | 0;
    if (!item.posterPath.isEmpty() || !item.backdropPath.isEmpty()) {
      ++itemArtCount;
    }

    if (!item.path.isEmpty()) {
      itemMetadataLibrary.push_back(item);
    }
  }

  metadataAvailable =
      !itemMetadataLibrary.empty() || !showMetadataLibrary.empty();
  Serial.printf("Metadata loaded: %u item entries (%u with art), %u show entries (%u with art)\n",
                static_cast<unsigned>(itemMetadataLibrary.size()),
                static_cast<unsigned>(itemArtCount),
                static_cast<unsigned>(showMetadataLibrary.size()),
                static_cast<unsigned>(showArtCount));
  applyStatusLed();
}

void applyShowMetadata(MediaItem& item) {
  if (item.section != "tv" || item.showSlug.isEmpty()) {
    return;
  }

  const ShowMetadata* metadata = findShowMetadata(item.showSlug);
  if (metadata == nullptr) {
    return;
  }

  mergeStringField(item.showTitle, metadata->title);
  mergeStringField(item.year, metadata->year);
  mergeStringField(item.overview, metadata->overview);
  mergeStringField(item.genres, metadata->genres);
  mergeStringField(item.contentRating, metadata->contentRating);
  mergeStringField(item.posterPath, metadata->posterPath);
  mergeStringField(item.backdropPath, metadata->backdropPath);
  mergeStringField(item.metadataSource, metadata->metadataSource);
  if (item.tmdbRating <= 0 && metadata->tmdbRating > 0) {
    item.tmdbRating = metadata->tmdbRating;
  }
  if (item.matchConfidence <= 0 && metadata->matchConfidence > 0) {
    item.matchConfidence = metadata->matchConfidence;
  }
  item.hasMetadata = true;
}

void applyItemMetadata(MediaItem& item) {
  const ItemMetadata* metadata = findItemMetadata(item.path);
  if (metadata != nullptr) {
    mergeStringField(item.title, metadata->title);
    mergeStringField(item.sortTitle, metadata->sortTitle);
    mergeStringField(item.overview, metadata->overview);
    mergeStringField(item.tagline, metadata->tagline);
    mergeStringField(item.year, metadata->year);
    mergeStringField(item.releaseDate, metadata->releaseDate);
    mergeStringField(item.genres, metadata->genres);
    mergeStringField(item.contentRating, metadata->contentRating);
    mergeStringField(item.artist, metadata->artist);
    mergeStringField(item.album, metadata->album);
    mergeStringField(item.posterPath, metadata->posterPath);
    mergeStringField(item.backdropPath, metadata->backdropPath);
    mergeStringField(item.metadataSource, metadata->metadataSource);
    mergeStringField(item.showTitle, metadata->showTitle);
    mergeStringField(item.showSlug, metadata->showSlug);
    mergeStringField(item.seasonLabel, metadata->seasonLabel);
    if (metadata->tmdbRating > 0) {
      item.tmdbRating = metadata->tmdbRating;
    }
    if (metadata->runtimeMinutes > 0) {
      item.runtimeMinutes = metadata->runtimeMinutes;
    }
    if (metadata->matchConfidence > 0) {
      item.matchConfidence = metadata->matchConfidence;
    }
    if (metadata->seasonNumber > 0) {
      item.seasonNumber = metadata->seasonNumber;
    }
    if (metadata->episodeNumber > 0) {
      item.episodeNumber = metadata->episodeNumber;
    }
    item.hasMetadata = true;
  }

  if (item.section == "tv") {
    if (item.showSlug.isEmpty() && !item.showTitle.isEmpty()) {
      item.showSlug = slugify(item.showTitle);
    }
    if (item.seasonNumber == 0 && !item.seasonLabel.isEmpty()) {
      item.seasonNumber = parseSeasonNumber(item.seasonLabel);
    }
    if (item.episodeNumber == 0) {
      item.episodeNumber = parseEpisodeNumber(item.title);
    }
    applyShowMetadata(item);
  }
}

void decorateMediaItem(MediaItem& item) {
  const std::vector<String> segments = splitPath(item.path);
  item.section = sectionFromPath(segments, item.type);

  if (item.section != "tv") {
    return;
  }

  const bool hasShowFolder = segments.size() >= 4;
  item.showTitle = hasShowFolder ? prettifyName(segments[2]) : "Unknown Show";
  item.showSlug = slugify(item.showTitle);

  if (segments.size() >= 5) {
    item.seasonLabel = prettifyName(segments[3]);
  } else {
    item.seasonLabel = "Season 1";
  }

  item.seasonNumber = parseSeasonNumber(item.seasonLabel);
  item.episodeNumber = parseEpisodeNumber(item.title);
}

int sectionSortOrder(const String& section) {
  if (section == "movies") return 0;
  if (section == "tv") return 1;
  if (section == "music") return 2;
  if (section == "audiobooks") return 3;
  if (section == "documents") return 4;
  return 4;
}

void sortLibrary() {
  std::sort(mediaLibrary.begin(), mediaLibrary.end(),
            [](const MediaItem& left, const MediaItem& right) {
              if (left.section != right.section) {
                return sectionSortOrder(left.section) < sectionSortOrder(right.section);
              }

              if (left.section == "tv") {
                const String leftShow = lowercaseCopy(left.showTitle);
                const String rightShow = lowercaseCopy(right.showTitle);
                if (leftShow != rightShow) {
                  return leftShow < rightShow;
                }
                if (left.seasonNumber != right.seasonNumber) {
                  return left.seasonNumber < right.seasonNumber;
                }
                if (left.episodeNumber != right.episodeNumber &&
                    left.episodeNumber != 0 && right.episodeNumber != 0) {
                  return left.episodeNumber < right.episodeNumber;
                }
              }

              return lowercaseCopy(left.title) < lowercaseCopy(right.title);
            });
}

size_t countMediaType(const String& type) {
  size_t count = 0;
  for (const MediaItem& item : mediaLibrary) {
    if (item.type == type) {
      ++count;
    }
  }
  return count;
}

String playbackAgeLabel() {
  if (lastPlaybackAtMs == 0 || lastPlaybackTitle.isEmpty()) {
    return "No recent playback";
  }

  unsigned long ageSeconds = (millis() - lastPlaybackAtMs) / 1000;
  if (ageSeconds < 10) {
    return "Streaming now";
  }
  if (ageSeconds < 60) {
    return "Played " + String(ageSeconds) + "s ago";
  }

  unsigned long ageMinutes = ageSeconds / 60;
  if (ageMinutes < 60) {
    return "Played " + String(ageMinutes) + "m ago";
  }
  return "Played " + String(ageMinutes / 60) + "h ago";
}

void setDisplayBanner(const String& headline, const String& detail) {
  displayHeadline = headline;
  displayDetail = detail;
  markDisplayDirty();
}

void drawPanel(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t fill) {
  Adafruit_GFX* surface = activeDisplaySurface();
  surface->fillRoundRect(x, y, w, h, 12, fill);
  surface->drawRoundRect(x, y, w, h, 12, colors::kOutline);
}

void drawPanelLabel(int16_t x, int16_t y, const String& label) {
  Adafruit_GFX* surface = activeDisplaySurface();
  surface->setTextColor(colors::kMuted);
  surface->setTextSize(1);
  surface->setCursor(x, y);
  surface->print(label);
}

void drawQrCode(int16_t x, int16_t y, const String& text) {
  Adafruit_GFX* surface = activeDisplaySurface();
  uint8_t qrcodeData[qrcode_getBufferSize(config::kQrVersion)];
  QRCode qrcode;
  qrcode_initText(&qrcode, qrcodeData, config::kQrVersion, ECC_LOW, text.c_str());

  const int16_t totalModules = qrcode.size + (config::kQrBorder * 2);
  const int16_t totalSize = totalModules * config::kQrModuleSize;

  surface->fillRoundRect(x, y, totalSize, totalSize, 12, colors::kText);

  for (int16_t row = -config::kQrBorder; row < qrcode.size + config::kQrBorder; ++row) {
    for (int16_t col = -config::kQrBorder; col < qrcode.size + config::kQrBorder; ++col) {
      const bool isBorder =
          row < 0 || col < 0 || row >= qrcode.size || col >= qrcode.size;
      const bool isDark = !isBorder && qrcode_getModule(&qrcode, col, row);
      const uint16_t color = isDark ? colors::kBackground : colors::kText;
      const int16_t px = x + (col + config::kQrBorder) * config::kQrModuleSize;
      const int16_t py = y + (row + config::kQrBorder) * config::kQrModuleSize;
      surface->fillRect(px, py, config::kQrModuleSize, config::kQrModuleSize, color);
    }
  }
}

void drawConnectScreen() {
  Adafruit_GFX* surface = activeDisplaySurface();
  const String host = mdnsHostName();
  const String url = preferredAppUrl();
  const int16_t qrSize =
      (4 * config::kQrVersion + 17 + (config::kQrBorder * 2)) * config::kQrModuleSize;
  const int16_t qrX = (config::kLcdWidth - qrSize) / 2;

  surface->fillScreen(colors::kBackground);

  drawPanel(8, 8, 156, 56, colors::kPanelAlt);
  surface->setTextColor(colors::kAccent);
  surface->setTextSize(1);
  surface->setCursor(18, 20);
  surface->print("JOIN WIFI");
  surface->setTextColor(colors::kText);
  surface->setTextSize(2);
  surface->setCursor(18, 34);
  surface->print(trimForDisplay(configuredSsid(), 14));

  drawPanel(8, 72, 156, 122, colors::kPanel);
  drawQrCode(qrX, 82, url);

  drawPanel(8, 204, 156, 108, colors::kPanelAlt);
  drawPanelLabel(18, 218, "OPEN DIRECTLY");
  surface->setTextColor(colors::kText);
  surface->setTextSize(1);
  surface->setCursor(18, 234);
  surface->print(trimForDisplay(host, 24));
  surface->setCursor(18, 248);
  surface->print("/app");
  surface->setTextColor(colors::kMuted);
  surface->setCursor(18, 266);
  surface->print(trimForDisplay(currentAccessPointIp(), 24));
  surface->setCursor(18, 280);
  surface->print(configuredPassword().isEmpty()
                     ? "Open network"
                     : "Pass: " + trimForDisplay(configuredPassword(), 18));
  surface->setCursor(18, 294);
  surface->print("Scan or open in browser");
}

void drawWifiInfoScreen() {
  Adafruit_GFX* surface = activeDisplaySurface();
  const String wifiPayload = wifiJoinQrPayload();
  const uint8_t clients = WiFi.softAPgetStationNum();
  const int16_t qrSize =
      (4 * config::kQrVersion + 17 + (config::kQrBorder * 2)) * config::kQrModuleSize;
  const int16_t qrX = (config::kLcdWidth - qrSize) / 2;

  surface->fillScreen(colors::kBackground);

  drawPanel(8, 8, 156, 58, colors::kPanelAlt);
  surface->setTextColor(colors::kAccent);
  surface->setTextSize(1);
  surface->setCursor(18, 20);
  surface->print("JOIN WIFI");
  surface->setTextColor(colors::kText);
  surface->setTextSize(2);
  surface->setCursor(18, 36);
  surface->print(trimForDisplay(configuredSsid(), 14));

  drawPanel(8, 74, 156, 134, colors::kPanel);
  drawQrCode(qrX, 84, wifiPayload);

  drawPanel(8, 216, 156, 96, colors::kPanelAlt);
  drawPanelLabel(18, 230, "SCAN TO JOIN");
  surface->setTextColor(colors::kText);
  surface->setTextSize(1);
  surface->setCursor(18, 248);
  surface->print(configuredPassword().isEmpty()
                     ? "Open network"
                     : "Pass: " + trimForDisplay(configuredPassword(), 18));
  surface->setTextColor(colors::kMuted);
  surface->setCursor(18, 266);
  surface->print(trimForDisplay(String(clients) + " client(s) connected", 24));
  surface->setCursor(18, 284);
  surface->print("Open camera to scan");
  surface->setCursor(18, 298);
  surface->print("Then browse to /app");
}

void drawStatusScreen() {
  Adafruit_GFX* surface = activeDisplaySurface();
  const String ip = currentAccessPointIp();
  const size_t videos = countMediaType("video");
  const size_t audio = countMediaType("audio");
  const size_t images = countMediaType("image");
  const uint8_t clients = WiFi.softAPgetStationNum();

  surface->fillScreen(colors::kBackground);

  drawPanel(8, 8, 156, 56, colors::kPanelAlt);
  surface->setTextColor(colors::kAccent);
  surface->setTextSize(1);
  surface->setCursor(18, 20);
  surface->print("SERVER");
  surface->setTextColor(colors::kText);
  surface->setTextSize(2);
  surface->setCursor(18, 34);
  surface->print(trimForDisplay(configuredDeviceName(), 14));

  drawPanel(8, 70, 156, 66, colors::kPanel);
  drawPanelLabel(18, 84, "OPEN");
  surface->setTextColor(colors::kText);
  surface->setTextSize(1);
  surface->setCursor(18, 102);
  surface->print(trimForDisplay(mdnsHostName(), 22));
  surface->setCursor(18, 116);
  surface->print("/app");

  drawPanel(8, 144, 156, 54, colors::kPanel);
  drawPanelLabel(18, 158, "SYSTEM");
  surface->setTextColor(sdMounted ? colors::kSuccess : colors::kDanger);
  surface->setTextSize(2);
  surface->setCursor(18, 172);
  surface->print(sdMounted ? "SD Ready" : "No SD");
  surface->setTextColor(colors::kMuted);
  surface->setTextSize(1);
  surface->setCursor(18, 190);
  surface->print(String(mediaLibrary.size()) + " items  |  " + String(clients) + " clients");

  drawPanel(8, 206, 156, 74, colors::kPanelAlt);
  drawPanelLabel(18, 220, lastPlaybackTitle.isEmpty() ? "STATUS" : "RECENT PLAY");
  surface->setTextColor(colors::kText);
  surface->setTextSize(1);
  surface->setCursor(18, 236);
  surface->print(trimForDisplay(lastPlaybackTitle.isEmpty() ? displayHeadline : lastPlaybackTitle, 24));
  surface->setTextColor(colors::kMuted);
  surface->setCursor(18, 250);
  surface->print(trimForDisplay(lastPlaybackTitle.isEmpty() ? displayDetail : playbackAgeLabel(), 24));
  if (!lastPlaybackType.isEmpty()) {
    surface->setCursor(18, 264);
    surface->print(trimForDisplay("Type " + lastPlaybackType, 24));
  } else {
    surface->setCursor(18, 264);
    surface->print(trimForDisplay("IP " + ip, 24));
  }

  drawPanel(8, 288, 156, 24, colors::kPanel);
  surface->setTextColor(colors::kWarm);
  surface->setTextSize(1);
  surface->setCursor(18, 296);
  surface->print("V ");
  surface->print(String(videos));
  surface->setTextColor(colors::kAccent);
  surface->setCursor(58, 296);
  surface->print("A ");
  surface->print(String(audio));
  surface->setTextColor(colors::kSuccess);
  surface->setCursor(98, 296);
  surface->print("P ");
  surface->print(String(images));
}

void drawActiveDisplayPage() {
  switch (activeDisplayPage) {
    case DisplayPage::kConnectQr:
      drawConnectScreen();
      break;
    case DisplayPage::kWifiInfo:
      drawWifiInfoScreen();
      break;
    case DisplayPage::kStatus:
      drawStatusScreen();
      break;
  }
}

void presentDisplay() {
  if (!displayReady || display == nullptr || displayCanvas == nullptr ||
      displayCanvas->getBuffer() == nullptr) {
    return;
  }

  display->drawRGBBitmap(0, 0, displayCanvas->getBuffer(), config::kLcdWidth,
                         config::kLcdHeight);
}

bool readBootButtonRaw() {
  return digitalRead(config::kBootButtonPin) == LOW;
}

void handlePendingBootButtonClicks(unsigned long nowMs) {
  if (bootButtonPressed || bootButtonPendingClicks == 0 ||
      (nowMs - bootButtonReleasedAtMs) < config::kButtonDoublePressMs) {
    return;
  }

  if (bootButtonPendingClicks >= 2) {
    toggleDisplayBrightnessPreset();
  } else {
    cycleDisplayPage();
  }

  bootButtonPendingClicks = 0;
}

void updateBootButtonControls() {
  const unsigned long nowMs = millis();
  const bool rawPressed = readBootButtonRaw();

  if (rawPressed != bootButtonLastReading) {
    bootButtonLastReading = rawPressed;
    bootButtonLastTransitionMs = nowMs;
  }

  if ((nowMs - bootButtonLastTransitionMs) >= config::kButtonDebounceMs &&
      rawPressed != bootButtonPressed) {
    bootButtonPressed = rawPressed;
    if (bootButtonPressed) {
      bootButtonPressedAtMs = nowMs;
      bootButtonHoldStage = 0;
    } else {
      if (bootButtonHoldStage == 0) {
        if (bootButtonPendingClicks < 2) {
          ++bootButtonPendingClicks;
        }
        bootButtonReleasedAtMs = nowMs;
      }
      bootButtonHoldStage = 0;
    }
  }

  if (bootButtonPressed && bootButtonHoldStage == 0 &&
      (nowMs - bootButtonPressedAtMs) >= config::kButtonScreenOffHoldMs) {
    bootButtonHoldStage = 1;
    bootButtonPendingClicks = 0;
    setDisplayPowerState(false, false);
  }

  if (bootButtonPressed && bootButtonHoldStage == 1 &&
      (nowMs - bootButtonPressedAtMs) >= config::kButtonQuietOffHoldMs) {
    bootButtonHoldStage = 2;
    bootButtonPendingClicks = 0;
    enterQuietScreenOffMode();
  }

  handlePendingBootButtonClicks(nowMs);
}

void refreshDisplayIfNeeded() {
  if (!displayReady) {
    return;
  }

  if (!displayBacklightEnabled) {
    return;
  }

  const int clientCount = static_cast<int>(WiFi.softAPgetStationNum());
  if (clientCount != lastDisplayClientCount) {
    lastDisplayClientCount = clientCount;
    displayDirty = true;
  }

  if (!displayDirty) {
    return;
  }

  drawActiveDisplayPage();
  presentDisplay();
  displayDirty = false;
}

void setupDisplay() {
  pinMode(config::kBootButtonPin, INPUT_PULLUP);
  bootButtonLastReading = readBootButtonRaw();
  bootButtonPressed = bootButtonLastReading;
  bootButtonLastTransitionMs = millis();
  ledcSetup(config::kBacklightPwmChannel, config::kBacklightPwmFrequency,
            config::kBacklightPwmResolution);
  ledcAttachPin(config::kLcdBacklightPin, config::kBacklightPwmChannel);
  applyBacklightLevel();

  displaySpi.begin(config::kLcdSclkPin, -1, config::kLcdMosiPin, config::kLcdCsPin);
  display = new WaveshareST7789(&displaySpi, config::kLcdCsPin, config::kLcdDcPin,
                                config::kLcdResetPin);
  display->init(config::kLcdWidth, config::kLcdHeight);
  display->setOffsets(config::kLcdColOffset, config::kLcdRowOffset, config::kLcdColOffset,
                      config::kLcdRowOffset);
  display->setRotation(0);
  display->fillScreen(colors::kBackground);
  displayCanvas = new GFXcanvas16(config::kLcdWidth, config::kLcdHeight);
  if (displayCanvas != nullptr && displayCanvas->getBuffer() != nullptr) {
    displaySurface = displayCanvas;
    displayCanvas->fillScreen(colors::kBackground);
    Serial.printf("Display back buffer enabled (%u bytes)\n",
                  static_cast<unsigned>(config::kLcdWidth * config::kLcdHeight *
                                        sizeof(uint16_t)));
  } else {
    if (displayCanvas != nullptr) {
      delete displayCanvas;
      displayCanvas = nullptr;
    }
    displaySurface = display;
    Serial.println("Display back buffer unavailable, using direct draw");
  }
  displayReady = true;
  applyBacklightLevel();
  setDisplayBanner("Booting media hub", "Preparing Wi-Fi access point");
}

String mimeTypeForPath(const String& path) {
  String lowered = lowercaseCopy(path);
  if (lowered.endsWith(".html")) return "text/html";
  if (lowered.endsWith(".css")) return "text/css";
  if (lowered.endsWith(".js")) return "application/javascript";
  if (lowered.endsWith(".json")) return "application/json";
  if (lowered.endsWith(".png")) return "image/png";
  if (lowered.endsWith(".jpg") || lowered.endsWith(".jpeg")) return "image/jpeg";
  if (lowered.endsWith(".webp")) return "image/webp";
  if (lowered.endsWith(".gif")) return "image/gif";
  if (lowered.endsWith(".svg")) return "image/svg+xml";
  if (lowered.endsWith(".mp4") || lowered.endsWith(".m4v")) return "video/mp4";
  if (lowered.endsWith(".mkv")) return "video/x-matroska";
  if (lowered.endsWith(".mov")) return "video/quicktime";
  if (lowered.endsWith(".webm")) return "video/webm";
  if (lowered.endsWith(".avi")) return "video/x-msvideo";
  if (lowered.endsWith(".mp3")) return "audio/mpeg";
  if (lowered.endsWith(".aac")) return "audio/aac";
  if (lowered.endsWith(".m4a") || lowered.endsWith(".m4b")) return "audio/mp4";
  if (lowered.endsWith(".flac")) return "audio/flac";
  if (lowered.endsWith(".ogg")) return "audio/ogg";
  if (lowered.endsWith(".wav")) return "audio/wav";
  if (lowered.endsWith(".pdf")) return "application/pdf";
  if (lowered.endsWith(".txt")) return "text/plain; charset=utf-8";
  if (lowered.endsWith(".md")) return "text/markdown; charset=utf-8";
  if (lowered.endsWith(".csv")) return "text/csv; charset=utf-8";
  if (lowered.endsWith(".gpx")) return "application/gpx+xml";
  if (lowered.endsWith(".kml")) return "application/vnd.google-earth.kml+xml";
  if (lowered.endsWith(".doc")) return "application/msword";
  if (lowered.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  return "application/octet-stream";
}

String classifyMediaType(const String& lowerPath) {
  if (lowerPath.endsWith(".mp4") || lowerPath.endsWith(".mkv") ||
      lowerPath.endsWith(".mov") || lowerPath.endsWith(".webm") ||
      lowerPath.endsWith(".m4v") || lowerPath.endsWith(".avi")) {
    return "video";
  }
  if (lowerPath.endsWith(".mp3") || lowerPath.endsWith(".m4a") ||
      lowerPath.endsWith(".m4b") ||
      lowerPath.endsWith(".aac") || lowerPath.endsWith(".wav") ||
      lowerPath.endsWith(".flac") || lowerPath.endsWith(".ogg")) {
    return "audio";
  }
  if (lowerPath.endsWith(".jpg") || lowerPath.endsWith(".jpeg") ||
      lowerPath.endsWith(".png") || lowerPath.endsWith(".gif") ||
      lowerPath.endsWith(".webp")) {
    return "image";
  }
  if (lowerPath.endsWith(".pdf") || lowerPath.endsWith(".txt") ||
      lowerPath.endsWith(".md") || lowerPath.endsWith(".csv") ||
      lowerPath.endsWith(".gpx") || lowerPath.endsWith(".kml") ||
      lowerPath.endsWith(".doc") || lowerPath.endsWith(".docx")) {
    return "document";
  }
  return "";
}

bool isMediaFile(const String& path) {
  return !classifyMediaType(path).isEmpty();
}

void scanDirectory(const String& folder) {
  const String normalizedFolder = normalizeSdPath(folder);
  File dir = openSdPath(normalizedFolder);
  if (!dir || !dir.isDirectory()) {
    return;
  }

  File entry = dir.openNextFile();
  while (entry && mediaLibrary.size() < config::kLibraryCapacity) {
    String path = String(entry.name());
    if (!path.startsWith("/")) {
      path = normalizedFolder + (normalizedFolder.endsWith("/") ? "" : "/") + path;
    }
    path = normalizeSdPath(path);
    if (path.isEmpty()) {
      path = normalizeSdPath(String(entry.path()));
    }
    if (isMetadataPath(path)) {
      entry = dir.openNextFile();
      continue;
    }

    if (entry.isDirectory()) {
      scanDirectory(path);
    } else {
      String lowered = path;
      lowered.toLowerCase();
      if (isMediaFile(lowered)) {
        MediaItem item;
        item.title = titleFromPath(path);
        item.sortTitle = item.title;
        item.path = path;
        item.type = classifyMediaType(lowered);
        int dot = path.lastIndexOf('.');
        item.extension = dot > 0 ? path.substring(dot + 1) : "";
        item.extension.toUpperCase();
        item.bytes = entry.size();
        decorateMediaItem(item);
        applyItemMetadata(item);
        mediaLibrary.push_back(item);
      }
    }
    entry = dir.openNextFile();
  }

  dir.close();
}

void rebuildLibrary() {
  mediaLibrary.clear();
  loadMetadataLibrary();
  if (!sdMounted) {
    markDisplayDirty();
    return;
  }
  scanDirectory(config::kMediaRoot);
  sortLibrary();
  markDisplayDirty();
}

bool configureSdPins() {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  return SD_MMC.setPins(config::kSdClkPin, config::kSdCmdPin, config::kSdD0Pin,
                        config::kSdD1Pin, config::kSdD2Pin, config::kSdD3Pin);
#else
  return true;
#endif
}

bool ensureSdMounted() {
  if (sdMounted) {
    return true;
  }

  if (!configureSdPins()) {
    Serial.println("SD_MMC pin assignment failed");
    sdMounted = false;
    setDisplayBanner("SD setup failed", "Pin assignment was rejected");
    applyStatusLed();
    return false;
  }

  if (!SD_MMC.begin("/sdcard", config::kSdOneBitMode, false, SDMMC_FREQ_DEFAULT)) {
    Serial.println("SD_MMC mount failed");
    sdMounted = false;
    setDisplayBanner("Insert SD card", "Mount failed for TF storage");
    applyStatusLed();
    return false;
  }

  if (SD_MMC.cardType() == CARD_NONE) {
    Serial.println("No SD card detected");
    SD_MMC.end();
    sdMounted = false;
    setDisplayBanner("Insert SD card", "No TF card detected");
    applyStatusLed();
    return false;
  }

  if (!SD_MMC.exists(config::kMediaRoot)) {
    SD_MMC.mkdir(config::kMediaRoot);
  }

  sdMounted = true;
  setDisplayBanner("Storage ready", "Scanning /media on TF card");
  applyStatusLed();
  rebuildLibrary();
  return true;
}

void serveStaticFile(const char* path) {
  File file = LittleFS.open(path, "r");
  if (!file) {
    server.send(404, "text/plain", "Missing asset");
    return;
  }

  server.sendHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  server.sendHeader("Pragma", "no-cache");
  server.sendHeader("Expires", "0");
  server.streamFile(file, mimeTypeForPath(String(path)));
  file.close();
}

void redirectToPath(const String& path) {
  server.sendHeader("Location", path, true);
  server.send(302, "text/plain", "");
}

void handleAppIndex() {
  serveStaticFile("/index.html");
}

void handleRoot() {
  redirectToPath(config::kAppPath);
}

void handleStatus() {
  JsonDocument doc(&jsonAllocator);
  doc["device"] = configuredDeviceName();
  doc["ssid"] = configuredSsid();
  doc["password"] = configuredPassword();
  doc["ip"] = WiFi.softAPIP().toString();
  doc["appUrl"] = preferredAppUrl();
  doc["ipAppUrl"] = ipAppUrl();
  doc["mdnsHost"] = mdnsHostName();
  doc["mdnsUrl"] = mdnsAppUrl();
  doc["mdnsReady"] = mdnsReady;
  doc["streamPort"] = config::kMediaStreamPort;
  doc["streamBaseUrl"] = mediaStreamBaseUrl();
  doc["activeStreams"] = static_cast<int>(activeMediaStreamTasks);
  doc["maxStreams"] = config::kMediaStreamMaxTasks;
  doc["maxClients"] = config::kSoftApMaxConnections;
  doc["configSource"] = runtimeConfigSource;
  doc["sdMounted"] = ensureSdMounted();
  doc["libraryCount"] = mediaLibrary.size();
  doc["mediaRoot"] = config::kMediaRoot;
  doc["clients"] = WiFi.softAPgetStationNum();
  doc["lastPlayed"] = lastPlaybackTitle;
  doc["lastPlayedType"] = lastPlaybackType;
  doc["metadataAvailable"] = metadataAvailable;
  doc["metadataGeneratedAt"] = metadataGeneratedAt;
  doc["metadataGenerator"] = metadataGenerator;
  doc["metadataItemCount"] = itemMetadataLibrary.size();
  doc["metadataShowCount"] = showMetadataLibrary.size();
  sendJsonDocument(200, doc);
}

size_t countSection(const String& section) {
  size_t count = 0;
  for (const MediaItem& item : mediaLibrary) {
    if (item.section == section) {
      ++count;
    }
  }
  return count;
}

ShowView* findShow(std::vector<ShowView>& shows, const String& slug) {
  for (ShowView& show : shows) {
    if (show.slug == slug) {
      return &show;
    }
  }
  return nullptr;
}

SeasonView* findSeason(ShowView& show, const String& label) {
  for (SeasonView& season : show.seasons) {
    if (season.label == label) {
      return &season;
    }
  }
  return nullptr;
}

std::vector<ShowView> buildShowLibrary() {
  std::vector<ShowView> shows;

  for (const MediaItem& item : mediaLibrary) {
    if (item.section != "tv") {
      continue;
    }

    ShowView* show = findShow(shows, item.showSlug);
    if (show == nullptr) {
      ShowView created;
      created.title = item.showTitle;
      created.slug = item.showSlug;
      shows.push_back(created);
      show = &shows.back();
    }

    if (show->year.isEmpty()) {
      show->year = item.year;
    }
    if (show->overview.isEmpty()) {
      show->overview = item.overview;
    }
    if (show->genres.isEmpty()) {
      show->genres = item.genres;
    }
    if (show->contentRating.isEmpty()) {
      show->contentRating = item.contentRating;
    }
    if (show->posterPath.isEmpty()) {
      show->posterPath = item.posterPath;
    }
    if (show->backdropPath.isEmpty()) {
      show->backdropPath = item.backdropPath;
    }
    if (show->metadataSource.isEmpty()) {
      show->metadataSource = item.metadataSource;
    }
    if (show->tmdbRating <= 0 && item.tmdbRating > 0) {
      show->tmdbRating = item.tmdbRating;
    }
    if (show->matchConfidence <= 0 && item.matchConfidence > 0) {
      show->matchConfidence = item.matchConfidence;
    }

    SeasonView* season = findSeason(*show, item.seasonLabel);
    if (season == nullptr) {
      SeasonView created;
      created.label = item.seasonLabel;
      created.number = item.seasonNumber;
      show->seasons.push_back(created);
      season = &show->seasons.back();
    }

    season->episodes.push_back(&item);
  }

  for (ShowView& show : shows) {
    const ShowMetadata* metadata = findShowMetadata(show.slug);
    if (metadata != nullptr) {
      mergeStringField(show.title, metadata->title);
      mergeStringField(show.year, metadata->year);
      mergeStringField(show.overview, metadata->overview);
      mergeStringField(show.genres, metadata->genres);
      mergeStringField(show.contentRating, metadata->contentRating);
      mergeStringField(show.posterPath, metadata->posterPath);
      mergeStringField(show.backdropPath, metadata->backdropPath);
      mergeStringField(show.metadataSource, metadata->metadataSource);
      if (metadata->tmdbRating > 0) {
        show.tmdbRating = metadata->tmdbRating;
      }
      if (metadata->matchConfidence > 0) {
        show.matchConfidence = metadata->matchConfidence;
      }
    }
  }

  std::sort(shows.begin(), shows.end(), [](const ShowView& left, const ShowView& right) {
    return lowercaseCopy(left.title) < lowercaseCopy(right.title);
  });

  for (ShowView& show : shows) {
    std::sort(show.seasons.begin(), show.seasons.end(),
              [](const SeasonView& left, const SeasonView& right) {
                if (left.number != right.number) {
                  return left.number < right.number;
                }
                return lowercaseCopy(left.label) < lowercaseCopy(right.label);
              });

    for (SeasonView& season : show.seasons) {
      std::sort(season.episodes.begin(), season.episodes.end(),
                [](const MediaItem* left, const MediaItem* right) {
                  if (left->episodeNumber != right->episodeNumber &&
                      left->episodeNumber != 0 && right->episodeNumber != 0) {
                    return left->episodeNumber < right->episodeNumber;
                  }
                  return lowercaseCopy(left->title) < lowercaseCopy(right->title);
                });
    }
  }

  return shows;
}

void serializeMediaItem(JsonObject out, const MediaItem& item) {
  out["title"] = item.title;
  out["path"] = item.path;
  out["type"] = item.type;
  out["section"] = item.section;
  out["extension"] = item.extension;
  out["bytes"] = item.bytes;
  out["streamUrl"] = streamUrlForPath(item.path);
  out["posterPath"] = item.posterPath;
  out["backdropPath"] = item.backdropPath;
  out["posterUrl"] = assetUrlForPath(item.posterPath);
  out["backdropUrl"] = assetUrlForPath(item.backdropPath);
  out["sortTitle"] = item.sortTitle;
  out["overview"] = item.overview;
  out["tagline"] = item.tagline;
  out["year"] = item.year;
  out["releaseDate"] = item.releaseDate;
  out["genres"] = item.genres;
  out["contentRating"] = item.contentRating;
  out["artist"] = item.artist;
  out["album"] = item.album;
  out["tmdbRating"] = item.tmdbRating;
  out["runtimeMinutes"] = item.runtimeMinutes;
  out["hasMetadata"] = item.hasMetadata;
  out["metadataSource"] = item.metadataSource;
  out["matchConfidence"] = item.matchConfidence;

  if (item.section == "tv") {
    out["showTitle"] = item.showTitle;
    out["showSlug"] = item.showSlug;
    out["seasonLabel"] = item.seasonLabel;
    out["seasonNumber"] = item.seasonNumber;
    out["episodeNumber"] = item.episodeNumber;
  }
}

void handleLibrary() {
  if (!ensureSdMounted()) {
    server.send(503, "application/json", "{\"error\":\"SD card unavailable\"}");
    return;
  }

  JsonDocument doc(&jsonAllocator);
  responseOverflowError = false;
  const std::vector<ShowView> shows = buildShowLibrary();
  size_t itemArtCount = 0;
  for (const MediaItem& item : mediaLibrary) {
    if (!item.posterPath.isEmpty() || !item.backdropPath.isEmpty()) {
      ++itemArtCount;
    }
  }

  size_t showArtCount = 0;
  for (const ShowView& show : shows) {
    if (!show.posterPath.isEmpty() || !show.backdropPath.isEmpty()) {
      ++showArtCount;
    }
  }

  JsonObject counts = doc["counts"].to<JsonObject>();
  doc["count"] = mediaLibrary.size();
  counts["total"] = mediaLibrary.size();
  counts["movies"] = countSection("movies");
  counts["shows"] = shows.size();
  counts["episodes"] = countSection("tv");
  counts["music"] = countSection("music");
  counts["audiobooks"] = countSection("audiobooks");
  counts["documents"] = countSection("documents");

  JsonObject metadata = doc["metadata"].to<JsonObject>();
  metadata["available"] = metadataAvailable;
  metadata["generatedAt"] = metadataGeneratedAt;
  metadata["generator"] = metadataGenerator;
  metadata["itemCount"] = itemMetadataLibrary.size();
  metadata["showCount"] = showMetadataLibrary.size();

  JsonObject sections = doc["sections"].to<JsonObject>();
  JsonArray movies = sections["movies"].to<JsonArray>();
  JsonArray music = sections["music"].to<JsonArray>();
  JsonArray audiobooks = sections["audiobooks"].to<JsonArray>();
  JsonArray documents = sections["documents"].to<JsonArray>();

  for (const MediaItem& item : mediaLibrary) {
    if (item.section == "movies") {
      JsonObject out = movies.add<JsonObject>();
      serializeMediaItem(out, item);
    } else if (item.section == "music") {
      JsonObject out = music.add<JsonObject>();
      serializeMediaItem(out, item);
    } else if (item.section == "audiobooks") {
      JsonObject out = audiobooks.add<JsonObject>();
      serializeMediaItem(out, item);
    } else if (item.section == "documents") {
      JsonObject out = documents.add<JsonObject>();
      serializeMediaItem(out, item);
    }
  }

  JsonArray showArray = sections["tv"].to<JsonArray>();
  for (const ShowView& show : shows) {
    JsonObject outShow = showArray.add<JsonObject>();
    outShow["title"] = show.title;
    outShow["slug"] = show.slug;
    outShow["posterPath"] = show.posterPath;
    outShow["backdropPath"] = show.backdropPath;
    outShow["posterUrl"] = assetUrlForPath(show.posterPath);
    outShow["backdropUrl"] = assetUrlForPath(show.backdropPath);
    outShow["year"] = show.year;
    outShow["overview"] = show.overview;
    outShow["genres"] = show.genres;
    outShow["contentRating"] = show.contentRating;
    outShow["metadataSource"] = show.metadataSource;
    outShow["tmdbRating"] = show.tmdbRating;
    outShow["matchConfidence"] = show.matchConfidence;
    outShow["detailUrl"] = String(config::kAppPath) + "/tv/" + show.slug;
    outShow["seasonCount"] = show.seasons.size();

    size_t episodeCount = 0;
    for (const SeasonView& season : show.seasons) {
      episodeCount += season.episodes.size();
    }
    outShow["episodeCount"] = episodeCount;

    JsonArray seasons = outShow["seasons"].to<JsonArray>();
    for (const SeasonView& season : show.seasons) {
      JsonObject outSeason = seasons.add<JsonObject>();
      outSeason["label"] = season.label;
      outSeason["number"] = season.number;
      outSeason["episodeCount"] = season.episodes.size();

      JsonArray episodes = outSeason["episodes"].to<JsonArray>();
      for (const MediaItem* episode : season.episodes) {
        JsonObject outEpisode = episodes.add<JsonObject>();
        serializeMediaItem(outEpisode, *episode);
      }
    }
  }

  if (doc.overflowed()) {
    responseOverflowError = true;
    Serial.printf("Library response overflowed: %u items (%u with art), %u shows (%u with art)\n",
                  static_cast<unsigned>(mediaLibrary.size()),
                  static_cast<unsigned>(itemArtCount),
                  static_cast<unsigned>(shows.size()),
                  static_cast<unsigned>(showArtCount));
    applyStatusLed();
  }

  sendJsonDocument(200, doc);
}

bool parseByteRange(const String& rangeHeader, size_t fileSize, size_t& start, size_t& end) {
  if (!rangeHeader.startsWith("bytes=") || fileSize == 0) {
    return false;
  }

  String rangeSpec = rangeHeader.substring(6);
  const int comma = rangeSpec.indexOf(',');
  if (comma >= 0) {
    rangeSpec = rangeSpec.substring(0, comma);
  }

  const int dash = rangeSpec.indexOf('-');
  if (dash < 0) {
    return false;
  }

  const String startStr = rangeSpec.substring(0, dash);
  const String endStr = rangeSpec.substring(dash + 1);
  if (startStr.isEmpty() && endStr.isEmpty()) {
    return false;
  }

  if (startStr.isEmpty()) {
    const size_t suffixLength = static_cast<size_t>(endStr.toInt());
    if (suffixLength == 0) {
      return false;
    }

    if (suffixLength >= fileSize) {
      start = 0;
    } else {
      start = fileSize - suffixLength;
    }
    end = fileSize - 1;
    return true;
  }

  start = static_cast<size_t>(startStr.toInt());
  if (start >= fileSize) {
    return false;
  }

  if (endStr.isEmpty()) {
    end = fileSize - 1;
    return true;
  }

  end = static_cast<size_t>(endStr.toInt());
  if (end < start) {
    return false;
  }
  if (end >= fileSize) {
    end = fileSize - 1;
  }
  return true;
}

String cacheControlForFileRequest(bool trackPlayback) {
  if (trackPlayback) {
    return "no-store";
  }
  return "public, max-age=31536000, immutable";
}

void sendStreamHeaders(int statusCode, const String& contentType, size_t contentLength,
                       size_t fileSize, size_t start, size_t end,
                       const String& cacheControl) {
  server.sendHeader("Accept-Ranges", "bytes");
  server.sendHeader("Cache-Control", cacheControl);
  if (statusCode == 206) {
    server.sendHeader("Content-Range",
                      "bytes " + String(start) + "-" + String(end) + "/" +
                          String(fileSize));
  }
  server.setContentLength(contentLength);
  server.send(statusCode, contentType.c_str(), "");
}

bool writeChunkWithRetry(WiFiClient& client, const uint8_t* buffer, size_t length) {
  size_t writtenTotal = 0;
  uint8_t stalledWrites = 0;

  while (writtenTotal < length && client.connected()) {
    const size_t written = client.write(buffer + writtenTotal, length - writtenTotal);
    if (written > 0) {
      writtenTotal += written;
      stalledWrites = 0;
      delay(0);
      continue;
    }

    ++stalledWrites;
    if (stalledWrites >= config::kMediaWriteStallRetries) {
      return false;
    }
    delay(config::kMediaWriteStallDelayMs);
  }

  return writtenTotal == length;
}

void writeFileRange(File& file, size_t start, size_t end) {
  WiFiClient client = server.client();
  client.setNoDelay(true);
  client.setTimeout(config::kMediaStreamSocketTimeoutMs);
  if (!lockSdIo()) {
    return;
  }
  file.seek(start);
  unlockSdIo();
  size_t remaining = end - start + 1;

  while (remaining > 0 && client.connected()) {
    size_t toRead = remaining > sizeof(streamTransferBuffer) ? sizeof(streamTransferBuffer) : remaining;
    if (!lockSdIo()) {
      break;
    }
    size_t readBytes = file.read(streamTransferBuffer, toRead);
    unlockSdIo();
    if (readBytes == 0) {
      break;
    }

    if (!writeChunkWithRetry(client, streamTransferBuffer, readBytes)) {
      Serial.println("Stream write stalled before chunk completed");
      break;
    }

    remaining -= readBytes;
    vTaskDelay(config::kMediaStreamYieldTicks);
  }
}

const char* httpReasonPhrase(int statusCode) {
  switch (statusCode) {
    case 200:
      return "OK";
    case 204:
      return "No Content";
    case 400:
      return "Bad Request";
    case 404:
      return "Not Found";
    case 405:
      return "Method Not Allowed";
    case 416:
      return "Range Not Satisfiable";
    case 500:
      return "Internal Server Error";
    case 503:
      return "Service Unavailable";
    default:
      return "OK";
  }
}

void writeMediaCorsHeaders(WiFiClient& client) {
  client.print("Access-Control-Allow-Origin: *\r\n");
  client.print("Access-Control-Allow-Methods: GET, HEAD, OPTIONS\r\n");
  client.print(
      "Access-Control-Expose-Headers: Accept-Ranges, Content-Length, Content-Range, Content-Type, Cache-Control\r\n");
}

void writeMediaTextResponse(WiFiClient& client, int statusCode, const String& body,
                            const String& contentType = "text/plain") {
  client.printf("HTTP/1.1 %d %s\r\n", statusCode, httpReasonPhrase(statusCode));
  client.print("Content-Type: ");
  client.print(contentType);
  client.print("\r\n");
  client.print("Content-Length: ");
  client.print(body.length());
  client.print("\r\n");
  client.print("Cache-Control: no-store\r\n");
  writeMediaCorsHeaders(client);
  client.print("Connection: close\r\n\r\n");
  client.print(body);
}

void writeMediaStreamHeaders(WiFiClient& client, int statusCode, const String& contentType,
                             size_t contentLength, size_t fileSize, size_t start, size_t end,
                             const String& cacheControl) {
  client.printf("HTTP/1.1 %d %s\r\n", statusCode, httpReasonPhrase(statusCode));
  client.print("Content-Type: ");
  client.print(contentType);
  client.print("\r\n");
  client.print("Content-Length: ");
  client.print(String(contentLength));
  client.print("\r\n");
  client.print("Accept-Ranges: bytes\r\n");
  client.print("Cache-Control: ");
  client.print(cacheControl);
  client.print("\r\n");
  if (statusCode == 206) {
    client.print("Content-Range: bytes ");
    client.print(String(start));
    client.print("-");
    client.print(String(end));
    client.print("/");
    client.print(String(fileSize));
    client.print("\r\n");
  }
  writeMediaCorsHeaders(client);
  client.print("Connection: close\r\n\r\n");
}

void writeFileRangeToClient(File& file, WiFiClient& client, size_t start, size_t end) {
  // Keep the worker task stack small so we can sustain more concurrent viewers.
  uint8_t stackBuffer[512];
  uint8_t* buffer = allocateMediaStreamBuffer(config::kMediaStreamTaskChunkSize);
  size_t bufferSize = config::kMediaStreamTaskChunkSize;
  const bool usingHeapBuffer = buffer != nullptr;
  if (!usingHeapBuffer) {
    buffer = stackBuffer;
    bufferSize = sizeof(stackBuffer);
  }

  client.setTimeout(config::kMediaStreamSocketTimeoutMs);

  if (!lockSdIo()) {
    if (usingHeapBuffer) {
      heap_caps_free(buffer);
    }
    return;
  }
  file.seek(start);
  unlockSdIo();
  size_t remaining = end - start + 1;

  while (remaining > 0 && client.connected()) {
    const size_t toRead = remaining > bufferSize ? bufferSize : remaining;
    if (!lockSdIo()) {
      break;
    }
    const size_t readBytes = file.read(buffer, toRead);
    unlockSdIo();
    if (readBytes == 0) {
      break;
    }

    if (!writeChunkWithRetry(client, buffer, readBytes)) {
      Serial.println("Media stream write stalled before chunk completed");
      break;
    }

    remaining -= readBytes;
    vTaskDelay(config::kMediaStreamYieldTicks);
  }

  if (usingHeapBuffer) {
    heap_caps_free(buffer);
  }
}

bool reserveMediaStreamTaskSlot() {
  bool reserved = false;
  portENTER_CRITICAL(&mediaStreamTaskMux);
  if (activeMediaStreamTasks < config::kMediaStreamMaxTasks) {
    ++activeMediaStreamTasks;
    reserved = true;
  }
  portEXIT_CRITICAL(&mediaStreamTaskMux);
  return reserved;
}

void releaseMediaStreamTaskSlot() {
  portENTER_CRITICAL(&mediaStreamTaskMux);
  if (activeMediaStreamTasks > 0) {
    --activeMediaStreamTasks;
  }
  portEXIT_CRITICAL(&mediaStreamTaskMux);
}

String queryParamValue(const String& uri, const String& key) {
  const int queryIndex = uri.indexOf('?');
  if (queryIndex < 0 || queryIndex >= static_cast<int>(uri.length() - 1)) {
    return "";
  }

  int cursor = queryIndex + 1;
  while (cursor <= uri.length()) {
    int amp = uri.indexOf('&', cursor);
    if (amp < 0) {
      amp = uri.length();
    }

    const String pair = uri.substring(cursor, amp);
    const int equals = pair.indexOf('=');
    const String candidateKey = equals >= 0 ? pair.substring(0, equals) : pair;
    if (candidateKey == key) {
      return equals >= 0 ? pair.substring(equals + 1) : "";
    }

    cursor = amp + 1;
  }

  return "";
}

bool readMediaRequest(WiFiClient& client, String& method, String& uri, String& rangeHeader) {
  client.setTimeout(config::kMediaRequestHeaderTimeoutMs);
  String requestLine = client.readStringUntil('\n');
  requestLine.trim();
  if (requestLine.isEmpty()) {
    return false;
  }

  const int firstSpace = requestLine.indexOf(' ');
  const int secondSpace = requestLine.indexOf(' ', firstSpace + 1);
  if (firstSpace <= 0 || secondSpace <= firstSpace) {
    return false;
  }

  method = requestLine.substring(0, firstSpace);
  uri = requestLine.substring(firstSpace + 1, secondSpace);

  while (client.connected()) {
    String headerLine = client.readStringUntil('\n');
    headerLine.trim();
    if (headerLine.isEmpty()) {
      break;
    }

    const int colon = headerLine.indexOf(':');
    if (colon <= 0) {
      continue;
    }

    String headerName = headerLine.substring(0, colon);
    headerName.toLowerCase();
    String headerValue = headerLine.substring(colon + 1);
    headerValue.trim();
    if (headerName == "range") {
      rangeHeader = headerValue;
    }
  }

  return true;
}

void serviceMediaStreamClient(WiFiClient& client) {
  String method;
  String uri;
  String rangeHeader;
  if (!readMediaRequest(client, method, uri, rangeHeader)) {
    writeMediaTextResponse(client, 400, "Malformed request");
    return;
  }

  if (method == "OPTIONS") {
    client.printf("HTTP/1.1 204 %s\r\n", httpReasonPhrase(204));
    writeMediaCorsHeaders(client);
    client.print("Connection: close\r\n\r\n");
    return;
  }

  const bool headersOnly = method == "HEAD";
  if (method != "GET" && !headersOnly) {
    writeMediaTextResponse(client, 405, "Only GET and HEAD are supported");
    return;
  }

  const int pathEnd = uri.indexOf('?');
  const String routePath = pathEnd >= 0 ? uri.substring(0, pathEnd) : uri;
  if (routePath != "/api/stream") {
    writeMediaTextResponse(client, 404, "Stream endpoint not found");
    return;
  }

  if (!ensureSdMounted()) {
    writeMediaTextResponse(client, 503, "SD card unavailable");
    return;
  }

  String path = urlDecode(queryParamValue(uri, "path"));
  path = normalizeSdPath(path);
  if (!path.startsWith(config::kMediaRoot) || !sdPathExists(path)) {
    writeMediaTextResponse(client, 404, "Media file not found");
    return;
  }

  File file = openSdPath(path, "r");
  if (!file || file.isDirectory()) {
    writeMediaTextResponse(client, 404, "Media file not found");
    return;
  }

  if (!headersOnly) {
    updatePlaybackStateForPath(path);
  }

  const size_t fileSize = file.size();
  const String contentType = mimeTypeForPath(path);
  const String cacheControl = cacheControlForFileRequest(true);
  if (!rangeHeader.isEmpty()) {
    size_t start = 0;
    size_t end = fileSize > 0 ? fileSize - 1 : 0;
    if (!parseByteRange(rangeHeader, fileSize, start, end)) {
      client.printf("HTTP/1.1 416 %s\r\n", httpReasonPhrase(416));
      client.print("Content-Range: bytes */");
      client.print(String(fileSize));
      client.print("\r\n");
      client.print("Cache-Control: no-store\r\n");
      writeMediaCorsHeaders(client);
      client.print("Connection: close\r\n\r\n");
      file.close();
      return;
    }

    writeMediaStreamHeaders(client, 206, contentType, end - start + 1, fileSize, start, end,
                            cacheControl);
    if (!headersOnly) {
      writeFileRangeToClient(file, client, start, end);
    }
    file.close();
    return;
  }

  writeMediaStreamHeaders(client, 200, contentType, fileSize, fileSize, 0, 0, cacheControl);
  if (!headersOnly && fileSize > 0) {
    writeFileRangeToClient(file, client, 0, fileSize - 1);
  }
  file.close();
}

struct MediaStreamTaskContext {
  explicit MediaStreamTaskContext(const WiFiClient& acceptedClient) : client(acceptedClient) {}
  WiFiClient client;
};

void mediaStreamTask(void* parameter) {
  MediaStreamTaskContext* context = static_cast<MediaStreamTaskContext*>(parameter);
  WiFiClient client = context->client;
  delete context;

  client.setNoDelay(true);
  serviceMediaStreamClient(client);
  client.stop();
  releaseMediaStreamTaskSlot();
  vTaskDelete(nullptr);
}

void acceptMediaStreamClients() {
  while (true) {
    WiFiClient client = mediaStreamServer.available();
    if (!client) {
      return;
    }

    client.setNoDelay(true);
    if (!reserveMediaStreamTaskSlot()) {
      writeMediaTextResponse(client, 503, "Media stream server is busy");
      client.stop();
      continue;
    }

    MediaStreamTaskContext* context = new MediaStreamTaskContext(client);
    const BaseType_t started = xTaskCreatePinnedToCore(
        mediaStreamTask, "media_stream", config::kMediaStreamTaskStackWords, context,
        config::kMediaStreamTaskPriority, nullptr, config::kMediaStreamTaskCore);
    if (started != pdPASS) {
      delete context;
      releaseMediaStreamTaskSlot();
      writeMediaTextResponse(client, 503, "Unable to start media stream task");
      client.stop();
    }
  }
}

void updatePlaybackStateForPath(const String& path) {
  String loweredPath = path;
  loweredPath.toLowerCase();
  lastPlaybackTitle = titleFromPath(path);
  lastPlaybackType = classifyMediaType(loweredPath);
  lastPlaybackAtMs = millis();
  markDisplayDirty();
}

void handleFileRequest(bool headersOnly, bool trackPlayback) {
  if (!ensureSdMounted()) {
    server.send(503, "text/plain", "SD card unavailable");
    return;
  }

  String path = urlDecode(server.arg("path"));
  path = normalizeSdPath(path);
  if (!path.startsWith(config::kMediaRoot) || !sdPathExists(path)) {
    Serial.println(String("Requested SD path not found: ") + path);
    server.send(404, "text/plain", "Media file not found");
    return;
  }

  File file = openSdPath(path, "r");
  if (!file || file.isDirectory()) {
    Serial.println(String("Requested SD path could not be opened: ") + path);
    server.send(404, "text/plain", "Media file not found");
    return;
  }

  if (trackPlayback) {
    updatePlaybackStateForPath(path);
  }

  size_t fileSize = file.size();
  String contentType = mimeTypeForPath(path);
  String range = server.header("Range");
  const String cacheControl = cacheControlForFileRequest(trackPlayback);

  if (!range.isEmpty()) {
    size_t start = 0;
    size_t end = fileSize > 0 ? fileSize - 1 : 0;

    if (!parseByteRange(range, fileSize, start, end)) {
      server.sendHeader("Content-Range", "bytes */" + String(fileSize));
      server.send(416, "text/plain", "Requested range not satisfiable");
      file.close();
      return;
    }

    if (headersOnly) {
      sendStreamHeaders(206, contentType, end - start + 1, fileSize, start, end, cacheControl);
    } else {
      sendStreamHeaders(206, contentType, end - start + 1, fileSize, start, end, cacheControl);
      writeFileRange(file, start, end);
    }
    file.close();
    return;
  }

  server.sendHeader("Accept-Ranges", "bytes");
  server.sendHeader("Cache-Control", cacheControl);
  if (headersOnly) {
    server.setContentLength(fileSize);
    server.send(200, contentType.c_str(), "");
  } else {
    sendStreamHeaders(200, contentType, fileSize, fileSize, 0, 0, cacheControl);
    if (fileSize > 0) {
      writeFileRange(file, 0, fileSize - 1);
    }
  }
  file.close();
}

void handleStream() {
  handleFileRequest(false, true);
}

void handleStreamHead() {
  handleFileRequest(true, true);
}

void handleAsset() {
  handleFileRequest(false, false);
}

void handleAssetHead() {
  handleFileRequest(true, false);
}

void handleRescan() {
  ensureSdMounted();
  setDisplayBanner("Refreshing library", "Scanning storage again");
  rebuildLibrary();
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleNotFound() {
  String uri = server.uri();
  if (uri == config::kAppPath || uri.startsWith(String(config::kAppPath) + "/")) {
    handleAppIndex();
    return;
  }

  if (LittleFS.exists(uri)) {
    serveStaticFile(uri.c_str());
    return;
  }

  if (uri.startsWith("/api/")) {
    server.send(404, "application/json", "{\"error\":\"Not found\"}");
    return;
  }

  redirectToPath(config::kAppPath);
}

void setupAccessPoint() {
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  const String ssid = configuredSsid();
  const String password = configuredPassword();
  const bool started = password.isEmpty()
                           ? WiFi.softAP(ssid.c_str(), nullptr, config::kSoftApChannel, 0,
                                         config::kSoftApMaxConnections)
                           : WiFi.softAP(ssid.c_str(), password.c_str(), config::kSoftApChannel,
                                         0, config::kSoftApMaxConnections);
  softApError = !started;
  if (!started) {
    Serial.println("SoftAP start failed");
    applyStatusLed();
  }

  Serial.print("AP ready at http://");
  Serial.println(WiFi.softAPIP());
  Serial.printf("AP tuning: channel %u, max clients %u, modem sleep disabled\n",
                static_cast<unsigned>(config::kSoftApChannel),
                static_cast<unsigned>(config::kSoftApMaxConnections));
  mediaStreamServer.begin();
  mediaStreamServer.setNoDelay(true);
  Serial.printf("Media stream server ready at %s\n", mediaStreamBaseUrl().c_str());
  setDisplayBanner("Access point ready", "Open " + preferredAppUrl());
}

void setupDiscovery() {
  if (MDNS.begin(runtimeMdnsHost.c_str())) {
    MDNS.addService("http", "tcp", 80);
    mdnsReady = true;
    mdnsError = false;
    Serial.print("mDNS ready at http://");
    Serial.println(mdnsHostName());
  } else {
    mdnsReady = false;
    mdnsError = true;
    Serial.println("mDNS startup failed");
    applyStatusLed();
  }
  markDisplayDirty();
}

void setupFileSystems() {
  if (sdIoMutex == nullptr) {
    sdIoMutex = xSemaphoreCreateMutex();
    if (sdIoMutex == nullptr) {
      Serial.println("SD I/O mutex allocation failed");
    }
  }

  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS mount failed");
    littleFsError = true;
    applyStatusLed();
  } else {
    littleFsError = false;
  }
  ensureSdMounted();
}

void setupRoutes() {
  server.collectHeaders(kCollectedRequestHeaders,
                        sizeof(kCollectedRequestHeaders) / sizeof(kCollectedRequestHeaders[0]));
  server.on("/", HTTP_GET, handleRoot);
  server.on("/app", HTTP_GET, handleAppIndex);
  server.on("/app/", HTTP_GET, handleAppIndex);
  server.on("/index.html", HTTP_GET, handleAppIndex);
  server.on("/styles.css", HTTP_GET, []() { serveStaticFile("/styles.css"); });
  server.on("/app.js", HTTP_GET, []() { serveStaticFile("/app.js"); });
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/library", HTTP_GET, handleLibrary);
  server.on("/api/stream", HTTP_GET, handleStream);
  server.on("/api/stream", HTTP_HEAD, handleStreamHead);
  server.on("/api/asset", HTTP_GET, handleAsset);
  server.on("/api/asset", HTTP_HEAD, handleAssetHead);
  server.on("/api/rescan", HTTP_POST, handleRescan);
  server.onNotFound(handleNotFound);
  server.begin();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  setupDisplay();
  setupFileSystems();
  loadRuntimeConfigFromSd();
  setupAccessPoint();
  setupDiscovery();
  setupRoutes();
  setDisplayBanner("Ready to stream", "Open " + preferredAppUrl());
}

void loop() {
  server.handleClient();
  acceptMediaStreamClients();
  updateBootButtonControls();
  refreshDisplayIfNeeded();
}
