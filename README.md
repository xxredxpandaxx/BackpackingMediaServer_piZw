# Backcountry Broadcast

A Raspberry Pi Zero W portable media server branded as Backcountry Broadcast. It keeps the existing media-library layout and metadata format while replacing the old microcontroller firmware with a Pi-native Python service.

The project now uses Backcountry Broadcast naming for the repo files, default service units, runtime config file, and generated metadata folder. During upgrades, the Pi still accepts older legacy config and metadata locations so existing installs can roll forward cleanly.

## What changed

- The project now runs on Raspberry Pi Zero W with Python.
- The web app in `data/` is still the main user interface.
- The backend serves the same core routes from Linux:
  - `/app`
  - `/api/status`
  - `/api/library`
  - `/api/stream`
  - `/api/asset`
  - `/api/rescan`
- The Pi still uses a simple `backcountry-broadcast.config.json` plus `media/` layout, and it keeps `library.json` compatibility while also building a SQLite catalog for paged browsing.

## Project layout

- `install.sh`: idempotent Pi installer for public GitHub repos
- `src/main.py`: Pi-native HTTP server, media scan logic, metadata merge, and streaming endpoints
- `data/`: static web app shell, styles, and client-side browsing logic
- `tools/backcountry_broadcast_refresh_metadata.py`: Pi-native metadata builder used during online rescans
- `backcountry-broadcast.config.example.json`: sample runtime config for `/srv/backcountry-broadcast/backcountry-broadcast.config.json`
- `deploy/network/`: fallback Wi-Fi script and `systemd` unit for known-network-first hotspot mode
- `deploy/backcountry-broadcast.service`: example `systemd` unit
- `deploy/backcountry-broadcast-screen.service`: example `systemd` unit for the TFT display launcher

## Storage layout

The server expects a storage root that contains:

- `backcountry-broadcast.config.json`
- `backcountry-broadcast.user.json`
- `media/`
- `media/.backcountry-broadcast/library.json` when metadata has been generated
- `media/.backcountry-broadcast/library.db` after the Pi scans the library

For local development, the app now keeps its default runtime files under `.backcountry-broadcast-runtime/` inside the repo so test media and generated metadata do not clutter the project root. On the Pi, `NOMADSCREEN_STORAGE_ROOT` holds config/runtime files such as `backcountry-broadcast.config.json` and the retained `backcountry-broadcast.user.json`, while `NOMADSCREEN_MEDIA_ROOT` can point at the real media library path. The installer now defaults that media path to `~/media`.

### Recommended media layout

- `media/movies`: movie files such as `.mp4`
- `media/tv`: episodic video files organized however you prefer
- `media/music`: music files such as `.mp3`, `.m4a`, `.flac`
- `media/audiobooks`: spoken-audio files such as `.mp3`, `.m4a`, `.m4b`
- `media/documents`: PDFs, maps, permits, checklists, and images

Nested folders inside `media/documents` show up as clickable folders in the Documents page.

### Runtime config notes

Keep `backcountry-broadcast.config.json` and `backcountry-broadcast.user.json` in your runtime storage root, for example `/srv/backcountry-broadcast`. You can keep that storage on:

- the Pi filesystem
- an external USB drive
- a removable SD card mounted by the Pi

Treat `backcountry-broadcast.config.json` as the installer-managed base file and put your custom values in `backcountry-broadcast.user.json`, which the installer and updater leave alone. The Device page now saves editable settings into the retained user file automatically, including the dedicated Screen Settings page for the attached TFT.

Edit that retained config file to set:

- the device/server name shown in the web UI
- the fallback hotspot name and password
- an optional dedicated password for the `/app/device` admin page
- whether fallback hotspot mode is enabled
- how long the Pi should wait for a known Wi-Fi network before it creates its own access point
- TMDb metadata settings and image-download options
- optional Pi server settings such as `httpPort`, `bindAddress`, and `mdnsEnabled`
- optional tiny-screen settings such as `displayEnabled`, `displayBackend`, `displayModel`, `displayView`, `displayStatusPollSeconds`, `displayBrightness`, and `displayButtons`

The backend and the fallback hotspot service both derive the `.local` host name automatically from `deviceName`.

The web UI now uses the SQLite catalog for the Home, Movies, TV, Movie Detail, and Show Detail routes so the browser does not need to download the entire library at once. Posters are lazy-loaded and the movie/show grids keep requesting more entries as you scroll.

Watch history now lives in the SQLite database on the Pi instead of only in browser storage. The server groups playback history by the client's local network address, so different browsers on the same phone, tablet, or laptop usually share the same resume history while connected to the Pi. That is the closest reliable device-level identifier the web app can use without direct hardware access such as a MAC address.

