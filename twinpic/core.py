"""TwinPic core: find photos that look the same, keep everything safe.

Two passes:
  1. Exact twins  - same bytes (SHA-256).
  2. Look-alikes  - same picture, different file (resized, recompressed),
     found with two perceptual hashes (a horizontal and a vertical dHash),
     an overall brightness check, and anchor-only comparison so groups
     cannot chain together unrelated photos.

Safety rules (same family as FolderEcho):
  - Scanning never changes anything, and skips TwinPic holding folders.
  - "Move aside" moves non-keepers into a holding folder; nothing is
    deleted. Undo puts every file back byte-for-byte.
  - The holding folder's manifest is one line per moved file, appended
    as each move lands, so an interrupted run still knows exactly where
    every moved file is.
  - Identical byte-copies: at least one copy always stays in place.
    A look-alike that exists as a single file moves aside like any
    extra - that is the product's job - and Undo brings it back.
  - A move only touches files the scan actually showed, and only while
    they are unchanged (size, mtime and hash re-checked first).
  - Cloud-synced folders (OneDrive, Dropbox) are skipped unless you opt in.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
CLOUD_MARKERS = ("onedrive", "dropbox")
MANIFEST_NAME = "twinpic-manifest.json"
MANIFEST_VERSION = 1
MIN_THRESHOLD, MAX_THRESHOLD = 0, 64

# Two pictures only "look the same" if their overall brightness is also close.
MEAN_TOLERANCE = 12.0
# A picture whose tiny grayscale version barely varies is "flat" (a solid
# colour, a plain scan). Flat pictures match only on identical bytes.
FLAT_RANGE = 8

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


class ManifestExistsError(RuntimeError):
    """The holding folder already has a TwinPic manifest."""


class ManifestError(RuntimeError):
    """The holding folder's manifest is missing, unreadable or damaged."""


@dataclass
class Photo:
    path: str
    size: int
    mtime: float
    sha256: str | None = None      # exact hash (None if unreadable)
    dhash: int | None = None       # horizontal perceptual hash
    dhash_v: int | None = None     # vertical perceptual hash
    mean: float | None = None      # mean luminance 0-255 (perceptual pass)
    flat: bool = False             # almost no detail; exact matches only


@dataclass
class Group:
    kind: str                       # "exact" or "similar"
    keeper: str                     # path suggested to keep (largest file)
    members: list = field(default_factory=list)  # all paths incl. keeper


@dataclass
class ScanResult:
    groups: list = field(default_factory=list)
    singles: int = 0
    unreadable: list = field(default_factory=list)
    cloud_skipped: list = field(default_factory=list)
    scanned: int = 0
    stats: dict = field(default_factory=dict)   # path -> (size, mtime) at scan
    hashes: dict = field(default_factory=dict)  # path -> sha256 at scan


def _is_cloud(path: str) -> bool:
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep)]
    return any(any(seg.startswith(m) for m in CLOUD_MARKERS) for seg in parts)


def check_threshold(threshold: int) -> int:
    if not isinstance(threshold, int) or isinstance(threshold, bool) \
            or not MIN_THRESHOLD <= threshold <= MAX_THRESHOLD:
        raise ValueError(
            f"threshold must be a number from {MIN_THRESHOLD} to "
            f"{MAX_THRESHOLD}")
    return threshold


