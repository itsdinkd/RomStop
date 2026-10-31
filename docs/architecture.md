# Integrations: ES-DE / Knulli tool and RomM add-on

Both read `device/index.json` published by the RomStop site (`format: 1`). The site, its hack data and the code that writes the index live in a separate, private repo; this repo only reads the index. Moved here from that repo on 2026-10-31. User-facing behavior is described in `device/README.md` and `romm/README.md`; this file covers how it is built and what to keep in sync.

## The device index (contract)

The site build writes `<site>/device/index.json` with this shape. Any change to it is made in both repos together: a PR in the site repo that writes the new field, and a PR here that reads it.

```
{ format: 1, generated, consoles: { <id>: {name, short, extensions, colors} },
  hacks: [ { id, title, console, baseGame, baseRom, authors, type, genre, version, released, updated, summary, description,
             rating, page, links, patches[{path,name,format,size,crc32,sourceUrl,sourceLabel,version}], noMirror,
             downloadUrl, versions, ratings, favs, favsUrl, features, tags, patchFormat, screenshots, titleScreen,
             language, patchNotes, downloads, compatibility{...,summary,names}, play{esde,batocera,emulatorjs},
             install{...,label,needs,steps,emulator}, mario{difficulty,length,types} } ] }
```

- Patch `path`s are relative to the site root, or absolute R2 URLs. `noMirror` hacks list no patches.
- Any field change or removal must be checked against `device/romstop.py` (`sync`, `hack_stubs`, `game_entry`, `get_patch`), `romm/overlay/backend/handler/romshelf/index.py` (`_prepare`, `_absolute`) and the RomM frontend types in `romm/overlay/frontend/src/services/api/romshelf.ts`. Adding optional fields is safe; bump `INDEX_FORMAT` (romstop.py) together with the site's index format for breaking changes.

## ES-DE / Knulli tool (`device/`)

