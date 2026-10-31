# RomStop: instructions for Claude

RomStop is the public half of the RomStop project: the RomM add-on (`romm/`). It reads the hack index (`<site>/device/index.json`, `format: 1`) that the RomStop site publishes. The site, the hack data, the scrapers and the code that writes the index live in a separate private repo (itsdinkd/Romshelf). Keep site code out of this repo.

## How to work in this repo

1. Read `docs/architecture.md` before exploring source. Source code wins over docs; fix the doc in the same PR.
2. A change to the index shape needs two PRs: the site repo writes it, this repo reads it. Adding optional fields is safe; breaking changes bump the format on both sides.
3. Mark anything you did not verify as "unverified".

## Commands (verified 2026-10-31)

```sh
python3 -m unittest discover romm/tests  # RomM add-on backend helpers
docker build -t romstop-romm romm        # the RomM image (CI builds and smoke-tests it on PRs)
```

CI: `romm-image.yml` (tests, builds the image; pushes `ghcr.io/itsdinkd/romstop-romm` on main), `romm-installer.yml` (lints and dry-runs the Windows installer, builds `RomM-RomStop-Setup.exe`), `release.yml` (on main: publishes the installer as a GitHub release). The Windows installer is only built and tested in CI.

`romm/romm.patch` and `romm/overlay/` are generated from a RomM checkout at `romm/ROMM_VERSION`: `romm/apply.sh <checkout>`, edit, `romm/export-from-tree.sh <checkout>`. Re-apply main's overlay before exporting, or you revert other work.

## Standing rules (from the owner)

- **Naming:** never write the owner's real name or first name anywhere in the repo, commits, code comments, test strings or PRs. Use **itsdinkd**.
- **Public names:** anything a user sees says RomStop. Internal names from the Romshelf days stay (see "Names kept" in docs/architecture.md).
- **No ROMs, ever.** Never host, download, commit or link-to-fetch ROMs. Base ROMs come only from the user's own RomM library.
- **Never run an `.exe`** or any program from a downloaded archive. Downloads holding a program are refused.
- **One-click patching:** RomM install gets the patch themselves (mirror first, then the source file) and never send the user to another site.
- **Emulator compatibility notes** must show in RomM.
- Never ask for API keys or secrets in chat or write them in the repo.

## Git and PRs

- Develop on the branch you were given; open a PR to `main`. Merging to `main` publishes the image and a release, so nothing broken may land there.
- Merge PRs with a merge commit.
- Before saying a change is done: `python3 -m unittest discover romm/tests` passes, and docs are updated if behavior changed.

The ES-DE/Knulli tool (`device/`) was removed for now (owner's choice, 2026-10-31); it is in this repo's history if it comes back.