def sha256_of(path: str, _chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def dhash_of(path: str, hash_size: int = 8):
    """Two 64-bit difference hashes, mean luminance and a flat flag.

    Horizontal hash: shrink to (hash_size+1) x hash_size grayscale, set a
    bit where the left pixel is brighter than its right neighbour.
    Vertical hash: the same idea top-to-bottom, so banded scenes (sky,
    sea, striped graphics) that share a horizontal profile but are
    different pictures do not pair up.
    Returns (horizontal bits, vertical bits, mean, flat).
    """
    if Image is None:
        raise RuntimeError("Pillow is required")
    with Image.open(path) as im:
        im = im.convert("L")
        h_im = im.resize((hash_size + 1, hash_size), Image.LANCZOS)
        v_im = im.resize((hash_size, hash_size + 1), Image.LANCZOS)
    h_px = h_im.tobytes()  # one byte per pixel in "L" mode
    v_px = v_im.tobytes()
    h_bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = h_px[row * (hash_size + 1) + col]
            right = h_px[row * (hash_size + 1) + col + 1]
            h_bits = (h_bits << 1) | (1 if left > right else 0)
    v_bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            top = v_px[row * hash_size + col]
            bottom = v_px[(row + 1) * hash_size + col]
            v_bits = (v_bits << 1) | (1 if top > bottom else 0)
    return h_bits, v_bits, sum(h_px) / len(h_px), \
        (max(h_px) - min(h_px)) <= FLAT_RANGE


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _looks_same(a: Photo, b: Photo, threshold: int) -> bool:
    """Same picture? Both hash directions must be close, and brightness
    too. Flat pictures never look-the-same; they match bytes only."""
    if a.flat or b.flat:
        return False
    if abs(a.mean - b.mean) > MEAN_TOLERANCE:
        return False
    return hamming(a.dhash, b.dhash) <= threshold \
        and hamming(a.dhash_v, b.dhash_v) <= threshold


def _anchor_cluster(photos: list, threshold: int) -> list:
    """Group photo indexes: same bytes first, then anchor-only look-alikes.

    Each candidate is compared with a cluster's ANCHOR (its first photo),
    never transitively - A~B and B~C does not put C with A. Flat pictures
    join exact-byte groups only.
    """
    by_sha: dict[str, list[int]] = {}
    for i, p in enumerate(photos):
        by_sha.setdefault(p.sha256, []).append(i)
    exact_groups = sorted(by_sha.values(), key=lambda ix: photos[ix[0]].path)

    clusters = []  # list of (anchor_index, [exact_group_index, ...])
    for gi, idxs in enumerate(exact_groups):
        a = photos[idxs[0]]
        placed = False
        for anchor_i, members in clusters:
            if _looks_same(a, photos[anchor_i], threshold):
                members.append(gi)
                placed = True
                break
        if not placed:
            clusters.append((idxs[0], [gi]))
    return [
        [i for gi in members for i in exact_groups[gi]]
        for _anchor, members in clusters
    ]


def scan(folders, threshold: int = 5, include_cloud: bool = False) -> ScanResult:
    """Read-only scan. Returns groups of twin photos; changes nothing.

    TwinPic holding folders (any folder containing a manifest) are skipped,
    so a second scan never re-processes files that were moved aside.
    """
    check_threshold(threshold)
    result = ScanResult()
    photos: list[Photo] = []
    for root_folder in folders:
        for dirpath, dirs, files in os.walk(root_folder):
            if MANIFEST_NAME in files:
                dirs[:] = []  # a holding folder: leave it alone
                continue
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
                    continue
                path = os.path.join(dirpath, name)
                if not include_cloud and _is_cloud(path):
                    result.cloud_skipped.append(path)
                    continue
                try:
                    st = os.stat(path)
                except OSError:
                    result.unreadable.append(path)
                    continue
                photos.append(Photo(path=path, size=st.st_size,
                                    mtime=st.st_mtime))
                result.stats[path] = (st.st_size, st.st_mtime)
    result.scanned = len(photos)

    # Pass 1: hashes
    readable: list[Photo] = []
    for p in photos:
        try:
            p.sha256 = sha256_of(p.path)
        except OSError:
            result.unreadable.append(p.path)
            continue
        readable.append(p)
    decodable: list[Photo] = []
    for p in readable:
        try:
            p.dhash, p.dhash_v, p.mean, p.flat = dhash_of(p.path)
        except Exception:
            result.unreadable.append(p.path)
            continue
        result.hashes[p.path] = p.sha256
        decodable.append(p)

    # Pass 2: anchor-only clustering (same bytes first)
    for members_idx in _anchor_cluster(decodable, threshold):
        if len(members_idx) < 2:
            result.singles += 1
            continue
        members = sorted((decodable[i] for i in members_idx),
                         key=lambda p: (-p.size, p.path))
        exact = len({p.sha256 for p in members}) == 1
        result.groups.append(Group(
            kind="exact" if exact else "similar",
            keeper=members[0].path,
            members=[p.path for p in members],
        ))
    result.groups.sort(key=lambda g: g.keeper)
    return result


def _unchanged(path: str, size: int, mtime: float) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_size == size and abs(st.st_mtime - mtime) < 1e-6


def _same_hash(path: str, sha: str | None) -> bool:
    if sha is None:
        return True
    try:
        return sha256_of(path) == sha
    except OSError:
        return False


def _as_scanned(path: str, result: ScanResult) -> bool:
    """Still exactly the file the scan showed? Size, mtime and hash."""
    size, mtime = result.stats.get(path, (None, None))
    if size is None or not _unchanged(path, size, mtime):
        return False
    return _same_hash(path, result.hashes.get(path))


def _move_file(src: str, dst: str):
    """Move one file, same drive or across drives. Never loses data:
    a cross-drive move is copy, verify the copy's hash, then remove."""
    try:
        os.replace(src, dst)
        return
    except OSError:
        pass  # different drive or filesystem rules: do it carefully
    shutil.copy2(src, dst)
    if sha256_of(dst) != sha256_of(src):
        os.remove(dst)
        raise OSError(f"copy did not verify: {src}")
    os.remove(src)


def _unique_dest(holding_folder: str, basename: str, n: int) -> str:
    """A destination name inside the holding folder that is guaranteed
    not to overwrite anything already there."""
    stem, ext = os.path.splitext(basename)
    candidate = os.path.join(holding_folder, f"{n:05d}-{basename}")
    extra = 2
    while os.path.exists(candidate):
        candidate = os.path.join(
            holding_folder, f"{n:05d}-{stem}-{extra}{ext}")
        extra += 1
    return candidate


# --- manifest: one JSON line per moved file, appended as moves land ----

def _manifest_path(holding_folder: str) -> str:
    return os.path.join(holding_folder, MANIFEST_NAME)


def _start_manifest(holding_folder: str):
    """Create the manifest with its header line. Exclusive create: an
    existing manifest is never overwritten."""
    line = json.dumps({"twinpic": MANIFEST_VERSION,
                       "created": time.time(),
                       "holding": os.path.abspath(holding_folder)})
    with open(_manifest_path(holding_folder), "x") as f:
        f.write(line + "\n")


def _append_move(holding_folder: str, src: str, dst: str):
    line = json.dumps({"from": os.path.abspath(src),
                       "to": os.path.abspath(dst)})
    with open(_manifest_path(holding_folder), "a") as f:
        f.write(line + "\n")


def _rewrite_manifest(holding_folder: str, moves: list):
    """Replace the manifest with the moves still outstanding (used as an
    Undo makes progress). Written atomically, so it is never half-written."""
    tmp = _manifest_path(holding_folder) + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps({"twinpic": MANIFEST_VERSION,
                            "created": time.time(),
                            "holding": os.path.abspath(holding_folder)})
                + "\n")
        for mv in moves:
            f.write(json.dumps(mv) + "\n")
    os.replace(tmp, _manifest_path(holding_folder))


