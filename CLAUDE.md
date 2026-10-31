# RomStop: instructions for Claude

RomStop is the public half of the RomStop project: the RomM add-on (`romm/`) and the ES-DE/Knulli tool (`device/`). Both read the hack index (`<site>/device/index.json`, `format: 1`) that the RomStop site publishes. The site, the hack data, the scrapers and the code that writes the index live in a separate private repo (itsdinkd/Romshelf). Keep site code out of this repo.

## How to work in this repo

1. Read `docs/architecture.md` before exploring source. Source code wins over docs; fix the doc in the same PR.
2. A change to the index shape needs two PRs: the site repo writes it, this repo reads it. Adding optional fields is safe; breaking changes bump the format on both sides.
3. Mark anything you did not verify as "unverified".

## Commands (verified 2026-10-31)

```sh
python3 -m unittest discover device      # ES-DE/Knulli tool (pillow needed only for the covers test)
python3 -m unittest discover romm/tests  # RomM add-on backend helpers
docker build -t romstop-romm romm        # the RomM image (CI builds and smoke-tests it on PRs)
```

CI: `device.yml` (tests on Linux + Windows, builds `romstop.exe`), `romm-image.yml` (tests, builds the image; pushes `ghcr.io/itsdinkd/romstop-romm` on main), `romm-installer.yml` (lints and dry-runs the Windows installer, builds `RomM-RomStop-Setup.exe`), `release.yml` (on main: publishes the exe, `romstop.py`, the three drag-and-drop zips and the installer as a GitHub release). The Windows installer and exe are only built and tested in CI.

`romm/romm.patch` and `romm/overlay/` are generated from a RomM checkout at `romm/ROMM_VERSION`: `romm/apply.sh <checkout>`, edit, `romm/export-from-tree.sh <checkout>`. Re-apply main's overlay before exporting, or you revert other work.

## Standing rules (from the owner)

- **Naming:** never write the owner's real name or first name anywhere in the repo, commits, code comments, test strings or PRs. Use **itsdinkd**.
- **Public names:** anything a user sees says RomStop. Internal names from the Romshelf days stay (see "Names kept" in docs/architecture.md).
- **No ROMs, ever.** Never host, download, commit or link-to-fetch ROMs. Base ROMs come only from the user's own files (RomM library, device ROM folders).
- **Never run an `.exe`** or any program from a downloaded archive. Downloads holding a program are refused.
- **One-click patching:** RomM install and the device tool get the patch themselves (mirror first, then the source file) and never send the user to another site.
- **Emulator compatibility notes** must show in RomM and on ES-DE/Knulli.
- Never ask for API keys or secrets in chat or write them in the repo.

## Git and PRs

- Develop on the branch you were given; open a PR to `main`. Merging to `main` publishes the image and a release, so nothing broken may land there.
- Merge PRs with a merge commit.
- Before saying a change is done: both Python test suites pass, and docs are updated if behavior changed.
