# Backpacking Media Server

A Raspberry Pi Zero W portable media server that keeps the existing Nomad Screen web UI, media-library layout, and metadata format, while replacing the old ESP32 firmware with a Pi-native Python service.

## What changed

- The project now runs on Raspberry Pi Zero W with Python instead of PlatformIO/ESP32 firmware.
- The web app in `data/` is still the main user interface.
- The backend now serves the same core routes from Linux:
  - `/app`
  - `/api/status`
  - `/api/library`
  - `/api/stream`
  - `/api/asset`
  - `/api/rescan`
- The storage layout from `sdcard-template/` still applies, so existing metadata tooling and `library.json` files stay compatible.

## Project layout

- `src/main.py`: Pi-native HTTP server, media scan logic, metadata merge, and streaming endpoints
- `data/`: static web app shell, styles, and client-side browsing logic
- `sdcard-template/`: copy-ready storage layout with `/media`, metadata tooling, and sample config
- `deploy/nomadscreen.service`: example `systemd` unit
- `deploy/hostapd/hostapd.conf.example`: optional Pi hotspot template
- `deploy/dnsmasq/nomadscreen.conf.example`: optional DHCP/captive-network helper template

## Storage layout

The server expects a storage root that contains:

- `nomadscreen.config.json`
- `media/`
- `media/.nomadscreen/library.json` when metadata has been generated

By default, the repo uses `sdcard-template/` as the storage root so the project can run immediately in-place. On the Pi, point `NOMADSCREEN_STORAGE_ROOT` at your real storage path, for example `/srv/nomadscreen` or a mounted USB/SD volume.

## Local run

1. Create and activate a virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the server:

   ```bash
   python src/main.py
   ```

4. If port `80` is already in use or you do not want admin privileges during local testing, override the port:

   ```bash
   NOMADSCREEN_PORT=8080 python src/main.py
   ```

5. Open `http://<device-or-pi-address>/app`.

## Raspberry Pi Zero W setup

1. Copy the repo to the Pi, for example `/opt/nomadscreen`.
2. Copy the contents of `sdcard-template/` to your runtime storage root, for example `/srv/nomadscreen`.
3. Install Python 3 and the project dependency:

   ```bash
   python3 -m pip install -r /opt/nomadscreen/requirements.txt
   ```

4. Start the server manually once to confirm the library loads:

   ```bash
   NOMADSCREEN_STORAGE_ROOT=/srv/nomadscreen python3 /opt/nomadscreen/src/main.py
   ```

5. Install the example service if you want it to start on boot:

   ```bash
   sudo cp /opt/nomadscreen/deploy/nomadscreen.service /etc/systemd/system/nomadscreen.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now nomadscreen.service
   ```

## Optional hotspot mode

The ESP32 used to create its own access point directly. On Raspberry Pi Zero W, that responsibility moves to Raspberry Pi OS networking.

- Use `deploy/hostapd/hostapd.conf.example` as the starting point for `hostapd`
- Use `deploy/dnsmasq/nomadscreen.conf.example` as the starting point for `dnsmasq`
- Keep `deviceName` and `wifiPassword` in `nomadscreen.config.json` aligned with your Pi hotspot settings so the UI still reports the same network name and password

## Runtime config

`nomadscreen.config.json` still supports the metadata-builder fields and now also supports:

- `httpPort`
- `bindAddress`
- `mdnsEnabled`
- `mdnsHost`
- `maxClients`
- `maxStreams`
- `clientWindowSeconds`

If a field is missing, sensible defaults are used.

## Metadata workflow

Nothing about the metadata format changed.

- Put your media under `media/`
- Run the existing metadata refresh tooling from `sdcard-template/tools/`
- The Pi backend reads `media/.nomadscreen/library.json` when present
- If that file is missing, the backend falls back to a direct filesystem scan and still builds `/api/library`

## Notes

- `platformio.ini` is now only a migration note so the repo clearly stops looking like an ESP32 build target.
- The frontend still exposes a “Device” page, but it now reports Raspberry Pi service status instead of onboard LCD or firmware state.