def _read_manifest(holding_folder: str):
    """(header, moves) from a holding folder's manifest.

    Raises FileNotFoundError when there is no manifest, ManifestError for
    anything unreadable or malformed. A cut-short last line (crash during
    an append) is ignored - the move it described simply did not complete.
    """
    damaged = ManifestError(
        f"The TwinPic manifest in {holding_folder} is damaged. The moved "
        "files are still in that folder; move them back by hand.")
    try:
        with open(_manifest_path(holding_folder)) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        raise
    except OSError as e:
        raise ManifestError(
            f"TwinPic could not read the manifest in {holding_folder} "
            f"({e.strerror or e}). The moved files are still in that "
            "folder.")
    if not lines:
        raise damaged
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError:
        raise damaged
    if not isinstance(header, dict) \
            or header.get("twinpic") != MANIFEST_VERSION:
        raise ManifestError(
            f"The file in {holding_folder} does not look like a TwinPic "
            "manifest. The files there are untouched.")
    moves = []
    for i, line in enumerate(lines[1:], start=2):
        try:
            mv = json.loads(line)
        except json.JSONDecodeError:
            if i == len(lines):
                break  # crash mid-append: that move never completed
            raise damaged
        if not isinstance(mv, dict) or not isinstance(mv.get("from"), str) \
                or not isinstance(mv.get("to"), str):
            raise damaged
        moves.append(mv)
    return header, moves


def _protected_keepers(result: ScanResult, group: Group) -> set:
    """Paths that must stay in place: the chosen keeper, plus one copy of
    every distinct byte-version in the group, so a move never strips the
    last in-place copy of any identical-byte set."""
    keep = {group.keeper}
    by_sha: dict[str, list[str]] = {}
    for path in group.members:
        sha = result.hashes.get(path)
        if sha:
            by_sha.setdefault(sha, []).append(path)
    for paths in by_sha.values():
        if group.keeper in paths or len(paths) < 2:
            # the keeper is this version's in-place copy, or this version
            # has a single file: moving it aside is the product's job,
            # and Undo can bring it back.
            continue
        # identical bytes in several files: one copy stays in place,
        # so a move never strips the last in-place copy of a photo.
        keep.add(max(paths,
                     key=lambda p: (result.stats.get(p, (0, 0))[0], p)))
    return keep