## Local run

1. Create and activate a virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

   For development tools:

   ```bash
   pip install -r requirements-dev.txt
   ```

   On the Pi, the installer also installs `requirements-pi.txt` for GPIO and display support.

3. Start the server:

   ```bash
   python src/main.py
   ```

4. If port `80` is already in use or you do not want admin privileges during local testing, override the port:

   ```bash
   NOMADSCREEN_PORT=8080 python src/main.py
   ```

5. Open `http://<device-or-pi-address>/app`.

## Development Checks

The project keeps checks light so they are usable on small boards and normal laptops:

```bash
pytest
ruff check .
```

## Raspberry Pi Zero W setup

### One-command install

Once the repo is public, you can install or update the Pi with one command:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/install.sh | sudo bash
```

What that installer does:

- installs `curl`, `git`, `python3`, `python3-venv`, and `NetworkManager`
- clones or updates the repo into `/opt/backcountry-broadcast`
- seeds `/srv/backcountry-broadcast/backcountry-broadcast.config.json` from `backcountry-broadcast.config.example.json` if needed
- creates `/srv/backcountry-broadcast/backcountry-broadcast.user.json` for retained custom settings if needed
- creates the standard `~/media` folder layout without overwriting existing files
- creates `/opt/backcountry-broadcast/.venv` and installs Python dependencies
- builds the Waveshare `fbcp` console-mirror binaries for the supported SPI panels
- installs File Browser into `/usr/local/bin/filebrowser`
- prepares `/srv/backcountry-broadcast/filebrowser` for the File Browser database and captured password
- applies the bundled File Browser branding from `/opt/backcountry-broadcast/deploy/filebrowser-branding`
- writes and enables `backcountry-broadcast-network.service`
- writes and enables `backcountry-broadcast.service`
- writes and enables `backcountry-broadcast-screen.service`
- updates the Pi boot config for TFT console mode when `displayBackend` is set to `console`
- writes and enables `backcountry-broadcast-filebrowser.service`
- captures the initial File Browser admin password so the Device page can show it

For normal updates after the first install, use the updater instead of the full installer:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/update.sh | bash
```

That updater:

- pulls the latest code into `/opt/backcountry-broadcast`
- refreshes Python dependencies
- rebuilds the Waveshare `fbcp` console-mirror binaries
- makes sure File Browser is installed
- reapplies the bundled File Browser branding
- rewrites the service units with your current paths
- refreshes `backcountry-broadcast-screen.service`
- updates the Pi boot config for TFT console mode when `displayBackend` is set to `console`
- refreshes `backcountry-broadcast-filebrowser.service`
- restarts `backcountry-broadcast`
- restarts `backcountry-broadcast-filebrowser`
- leaves `backcountry-broadcast-network` alone by default so you do not get kicked off the Pi's Wi-Fi mid-update

If you know you want to apply network-service changes immediately too, run:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/update.sh | bash -s -- --restart-network
```

### Manual setup

1. Install the base packages on the Pi:

   ```bash
   sudo apt update
   sudo apt install -y curl git python3 python3-venv network-manager
   ```

2. Clone the repo onto the Pi:

   ```bash
   sudo git clone <your-repo-url> /opt/backcountry-broadcast
   sudo chown -R $USER:$USER /opt/backcountry-broadcast
   ```

3. Copy `backcountry-broadcast.config.example.json` to your runtime storage root as `backcountry-broadcast.config.json`, for example `/srv/backcountry-broadcast/backcountry-broadcast.config.json`.
4. Create `/srv/backcountry-broadcast/backcountry-broadcast.user.json` with `{}` and put your custom settings there.
5. Create your media folders under the real media path, for example `~/media/{movies,tv,music,audiobooks,documents}`.
6. Create a virtual environment and install the dependencies:

   ```bash
   python3 -m venv /opt/backcountry-broadcast/.venv
   /opt/backcountry-broadcast/.venv/bin/pip install -r /opt/backcountry-broadcast/requirements.txt
   /opt/backcountry-broadcast/.venv/bin/pip install -r /opt/backcountry-broadcast/requirements-pi.txt
   ```

7. Install File Browser and create its state directory:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash
   sudo install filebrowser /usr/local/bin/filebrowser
   sudo mkdir -p /srv/backcountry-broadcast/filebrowser
   sudo chown -R $USER:$USER /srv/backcountry-broadcast/filebrowser
   ```

