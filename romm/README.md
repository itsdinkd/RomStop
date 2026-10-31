# RomStop for RomM

*Note: **romShelf** in code represents the previous name for this project*

Adds RomStop to [RomM](https://github.com/rommapp/romm) 5.4.1:

- **A RomStop tab on every game page.** One collapsed row per hack made for that game. Opening a row shows the ROM the hack needs (green when the ROM you are on is the right one, red with the needed ROM's name when it is not, for example Mega Man 2 (Japan) when you are on the USA dump), the needed and your checksums, the version with a changelog button, the description, a screenshot gallery, ratings, authors and source links, and an **Install** button.
- **Install** downloads the patch on the server, from RomStop's patch mirror first and then the hack's own download link, patches your ROM with RomM's own patcher, and adds the result as a new game named like `Hack Title (USA) (v1.2) [RomStop].nes`, with its description, cover, screenshots, authors and release date filled in and locked, and puts it in a "RomStop" collection. Copier and iNES headers are handled. If a site blocks server downloads, the row links to the hack's page so you can download the file and drop it (or its zip) on the row.
- **A RomStop page in the top bar**, like the website: every hack, by console, with search, type, sort, grid or list, and Show filters: "Ready to install" (a ROM in your library fits the hack), "I have the game" (any version of the game, including the wrong one) and "Need the game" (no copy of the game in your library). A green check means a ROM in your library fits the hack; a caution sign means you have the game but not the right version. The header count reads "N of total" while a filter is on. **Save Rom Hack** (the bookmark on each card, and the button on a hack's page) keeps a hack on your saved list, say one whose game you still need to add; the **Saved** toggle shows only saved hacks and combines with the Show filters, so Saved + "Need the game" lists the games still to add. Saved hacks are kept in RomM for your account, so they follow you to every device. On phones the page actions wrap under the title and the Show filter is a dropdown. On a hack's page, the original game's name (in "A hack of …" and under "ROM needed") opens that game's page in your library when you have it; it stays plain text when you don't.
- **Emulator compatibility**: when the hack's page says it doesn't work on an emulator, or only works on some, the row shows an "Emulator notes" tag and the details list which ones, with the page's own words. An installed hack is set to play with a working EmulatorJS core in RomM's player (only if you haven't picked one yourself), and the row warns when none of RomM's browser cores is known to work.
- Update badges when a newer version of an installed hack is out, a version picker, and a Reinstall button.
- **Import saves** (button on the RomStop page, or `/romstop?import=saves`): pick your RetroArch, ES-DE, ROM or emulator folders in the browser, on the device that has the saves (Legion Go, Steam Deck, PC). It finds save files, save states (`.state`, `.state1`…, `.state.auto`, standalone emulators' states) and state screenshots, matches each to a game by name, by a loose name, or by the checksum of the ROM next to it, and works out the emulator from RetroArch's core folders and playlists and ES-DE's emulator settings (or you pick one per system). You review the list: files named differently from the game are offered a rename to the game's name (RomM's copy only), and a game or emulator can be changed per file. Nothing in RomM is ever replaced: identical files are skipped, and a different file under a taken name is kept as a new version or skipped, your choice each time. Shared memory cards (PCSX2 `.ps2`, Dolphin card images) and folder saves (PPSSPP `SAVEDATA`, 3DS, Switch) are listed but not imported yet. For saves made after the import, RetroArch's own Cloud Sync can talk to RomM 5.4.1 directly (WebDAV at `/api/sync/retroarch/`; set up per RomM's docs, unverified on a real device); the import files saves the way Cloud Sync does, so the two don't duplicate each other.

RomM has no plugin system, so this is a small overlay on RomM's source: new files in `overlay/`, edits to RomM's own files in `romm.patch`, built into `ghcr.io/itsdinkd/romstop-romm` on top of the official image.

## Windows 11

Download `RomM-RomStop-Setup.exe` from <https://github.com/itsdinkd/RomStop/releases/latest/download/RomM-RomStop-Setup.exe> and run it. It asks for your ROM folder, a port and optional metadata accounts, then turns on WSL2 (one restart, after which it continues by itself), installs Docker Desktop, and starts RomM at `http://localhost:8080`. Create your admin account there and run a scan. Start menu entries open RomM and update it. Running the setup again repairs or upgrades and keeps your data. Uninstalling removes the containers and never touches your ROMs.

Scans run 8 ROMs at once (`SCAN_WORKERS` in `.env`; RomM's own default is 4). Set it to your ScreenScraper thread count, for example `-ScanWorkers 11`, or edit `.env` and run `docker compose up -d` in the install folder. RomM never sends ScreenScraper more requests at once than your account allows, so a higher number only queues. Scanning is fastest when the ROM folder is on a local SSD: every ROM is read in full once to hash it, which is how RetroAchievements, ScreenScraper and the RomStop patcher recognise it.

The scripts work on their own too: `powershell -ExecutionPolicy Bypass -File windows\Install-RomM.ps1 -LibraryDir D:\ROMs`.

## Any other Docker host

Use `ghcr.io/itsdinkd/romstop-romm:latest` instead of `rommapp/romm` in RomM's usual docker-compose file. Settings (all optional):

| Variable | Default |
| --- | --- |
| `ROMSTOP_SITE_URL` | `https://romstop.pages.dev/` |
| `ROMSTOP_INDEX_URL` | `<site>/device/index.json` (a `file://` path works too) |
| `ROMSTOP_INDEX_TTL_SECONDS` | `600` |

## Development

- Build: `docker build -t romstop-romm romm`
- Tests: `python -m unittest discover romm/tests`
- Edit in a RomM checkout: `git clone -b 5.4.1 https://github.com/rommapp/romm romm-checkout && romm/apply.sh romm-checkout`, change things there, then `romm/export-from-tree.sh romm-checkout` writes `overlay/` and `romm.patch` back.
- New RomM release: bump `ROMM_VERSION`, run `apply.sh` on the new tag and fix any patch rejects.