- `device/romstop.py`: one stdlib-only file (about 1,260 lines) declaring Python 3.8+ as its minimum; CI tests it on 3.12 only . Commands: `setup`, `sync`, `install <entry.romstop>`, `remove`, `package` (used by the release workflow), internal `_reload`. Also built to `romstop.exe` with PyInstaller on Windows (`device.yml`).
- Frontend detection: `Layout` / `detect_config` / `find_esde_home` / `is_batocera` find ES-DE (`~/ES-DE`, Windows `%APPDATA%`) or Batocera/Knulli (`/userdata`). `write_system_definition` adds a `romstop` system to `custom_systems/es_systems.xml` (existing file is kept, only the system block is added) or `es_systems_romstop.cfg`.
- `sync`: downloads the index, creates one `.romstop` stub per hack under `<ROMs>/romstop/<console>/`, writes gamelist entries (`game_entry`, description with compatibility via `compat_lines`), covers via `device/covers.py` output (`media/`, Pillow, build time only).
- `install` (on launch of a stub): `find_base_rom` hashes ROMs already on the device (`rom_candidates`; handles zips, SNES copier headers, NES iNES header, N64 byte orders via `variants`/`checksums`), `get_patch` fetches the mirrored patch (cached, crc32-checked) or uses a patch the user dropped into `<ROMs>/romstop/patches/<id>/`, `apply_patch` (own IPS/BPS/UPS implementation, mirrors the site's TypeScript patcher), writes `<Hack> (<version>) [RomStop].<ext>` into that console's ROM folder, updates the gamelist, then sets the per-game emulator from `hack.play` (`pick_emulator`; ES-DE `<altemulator>`, Batocera `batocera.conf` via `set_batocera_game_emulator`). Base ROMs are never downloaded.
- Install-only hacks (`install`) are shown with their steps and are never auto-patched.
- `package` builds drag-and-drop zips (ES-DE Windows, ES-DE Steam Deck/Linux, Knulli/Batocera) with "How to install.txt"; the owner wants to add to existing installs, not reinstall.
- Tests: `device/test_romstop.py` (20 tests): `python3 -m unittest discover device`. CI also runs them on Windows and smoke-tests `romstop.exe setup` (`device.yml`).
- The IPS/BPS/UPS code exists in both TypeScript (the site repo) and Python (`romstop.py`, and `romm` uses RomM's own `utils.rom_patcher`); fix patching bugs in each.

## RomM add-on (`romm/`)

RomM has no plugin system, so RomStop is an overlay on RomM source pinned to `romm/ROMM_VERSION` (5.4.1).

- `romm/overlay/` holds new files copied over a RomM checkout; `romm/romm.patch` holds edits to seven upstream files (`backend/main.py` registers the router under `/api`; frontend router, route names, nav destinations, `GameDetails.vue` for the tab, e2e pages). `romm/apply.sh <checkout>` applies both; `romm/export-from-tree.sh <checkout>` regenerates them after editing in a checkout. Do not hand-edit `romm.patch` hunks.
- Image: `romm/Dockerfile` clones RomM at `ROMM_VERSION`, applies the overlay, runs the frontend `typecheck` + `build`, then copies the patched backend files and built frontend onto `rommapp/romm:<version>`. Published as `ghcr.io/itsdinkd/romstop-romm:{latest,<version>}` on pushes to `main` that touch `romm/**` (`romm-image.yml`; PRs build and smoke-test against MariaDB).
- Backend API (`endpoints/romshelf.py`, prefix `/api/romshelf`, scopes `ROMS_READ`/`ROMS_WRITE`): `GET /status`, `POST /refresh`, `GET /hacks`, `GET /hacks/{id}`, `GET /roms/{rom_id}` (hacks for a game with match info), `POST /install`, `GET /installs/{record_id}`, `GET /counts/{hack_id}` (this RomM's download/play counts); saved hacks (`ROMS_USER_READ`/`ROMS_USER_WRITE`): `GET /saved`, `PUT /saved/{hack_id}`, `DELETE /saved/{hack_id}` (the list and hack responses also carry `saved`); save import (`ROMS_READ` + `ASSETS_READ`, read-only): `GET /saves/options` (systems, games, emulator names), `POST /saves/plan`.
- Handler modules (`handler/romshelf/`):
  - `index.py`: downloads and caches the device index (`ROMSTOP_SITE_URL`, `ROMSTOP_INDEX_URL`, `ROMSTOP_INDEX_TTL_SECONDS`, default **600 s**, conditional ETag/Last-Modified requests; cache under RomM resources `romshelf/`).
  - `library.py` + `matching.py`: hash the library in the background (headerless CRC/SHA, cached) and match hacks to ROMs: `MatchLevel` exact (checksum) / likely (name, game and region) / wrong_version / none; `PLATFORM_CONSOLE` maps RomM platform slugs to RomStop console ids.
  - `patch_source.py`: download the patch (URL list from `patch_urls`: version's mirrored patches first, preferring BPS/UPS, then the source `downloadUrl` unless `manual_only`), unpack zip/7z/rar (`/usr/bin/7zz`, `bsdtar`), refuse any archive holding a program (`unsafe`), pick among multiple patches (`NeedsChoice`), or accept a user-uploaded file (`browser_download` code when the server cannot fetch).
  - `installs.py`: strips headers, applies the patch with RomM's `utils.rom_patcher.apply_patch`, saves `<Hack> (<region>) (v<ver>) [RomStop].<ext>`, scans it into the library and finalizes metadata (description, cover, screenshots, authors, release date, locked via `manual_metadata`), adds it to the "RomStop" collection, and records installs in `installs.json` (file-locked). `modpatch.py` + `install_mod` handle Wii Riivolution and Vita rePatch installs (Python twin of `src/lib/modpatch`).
  - `counters.py`: counts for this RomM only (owner's choice, 2026-10-29: the static site has no counters, since it has no server to store them). Downloads: each successful install adds one in `romshelf/counts.sqlite` (a failure to count never fails the install). Plays: RomM's own `play_sessions` rows for the ROMs installed from the hack (`installs.json`).
  - `saved.py`: Save Rom Hack, per RomM user id, in `romshelf/saved.sqlite` (owner's choice, 2026-10-30: kept on the RomM server like the counts, nothing extra runs on the owner's PC). Ids the index later drops stay saved and are simply not listed.
  - `archives.py`: reads/extracts ROMs from zip/7z/rar for hashing and patching.
  - `saves.py` (stdlib only): the save import planner. Classifies a file (save, state, state screenshot, shared memory card, folder save), matches it to a RomM ROM (exact file name, case, loose name, device ROM CRC32), picks the emulator (RetroArch `saves|states/<core>/` folder via RomM's `handler/sync/retroarch/emulator_names.py`, standalone emulator folder, RetroArch `.lpl` playlists, ES-DE gamelist `altemulator` / `alternativeEmulator` and `es_systems.xml` default), names it `<ROM fs_name_no_ext><suffix>` like RomM's Cloud Sync, and checks it against the user's saves/states (MD5 = RomM's `content_hash`). A taken name with different content is a `conflict`, offered under RomM's version tag (` [YYYY-MM-DD_HH-MM-SS]`); it never overwrites.
- Frontend (Vue, RomM "v2" UI): `views/Romshelf/RomshelfIndex.vue` (top-bar page), `RomshelfHack.vue`, `components/Romshelf/*` (`RollButton` + `RollDialog` + `roll.ts`: "Roll a Rom Hack" on the index page, a case-opening reel that lands on a random hack that isn't installed, has a fetchable patch and an exact/likely library ROM, then opens it with `?rolled=1`; only then does the hack page show Re-roll; `HackCounters`, `SaveHackButton` (bookmark on index cards and list rows, button on the hack page; the index's Saved toggle, `?saved=1`, narrows the Show filters and their counts), `RomshelfTab`, `HackRow`, `HackDetails`, `InstallPanel`, `ChangelogDialog`, `ScreenshotGallery`, `MarioSpecifics` + `mario.ts` (SMW Central's Difficulty/Length/Mario Type card on the hack details), `RichText`, `MatchBadge`, `compat.ts`, `markdown.ts`), API client `services/api/romshelf.ts`.
- Save import UI: `SaveImportDialog.vue` (opened from the RomStop page, `?import=saves`) + `saveScan.ts`. Folders are read in the browser with `<input webkitdirectory>` (works over plain-HTTP LAN addresses, unlike the File System Access API); MD5 via `spark-md5` and CRC32 in JS because `crypto.subtle` needs a secure context. The dialog plans again right before uploading and uploads through RomM's own `POST /api/saves` and `/api/states` (no `overwrite`, no `slot`, no `autocleanup`), so nothing in RomM is deleted or replaced and nothing on the device is written. Not imported yet: shared memory cards (RomM's `/api/memory-cards` takes only its streaming broker's zip layout) and folder saves.
- Tests: `romm/tests/` (`test_matching`, `test_archives`, `test_modpatch`, `test_patch_source`, `test_counters`, `test_saved`, `test_saves`); run `python3 -m unittest discover romm/tests` (60 tests, 1 skipped when 7-Zip is not installed). The frontend and the image build are only verified in CI.
- Behavior to preserve: RomM never sends the user to an outside site for a patch (server fetches it; manual drop is the fallback); installed hacks get a working EmulatorJS core from `hack.play.emulatorjs` unless the user chose one; update badges and a version picker for installed hacks.

## Windows 11 installer (`romm/windows/`)

- `Install-RomM.ps1`: enables WSL2 (restart code 3010, resumes by itself via a RunOnce registry entry / `-Resume`), installs Docker Desktop, writes a docker-compose project (`RomM` + `mariadb:11`) and `.env` (keeps existing passwords on re-run, so it doubles as repair/upgrade), pulls `ghcr.io/itsdinkd/romstop-romm:latest`, starts RomM (default `http://localhost:8080`) and creates shortcuts. Options: `-LibraryDir`, `-Port`, `-ScanWorkers` (`SCAN_WORKERS`, default 8), `-SettingsFile` (JSON, deleted after reading), `-DryRun`.
- `Update-RomM.ps1` (pulls the newest image, or rebuilds `romstop-romm:local` from the repo), `Uninstall-RomM.ps1` (`-RemoveData`; never touches ROMs), `RomM-RomStop.iss` (Inno Setup wrapper, builds `RomM-RomStop-Setup.exe` in CI). Published by `release.yml` as a GitHub release asset.
- Constraint: scripts must run in Windows PowerShell 5.1, and native programs that write to stderr must not abort the script (`Invoke-Native`; CI tests this). CI lints with PSScriptAnalyzer and dry-runs the installer.
- The owner's PC is Windows (Legion Go 2). The installer is exercised only in CI; real-machine behavior is unverified from this repo.

## Names kept from before the rename

The project was called Romshelf. Internal names were kept so existing installs keep working: the `/api/romshelf` endpoints, the `handler/romshelf/` modules and their `romshelf/` data folder in RomM resources, Vue component names, and the `ROMSHELF_*` environment variables (still read when `ROMSTOP_*` are unset). Anything a user sees says RomStop. Hacks installed before the rename are tagged `[Romshelf]` and stay in a "Romshelf" collection; both are still recognized.