def move_aside(result: ScanResult, holding_folder: str,
               keepers=None, moves=None) -> dict:
    """Move non-keepers into holding_folder. Deletes nothing.

    keepers: chosen keeper paths, at most one per group, each a member of
    its group in the scan (anything else is a ValueError). moves: the
    exact list of files to move; every one must be a file the scan
    showed (anything else is a ValueError). With moves=None every
    non-protected member goes.

    Before any file in a group moves, that group's keeper and protected
    copies are re-checked (size, mtime and hash); if one changed or
    vanished, the whole group is left alone and reported. The manifest is
    created before the first move and one line is appended per moved
    file, so an interrupted run (full disk, pulled cable) still knows
    where every moved file went. One file failing never stops the
    others; it is reported instead. At least one copy of every
    identical-byte set stays in place.
    """
    manifest_path = _manifest_path(holding_folder)
    if os.path.exists(manifest_path):
        raise ManifestExistsError(
            f"{holding_folder} already holds a TwinPic holding folder. "
            "Undo it first, or choose another holding folder.")
    os.makedirs(holding_folder, exist_ok=True)

    # Keeper choices: one per group, and it must be in that group.
    chosen: dict[int, str] = {}
    for path in (keepers or []):
        for gi, g in enumerate(result.groups):
            if path in g.members:
                if gi in chosen:
                    raise ValueError(
                        "each group gets exactly one keeper")
                chosen[gi] = path
                break
        else:
            raise ValueError(f"keeper was not in the scanned preview: "
                             f"{path}")
    for gi, path in chosen.items():
        result.groups[gi].keeper = path

    # The move list must come from the scanned preview, and only from it.
    member_paths = {p for g in result.groups for p in g.members}
    if moves is None:
        wanted = None
    else:
        wanted = set(moves)
        for p in moves:
            if p not in member_paths:
                raise ValueError(f"not in the scanned preview: {p}")

    moved, skipped, unverified = [], [], []
    _start_manifest(holding_folder)
    n = 0
    for g in result.groups:
        keep = _protected_keepers(result, g)
        # The keeper and protected copies must still be there, unchanged,
        # before anything in this group moves.
        group_ok = all(_as_scanned(path, result) for path in keep)
        for path in g.members:
            if path in keep or (wanted is not None and path not in wanted):
                continue
            if not group_ok:
                unverified.append(path)
                continue
            if not _as_scanned(path, result):
                skipped.append(path)
                continue
            dest = _unique_dest(holding_folder, os.path.basename(path), n)
            n += 1
            try:
                _move_file(path, dest)
            except OSError:
                skipped.append(path)
                continue
            _append_move(holding_folder, path, dest)
            moved.append(path)
    return {"moved": moved, "skipped_changed_or_missing": skipped,
            "skipped_unverified": unverified, "manifest": manifest_path}


def undo(holding_folder: str) -> dict:
    """Put every moved file back where it came from, byte-for-byte.

    Works across drives (the same verified-copy move as "move aside").
    A file already back at its original place counts as done, so an
    interrupted Undo can simply be run again. The manifest shrinks as
    Undo makes progress, and is removed once everything is back.
    """
    _header, moves = _read_manifest(holding_folder)
    restored, problems, remaining = [], [], []
    for mv in moves:
        src, dst = mv["to"], mv["from"]
        if os.path.exists(dst) and not os.path.exists(src):
            continue  # already put back by an earlier Undo
        if not os.path.exists(src):
            problems.append(src + " (not in the holding folder)")
            remaining.append(mv)
            continue
        if os.path.exists(dst):
            problems.append(dst + " (a file already sits there)")
            remaining.append(mv)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _move_file(src, dst)
        except OSError as e:
            problems.append(f"{dst} ({e.strerror or e})")
            remaining.append(mv)
            continue
        restored.append(dst)
    if problems:
        _rewrite_manifest(holding_folder, remaining)
    else:
        try:
            os.remove(_manifest_path(holding_folder))
        except FileNotFoundError:
            pass
    return {"restored": restored, "problems": problems}
