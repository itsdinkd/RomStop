# RomStop on EmulationStation DE and Knulli

`romstop.py` adds a **RomStop** system to EmulationStation DE (Steam Deck, Legion Go, Windows, Linux) or to Knulli/Batocera. Inside it, each console has a folder with one entry per hack on the site. You browse them like games, with the site's cover art, description, authors, version, genre and rating. Nothing is patched until you pick a hack and launch it. Then the tool:

1. looks through the ROMs already on the device for the hack's base ROM, by checksum (CRC32 from the BPS/UPS patch or the hack's base ROM info, SHA-1 when known), including inside `.zip` files, SNES ROMs with a copier header, NES ROMs with or without the iNES header, and N64 dumps in any byte order;
2. applies the patch mirrored on the site (IPS, BPS or UPS), checking the result's checksum when the patch records one;
3. saves `<Hack> (<version>) [RomStop].<ext>` in that console's ROM folder and adds it to the console's game list with the hack's name, description and authors.

Base ROMs are never downloaded. If none matches, the message says which ROM (and checksum) is needed.

Launch **Refresh hack list** to pick up new hacks and versions from the site.

## Install: drag and drop

Download the zip for your device. Nothing gets reinstalled: you drag its folders onto your existing setup, and your systems, ROMs and settings stay as they are. Each zip has a "How to install.txt".

| Device | Zip | Where the folders go |
|---|---|---|
| Legion Go 2 / any PC on Windows | [RomStop-ES-DE-Windows.zip](https://github.com/itsdinkd/RomStop/releases/latest/download/RomStop-ES-DE-Windows.zip) | contents of `ES-DE` into `C:\Users\<you>\ES-DE`, `ROMs/romstop` into your ROMs folder |
| Steam Deck, SteamOS, Bazzite | [RomStop-ES-DE-SteamDeck-Linux.zip](https://github.com/itsdinkd/RomStop/releases/latest/download/RomStop-ES-DE-SteamDeck-Linux.zip) | contents of `ES-DE` into `~/ES-DE`, `ROMs/romstop` into your ROMs folder (EmuDeck: `Emulation/roms`) |
| Anbernic on Knulli, Batocera | [RomStop-Knulli-Batocera.zip](https://github.com/itsdinkd/RomStop/releases/latest/download/RomStop-Knulli-Batocera.zip) | `roms` and `system` onto the device's share (network share or the SD card's share partition) |

Then restart ES-DE (on Knulli: restart EmulationStation or Game settings > Update gamelists).

If you already have your own ES-DE `custom_systems/es_systems.xml`, copy the `<system>` block from the zip's file into yours instead of replacing it.

The zips carry the hack list, cover art and the tool (`romstop.exe` on Windows, `romstop.py` elsewhere, which runs with the Python 3 that SteamOS, Bazzite and Knulli already have). The first launch works out where your ROMs and ES-DE folders are from where the romstop folder sits.

### Or install from a terminal

```sh
curl -fsSL https://github.com/itsdinkd/RomStop/releases/latest/download/romstop.py -o /tmp/romstop.py && python3 /tmp/romstop.py setup
```

`setup` finds ES-DE or Knulli, adds the system (keeping an existing `es_systems.xml`), and fetches the hack list and covers. On Windows, `romstop.exe` from the [latest release](https://github.com/itsdinkd/RomStop/releases/latest) does the same when double-clicked. `romstop remove` takes the system out again; patched ROMs stay. For a portable ES-DE, pass `--esde-home <folder that holds settings/es_settings.xml>`.

## Where things go

| | ES-DE | Knulli / Batocera |
|---|---|---|
| Tool, cached hack list and patches, log | `~/.local/share/romstop/` (Windows: `%APPDATA%\RomStop\`) | `/userdata/system/romstop/` |
| System definition | `~/ES-DE/custom_systems/es_systems.xml` (an existing file is kept, only the `romstop` system is added) | `/userdata/system/configs/emulationstation/es_systems_romstop.cfg` |
| Hack entries and cover art | `<ROMs>/romstop/<console>/*.romstop`, covers in `~/ES-DE/downloaded_media/romstop/` | `/userdata/roms/romstop/...`, covers in `romstop/images/` |
| Patched ROMs | `<ROMs>/snes/`, `<ROMs>/gba/`, ... | `/userdata/roms/snes/`, ... |
| Game lists | `~/ES-DE/gamelists/<system>/gamelist.xml` | `/userdata/roms/<system>/gamelist.xml` |

Checksums of ROMs are cached in `checksums.json`, so only new or changed files are read on later launches.

## After patching

- **ES-DE** doesn't reload game lists while it runs, so the new ROM shows up after a restart or **Main menu > Utilities > Rescan ROM directory**.
- **Knulli/Batocera**: the tool asks EmulationStation to reload the game lists when you're back in the menu.

A message box shows the result (zenity or kdialog on Linux, a dialog on Windows, a notification on Knulli). Everything is also written to `romstop.log`.

## Emulator compatibility

When a hack's page says it only works on some emulators, or doesn't work on some, the entry's description says so ("Emulators: Works on bsnes. Doesn't work on Snes9x."). Patching then sets the new game to run with a working emulator, so it plays without changing anything by hand:

- **ES-DE**: `<altemulator>` in the console's `gamelist.xml`, the same as picking an alternative emulator in the metadata editor. The label is checked against ES-DE's `es_systems.xml` when the tool can find it (custom_systems, or the bundled one next to ES-DE.exe on Windows); the AppImage keeps its copy inside the image, so there the tool uses ES-DE's standard labels. ES-DE's "Enable alternative emulators per game" setting must be on (it is by default).
- **Knulli / Batocera**: per-game `<system>["<rom file>"].emulator=` and `.core=` lines in `/userdata/system/batocera.conf`, the same as Advanced game options > Emulator. Existing settings for other games are kept.

If none of the emulators the frontend can run is known to work, the description and the result message say so and nothing is changed. Hacks with no emulator notes keep the frontend's default.

## Hacks without a mirrored patch

Some authors ask not to rehost their patches, and some hacks aren't mirrored yet. Their entries say where to get the patch. Put the `.ips`, `.bps` or `.ups` file in `romstop/patches/<hack id>/` inside your ROMs folder and launch the entry again.

IPS patches record no checksum. When the hack has no base ROM checksum either, the tool picks the base ROM by file name and says so.

## How it's built

`romstop.py` is one stdlib-only file. The release workflow (`.github/workflows/release.yml`) builds `romstop.exe` with PyInstaller on Windows (`device.yml`, which also runs the tests on Linux and Windows), downloads the hack list from the RomStop site, draws cover art with `device/covers.py` (Pillow, build time only), and builds the three zips with `romstop.py package`. The tool reads the hack list from `<site>/device/index.json` (see `docs/architecture.md`). Patch paths in the index are relative to the site root. Hacks marked `noMirror` have no patch files listed.

Tests: `python3 -m unittest discover device`.
