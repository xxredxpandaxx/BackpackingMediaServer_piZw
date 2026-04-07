# Backpacking Media Server

A Raspberry Pi Zero W portable media server that keeps the existing Nomad Screen web UI, media-library layout, and metadata format, while replacing the old microcontroller firmware with a Pi-native Python service.

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
- The Pi still uses a simple `nomadscreen.config.json` plus `media/` layout, and it keeps `library.json` compatibility while also building a SQLite catalog for paged browsing.

## Project layout

- `install.sh`: idempotent Pi installer for public GitHub repos
- `src/main.py`: Pi-native HTTP server, media scan logic, metadata merge, and streaming endpoints
- `data/`: static web app shell, styles, and client-side browsing logic
- `tools/nomadscreen_refresh_metadata.py`: Pi-native metadata builder used during online rescans
- `nomadscreen.config.example.json`: sample runtime config for `/srv/nomadscreen/nomadscreen.config.json`
- `deploy/network/`: fallback Wi-Fi script and `systemd` unit for known-network-first hotspot mode
- `deploy/nomadscreen.service`: example `systemd` unit

## Storage layout

The server expects a storage root that contains:

- `nomadscreen.config.json`
- `media/`
- `media/.nomadscreen/library.json` when metadata has been generated
- `media/.nomadscreen/library.db` after the Pi scans the library

For local development, the app now keeps its default runtime files under `.nomadscreen-runtime/` inside the repo so test media and generated metadata do not clutter the project root. On the Pi, `NOMADSCREEN_STORAGE_ROOT` holds config/runtime files such as `nomadscreen.config.json`, while `NOMADSCREEN_MEDIA_ROOT` can point at the real media library path. The installer now defaults that media path to `~/media`. Large web uploads are staged under `/var/tmp/nomadscreen-upload` so they do not fill the Pi Zero W's small `/tmp` RAM disk.

### Recommended media layout

- `media/movies`: movie files such as `.mp4`
- `media/tv`: episodic video files organized however you prefer
- `media/music`: music files such as `.mp3`, `.m4a`, `.flac`
- `media/audiobooks`: spoken-audio files such as `.mp3`, `.m4a`, `.m4b`
- `media/documents`: PDFs, maps, permits, checklists, and images

Nested folders inside `media/documents` show up as clickable folders in the Documents page.

### Runtime config notes

Keep `nomadscreen.config.json` in your runtime storage root, for example `/srv/nomadscreen`. You can keep that storage on:

- the Pi filesystem
- an external USB drive
- a removable SD card mounted by the Pi

Edit that config file to set:

- the device/server name shown in the web UI
- the fallback hotspot name and password
- whether fallback hotspot mode is enabled
- how long the Pi should wait for a known Wi-Fi network before it creates its own access point
- TMDb metadata settings and image-download options
- optional Pi server settings such as `httpPort`, `bindAddress`, and `mdnsEnabled`

The backend and the fallback hotspot service both derive the `.local` host name automatically from `deviceName`.

The web UI now uses the SQLite catalog for the Home, Movies, TV, Movie Detail, and Show Detail routes so the browser does not need to download the entire library at once. Posters are lazy-loaded and the movie/show grids keep requesting more entries as you scroll.

Watch history now lives in the SQLite database on the Pi instead of only in browser storage. The server groups playback history by the client's local network address, so different browsers on the same phone, tablet, or laptop usually share the same resume history while connected to the Pi. That is the closest reliable device-level identifier the web app can use without direct hardware access such as a MAC address.

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

### One-command install

Once the repo is public, you can install or update the Pi with one command:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/install.sh | sudo bash
```

What that installer does:

- installs `git`, `python3`, `python3-venv`, and `NetworkManager`
- clones or updates the repo into `/opt/nomadscreen`
- seeds `/srv/nomadscreen/nomadscreen.config.json` from `nomadscreen.config.example.json` if needed
- creates the standard `~/media` folder layout without overwriting existing files
- creates `/opt/nomadscreen/.venv` and installs Python dependencies
- prepares `/var/tmp/nomadscreen-upload` for large browser uploads
- writes and enables `nomadscreen-network.service`
- writes and enables `nomadscreen.service`

For normal updates after the first install, use the updater instead of the full installer:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/update.sh | bash
```

That updater:

- pulls the latest code into `/opt/nomadscreen`
- refreshes Python dependencies
- rewrites the service units with your current paths
- keeps large web uploads pointed at `/var/tmp/nomadscreen-upload`
- restarts `nomadscreen`
- leaves `nomadscreen-network` alone by default so you do not get kicked off the Pi's Wi-Fi mid-update

If you know you want to apply network-service changes immediately too, run:

```bash
curl -fsSL https://raw.githubusercontent.com/xxredxpandaxx/BackpackingMediaServer_piZw/main/update.sh | bash -s -- --restart-network
```

### Manual setup

1. Install the base packages on the Pi:

   ```bash
   sudo apt update
   sudo apt install -y git python3 python3-venv
   ```

2. Clone the repo onto the Pi:

   ```bash
   sudo git clone <your-repo-url> /opt/nomadscreen
   sudo chown -R $USER:$USER /opt/nomadscreen
   ```