8. Start the server manually once to confirm the library loads:

   ```bash
   NOMADSCREEN_STORAGE_ROOT=/srv/backcountry-broadcast NOMADSCREEN_MEDIA_ROOT=/home/pi/media /opt/backcountry-broadcast/.venv/bin/python /opt/backcountry-broadcast/src/main.py
   ```

8. Install the example services if you want them to start on boot:

   ```bash
   sudo cp /opt/backcountry-broadcast/deploy/network/backcountry-broadcast-network.service /etc/systemd/system/backcountry-broadcast-network.service
   # If your Pi login is not "pi", edit User=, Group=, and NOMADSCREEN_MEDIA_ROOT= in backcountry-broadcast.service and backcountry-broadcast-filebrowser.service first.
   sudo cp /opt/backcountry-broadcast/deploy/backcountry-broadcast.service /etc/systemd/system/backcountry-broadcast.service
   sudo cp /opt/backcountry-broadcast/deploy/backcountry-broadcast-screen.service /etc/systemd/system/backcountry-broadcast-screen.service
   sudo cp /opt/backcountry-broadcast/deploy/backcountry-broadcast-filebrowser.service /etc/systemd/system/backcountry-broadcast-filebrowser.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now NetworkManager.service
   sudo systemctl enable --now backcountry-broadcast-network.service
   sudo systemctl enable --now backcountry-broadcast.service
   sudo systemctl enable --now backcountry-broadcast-screen.service
   sudo systemctl enable --now backcountry-broadcast-filebrowser.service
   ```

   To let the Device page show File Browser's initial admin password too, save the generated password into `/srv/backcountry-broadcast/filebrowser/admin-password.txt` after the first File Browser start:

   ```bash
   sudo sh -c "journalctl -u backcountry-broadcast-filebrowser.service -n 80 --no-pager | sed -n -E 's/.*randomly generated password: ([^[:space:]]+).*/\\1/p' | tail -n 1 > /srv/backcountry-broadcast/filebrowser/admin-password.txt"
   sudo chown $USER:$USER /srv/backcountry-broadcast/filebrowser/admin-password.txt
   sudo chmod 600 /srv/backcountry-broadcast/filebrowser/admin-password.txt
   ```

9. Open `/app/device`, launch File Browser from the File Management card, and log in with the captured initial admin password.

## Loading content over Wi-Fi

Once the Pi is online, the fastest browser-based path is File Browser from `/app/device`. It handles file uploads, renames, moves, and deletes directly against `~/media`, which keeps the Backcountry Broadcast server focused on low-power streaming, cataloging, and metadata work.

When you tap `Rescan Library` on the Device page, the Pi now checks for internet access first. If it is online and TMDb credentials are configured, it runs `tools/backcountry_broadcast_refresh_metadata.py` before the normal library scan so movie metadata and downloaded artwork stay fresh. If the Pi is offline, it falls back to the normal local rescan without failing the request.

You can also move media into `~/media` with whatever network workflow fits your setup, then run `/api/rescan` or use the Device page in the web UI.

Common choices are:

- `scp` or `sftp`
- SMB or Samba shares
- `rsync`
- Syncthing or another sync tool

## Fallback hotspot mode

On Raspberry Pi OS Bookworm and newer, the project uses NetworkManager for Wi-Fi handling.

The built-in `backcountry-broadcast-network.service` does this on boot:

- tries to join a known Wi-Fi network on `wlan0`
- waits `knownWifiTimeoutSeconds` for that connection to come up
- starts a fallback hotspot if no known network is available

The fallback hotspot uses:

- `deviceName` to derive the hotspot SSID shown in the UI
- `wifiPassword` as the hotspot password
- `10.0.0.1/24` as the fixed hotspot address on the Pi, with clients joining the `10.0.0.x` range
- `fallbackAccessPointEnabled` to turn the fallback behavior on or off
- `wifiInterface` if your wireless adapter is not `wlan0`

To preload known networks, use Raspberry Pi Imager advanced settings before first boot or connect once with `nmcli` on the Pi. NetworkManager will remember those credentials for future boots.

## Runtime config

`backcountry-broadcast.config.json` still supports the metadata-builder fields and `backcountry-broadcast.user.json` can override any of them. Those files now support:

- `httpPort`
- `bindAddress`
- `mdnsEnabled`
- `mdnsHost`
- `displayEnabled`: enable the physical SPI screen service
- `displayBackend`: `userspace` for the app-driven TFT UI or `console` to mirror the Pi boot console through Waveshare `fbcp`
- `displayModel`: `waveshare-1.69` or `waveshare-1.9`
- `displayView`: `auto`, `boot`, `wifi`, or `status`
- `displayStatusPollSeconds`: userspace TFT status polling interval, default `1.0`, clamped to `0.1-30`
- `displayBrightness`: TFT backlight brightness percent, default `100`, clamped to `5-100`
- `displayButtons`: optional GPIO pin mapping for the physical screen buttons, for example `{"next":"D16","previous":"D6","action":"D26"}`

