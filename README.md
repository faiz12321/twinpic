# TwinPic

Find photos that look the same. Keep everything safe.

TwinPic looks through your folders and finds photos you have twice:
exact copies, and the same picture saved smaller or re-compressed.
It shows you every group first. Extra copies are moved into a holding
folder, never deleted, and one click puts everything back.

Free and open source (MIT). Runs on your own folders. No account,
no upload, no internet needed. Your photos never leave your computer.

## Use it

Double-click TwinPic.exe. A window opens in your browser - it is still
only this computer; the page talks to the app, never to the internet.

1. Type or paste the folder to look through, then "Find twin photos".
2. Look through the groups. The biggest copy is marked KEEP;
   pick a different keeper with "keep this one instead".
3. "Move extras aside" puts the extra copies into a holding folder.
   "Put everything back (Undo)" restores them exactly.

From the command line the same things work too:

```
python run.py scan <folder> [more folders...]     # look, change nothing
python run.py aside <folder> --holding <dir>      # move extras aside
python run.py undo --holding <dir>                # put everything back
```

Options: `--threshold N` (how alike is alike, 0-64, default 5),
`--include-cloud` (also scan OneDrive/Dropbox folders; off by default).

## How it works

- Exact twins are found by SHA-256 (same bytes).
- Look-alikes are found with two perceptual hashes (a horizontal and a
  vertical dHash) plus an overall brightness check, so plain-colour
  images and banded scenes do not false-match.
- The window is a small local page served on 127.0.0.1 with a fresh
  random key every time TwinPic starts (and a Host-header check), so no
  other program or tab can drive it.
- Scanning never changes anything, and TwinPic holding folders are
  left out of scans. Moves go only into the holding folder, with a
  manifest that grows one line per moved file, and Undo restores every
  file byte-for-byte. Identical byte-copies always keep at least one
  copy in place. A look-alike that exists as a single file moves aside
  like any extra - that is the point - and Undo brings it back. Files
  that changed since the scan are skipped, never moved, and the keeper
  of each group is re-checked before anything in that group moves.

## Honest limits

- Finds the same picture in different files: copies, resized versions,
  re-compressed versions. It does not reliably catch edited photos
  (brightened, cropped, filtered). It errs on the careful side.
- Comfortable up to a few thousand photos per scan. A speed-up pass
  for very large libraries is planned, not built yet.
- Skips OneDrive/Dropbox folders unless you opt in, because moving
  files there can surprise other devices. If you do opt in with
  --include-cloud, reading online-only placeholder files may download
  them first.
- HEIC/HEIF photos (iPhone format) are not read yet and are skipped.
- Flat, detail-free pictures (solid colours, plain scans) only match on
  identical bytes - near-identical flat pictures are left alone.
- The flip side: pictures that only look alike (graphics, emoji, plain
  sky or sea shots) can still pair up by mistake. Always glance at the
  groups before moving.
- The exe is not code-signed yet, so Windows SmartScreen may warn on
  first run ("Windows protected your PC" -> "More info" -> "Run anyway").

## Tests

```
python -m unittest discover -s tests -v
python tests/smoke_exe.py "python run.py"
```

## Licenses

TwinPic is MIT licensed (see LICENSE). It uses Pillow (HPND license)
and CPython (PSF license), and the built exe carries the PyInstaller
bootloader (GPL with a special exception that permits bundled
programs) - see THIRD-PARTY.md.