3. Copy `nomadscreen.config.example.json` to your runtime storage root as `nomadscreen.config.json`, for example `/srv/nomadscreen/nomadscreen.config.json`.
4. Create your media folders under the real media path, for example `~/media/{movies,tv,music,audiobooks,documents}`.
5. Create a virtual environment and install the dependency:

   ```bash
   python3 -m venv /opt/nomadscreen/.venv
   /opt/nomadscreen/.venv/bin/pip install -r /opt/nomadscreen/requirements.txt
   ```

6. Start the server manually once to confirm the library loads:

   ```bash
   NOMADSCREEN_STORAGE_ROOT=/srv/nomadscreen NOMADSCREEN_MEDIA_ROOT=/home/pi/media /opt/nomadscreen/.venv/bin/python /opt/nomadscreen/src/main.py
   ```

7. Install the example service if you want it to start on boot:

   ```bash
   sudo cp /opt/nomadscreen/deploy/network/nomadscreen-network.service /etc/systemd/system/nomadscreen-network.service
   # If your Pi login is not "pi", edit User=, Group=, and NOMADSCREEN_MEDIA_ROOT= in nomadscreen.service first.
   sudo cp /opt/nomadscreen/deploy/nomadscreen.service /etc/systemd/system/nomadscreen.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now NetworkManager.service
   sudo systemctl enable --now nomadscreen-network.service
   sudo systemctl enable --now nomadscreen.service
   ```

8. Open `/app/device` and use the built-in upload panel to send files over Wi-Fi, or copy media into `~/media` manually if you prefer.

## Loading content over Wi-Fi

Once the Pi is online, the fastest path is the upload panel on `/app/device`, which saves files into the library and rescans automatically. Big uploads stage through `/var/tmp/nomadscreen-upload` first, so free space there matters too even though the finished media lands in `~/media`.

When you tap `Rescan Library` on the Device page, the Pi now checks for internet access first. If it is online and TMDb credentials are configured, it runs `tools/nomadscreen_refresh_metadata.py` before the normal library scan so movie metadata and downloaded artwork stay fresh. If the Pi is offline, it falls back to the normal local rescan without failing the request.

You can still move media into `~/media` with whatever network workflow fits your setup, then run `/api/rescan` or use the Device page in the web UI.

Common choices are:

- `scp` or `sftp`
- SMB or Samba shares
- `rsync`
- Syncthing or another sync tool

## Fallback hotspot mode

On Raspberry Pi OS Bookworm and newer, the project uses NetworkManager for Wi-Fi handling.

The built-in `nomadscreen-network.service` does this on boot:

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

`nomadscreen.config.json` still supports the metadata-builder fields and now also supports:

- `httpPort`
- `bindAddress`
- `mdnsEnabled`
- `mdnsHost`
- `wifiInterface`
- `fallbackAccessPointEnabled`
- `knownWifiTimeoutSeconds`
- `metadataRefreshOnRescan`
- `metadataRefreshTimeoutSeconds`
- `maxClients`
- `maxStreams`
- `clientWindowSeconds`

If a field is missing, sensible defaults are used.

## Metadata workflow

Nothing about the metadata format changed, but the default Pi-side metadata refresh path is now the Python tool in `tools/nomadscreen_refresh_metadata.py`.

- Put your media under `media/`
- Run the bundled metadata builder manually if you want:

  ```bash
  /opt/nomadscreen/.venv/bin/python /opt/nomadscreen/tools/nomadscreen_refresh_metadata.py --storage-root /srv/nomadscreen --media-root /home/pi/media
  ```

- The Pi backend still reads `media/.nomadscreen/library.json` when present for metadata compatibility
- Each rescan also rebuilds `media/.nomadscreen/library.db`, which powers the paged movie/show catalog APIs used by the web UI
- The metadata refresh step now also writes richer `movie_metadata` and `show_metadata` tables inside that same SQLite file using the smarter TMDb detail fetch logic
- If the JSON file is missing, the backend falls back to a direct filesystem scan and still rebuilds the live library plus the SQLite catalog

Typical metadata flow:

1. Copy media into the `media/` folders.
2. Optionally run the metadata tool directly on the Pi.
3. Let it rebuild `media/.nomadscreen/library.json` and any downloaded artwork.
4. Transfer the updated media tree to the Pi if needed.
5. Trigger `Rescan Library` from `/app/device`.

## Notes

- The frontend still exposes a "Device" page, but it now reports Raspberry Pi service status instead of onboard firmware state.
- The Pi-side automatic rescan path uses `tools/nomadscreen_refresh_metadata.py`.
- On Raspberry Pi OS Bookworm and newer, NetworkManager remembers known Wi-Fi networks and the project only creates its own hotspot when those networks are unavailable.
- Config saves, metadata JSON writes, media-file finalization, and the SQLite catalog now use crash-safer write patterns so a sudden battery pull is much less likely to corrupt the library. An upload that is interrupted mid-transfer can still be lost, but it should stay isolated to a temporary staging file instead of damaging existing media.