When you switch `displayBackend` to `console`, turn the TFT on, or change `displayModel`, run `sudo ./update.sh` and reboot so the Pi can rebuild the matching `fbcp` binary and refresh `/boot/firmware/config.txt` or `/boot/config.txt` for the TFT console mode.
The `console` backend also needs Raspberry Pi's VideoCore development package `libraspberrypi-dev`, because Waveshare's `fbcp` build depends on `bcm_host.h`. On newer generic Debian images such as Debian 13 Trixie, that package may be unavailable, and the project will fall back to the working app-driven `userspace` display mode instead of failing the update.

The physical screen service reads button GPIO mappings from `/srv/backcountry-broadcast/backcountry-broadcast.user.json` if you want to override the defaults without touching the installer-managed base config. The default layout is Up `D6`, Down `D16`, and Select `D26`. Internally those are still stored as `previous`, `next`, and `action`, so `next` advances through `boot -> wifi -> status`, `previous` goes the other direction, and `action` toggles between the current manual screen and the configured auto/manual screen selection. It also supports long-press gestures: Select long-press toggles the backlight, Down long-press opens the on-screen Settings menu, and Up long-press jumps to Boot. That Settings menu can turn the Backcountry Wi-Fi hotspot on or off, change backlight brightness, reboot the Pi, or power it down. When `RPi.GPIO` is available, button presses use edge detection so the screen service can mostly sleep until either a button is pressed or the next status poll is due.

The bundled `backcountry-broadcast-screen.service` now runs as root so the built-in TFT settings menu can control NetworkManager and request reboot or poweroff without extra sudo setup.

The physical screen service assumes the standard Waveshare Raspberry Pi wiring for ST7789 SPI LCDs:

- `CE0` for chip select
- `GPIO25` for data/command
- `GPIO27` for reset
- `GPIO18` for backlight

Enable the Raspberry Pi SPI interface before using the attached screen, for example with `sudo raspi-config`.
- `wifiInterface`
- `devicePassword`
- `fallbackAccessPointEnabled`
- `knownWifiTimeoutSeconds`
- `metadataRefreshOnRescan`
- `metadataRefreshTimeoutSeconds`
- `maxClients`
- `maxStreams`
- `clientWindowSeconds`

If a field is missing, sensible defaults are used.

The Device page is password-protected. If `devicePassword` is set, that unlocks `/app/device`. If it is blank, the Device page falls back to the hotspot `wifiPassword`.

## Metadata workflow

Nothing about the metadata format changed, but the default Pi-side metadata refresh path is now the Python tool in `tools/backcountry_broadcast_refresh_metadata.py`.

- Put your media under `media/`
- Run the bundled metadata builder manually if you want:

  ```bash
  /opt/backcountry-broadcast/.venv/bin/python /opt/backcountry-broadcast/tools/backcountry_broadcast_refresh_metadata.py --storage-root /srv/backcountry-broadcast --media-root /home/pi/media
  ```

- The Pi backend still reads `media/.backcountry-broadcast/library.json` when present for metadata compatibility
- Each rescan also rebuilds `media/.backcountry-broadcast/library.db`, which powers the paged movie/show catalog APIs used by the web UI
- The metadata refresh step now also writes richer `movie_metadata` and `show_metadata` tables inside that same SQLite file using the smarter TMDb detail fetch logic
- If the JSON file is missing, the backend falls back to a direct filesystem scan and still rebuilds the live library plus the SQLite catalog

Typical metadata flow:

1. Copy media into the `media/` folders.
2. Optionally run the metadata tool directly on the Pi.
3. Let it rebuild `media/.backcountry-broadcast/library.json` and any downloaded artwork.
4. Transfer the updated media tree to the Pi if needed.
5. Trigger `Rescan Library` from `/app/device`.

## Notes

- The frontend still exposes a "Device" page, but it now reports Raspberry Pi service status instead of onboard firmware state.
- The Pi-side automatic rescan path uses `tools/backcountry_broadcast_refresh_metadata.py`.
- On Raspberry Pi OS Bookworm and newer, NetworkManager remembers known Wi-Fi networks and the project only creates its own hotspot when those networks are unavailable.
- Config saves, metadata JSON writes, and the SQLite catalog use crash-safer write patterns so a sudden battery pull is much less likely to corrupt the library.
