# Storage Template

Copy the contents of this folder to the storage root that the Raspberry Pi Zero W server will use.

That storage root should contain:

- `nomadscreen.config.json`
- `media/`
- `tools/`

You can keep that storage on:

- the Pi filesystem
- an external USB drive
- a removable SD card mounted by the Pi

## Shared config file

Edit `nomadscreen.config.json` to set:

- the device/server name shown in the web UI
- the Wi-Fi password you want the UI to report for hotspot mode
- TMDb metadata settings and image-download options
- optional Pi server settings such as `httpPort`, `bindAddress`, and `mdnsEnabled`

The backend still derives the Wi-Fi name and `.local` host automatically from `deviceName`.

## Metadata workflow

1. Copy media into the `/media` folders in this storage root.
2. If you maintain the library from a Windows machine before sending it to the Pi, double-click `NomadScreen Refresh Metadata.cmd`.
3. Let the script rebuild `/media/.nomadscreen/library.json` and any downloaded artwork.
4. Transfer the updated media tree to the Pi if needed, then trigger `/api/rescan` from the Device page.

The Pi backend reads those generated files for titles, summaries, ratings, and poster art, but it does not show `.nomadscreen` as normal media.

## Recommended layout

- `media/movies`: movie files such as `.mp4`
- `media/tv`: episodic video files
- `media/music`: music files such as `.mp3`, `.m4a`, `.flac`
- `media/audiobooks`: spoken-audio files such as `.mp3`, `.m4a`, `.m4b`
- `media/documents`: PDFs, maps, permits, checklists, and images

## Notes

- The metadata script still works with built-in PowerShell on Windows.
- `tools/nomadscreen-metadata.config.json` is kept only as a legacy metadata-only fallback.
- Nested folders inside `media/documents` still show up as clickable folders in the Documents page.
