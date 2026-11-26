import json
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw
from twinpic import core


def make_photo(path, seed, size=(64, 64)):
    """A deterministic 'photo': gradient + shapes, unique per seed."""
    rng = random.Random(seed)
    im = Image.new("RGB", size)
    dr = ImageDraw.Draw(im)
    base = rng.randint(0, 200)
    for y in range(size[1]):
        dr.line([(0, y), (size[0], y)], fill=(base + y % 55, base, 255 - base))
    for _ in range(6):
        x0, y0 = rng.randint(0, 40), rng.randint(0, 40)
        dr.ellipse([x0, y0, x0 + rng.randint(5, 20), y0 + rng.randint(5, 20)],
                   fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    im.save(path)
    return path


def make_stripes(path, period, size=(64, 64)):
    """Horizontal stripes: uniform per row, banded top to bottom."""
    im = Image.new("RGB", size)
    dr = ImageDraw.Draw(im)
    for y in range(size[1]):
        v = 220 if (y // period) % 2 == 0 else 40
        dr.line([(0, y), (size[0], y)], fill=(v, v, v))
    im.save(path)
    return path


class TwinPicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="twinpic-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def test_exact_copy_groups(self):
        a = make_photo(self.p("a.jpg"), seed=1)
        shutil.copy(a, self.p("a-copy.jpg"))
        make_photo(self.p("other.jpg"), seed=2)
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        g = res.groups[0]
        self.assertEqual(g.kind, "exact")
        self.assertEqual(len(g.members), 2)
        self.assertEqual(res.singles, 1)

    def test_resized_recompressed_groups_similar(self):
        make_photo(self.p("big.png"), seed=7, size=(128, 128))
        Image.open(self.p("big.png")).resize((40, 40)).save(
            self.p("small.jpg"), quality=70)
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        self.assertEqual(res.groups[0].kind, "similar")

    def test_different_photos_do_not_group(self):
        for i in range(4):
            make_photo(self.p(f"p{i}.png"), seed=100 + i)
        res = core.scan([self.tmp])
        self.assertEqual(res.groups, [])
        self.assertEqual(res.singles, 4)

    def test_corrupt_file_is_listed_not_fatal(self):
        make_photo(self.p("ok1.png"), seed=5)
        shutil.copy(self.p("ok1.png"), self.p("ok2.png"))
        with open(self.p("broken.jpg"), "wb") as f:
            f.write(b"this is not a jpeg")
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        self.assertEqual(len(res.unreadable), 1)
        self.assertIn("broken.jpg", res.unreadable[0])

    def test_cloud_folder_skipped_by_default(self):
        os.makedirs(self.p("OneDrive - Test"))
        make_photo(self.p("OneDrive - Test", "c1.png"), seed=9)
        make_photo(self.p("OneDrive - Test", "c2.png"), seed=9)
        res = core.scan([self.tmp])
        self.assertEqual(res.scanned, 0)
        self.assertEqual(len(res.cloud_skipped), 2)
        res2 = core.scan([self.tmp], include_cloud=True)
        self.assertEqual(res2.scanned, 2)

    def test_move_aside_and_undo_roundtrip(self):
        make_photo(self.p("m1.png"), seed=3)
        shutil.copy(self.p("m1.png"), self.p("m2.png"))
        original = {}
        for name in ("m1.png", "m2.png"):
            with open(self.p(name), "rb") as f:
                original[name] = f.read()
        res = core.scan([self.tmp])
        holding = self.p("holding")
        out = core.move_aside(res, holding)
        self.assertEqual(len(out["moved"]), 1)
        self.assertTrue(os.path.exists(out["manifest"]))
        # keeper still in place, exactly one file moved aside
        remaining = [n for n in os.listdir(self.tmp) if n.endswith(".png")]
        self.assertEqual(len(remaining), 1)
        back = core.undo(holding)
        self.assertEqual(back["problems"], [])
        for name in ("m1.png", "m2.png"):
            with open(self.p(name), "rb") as f:
                self.assertEqual(f.read(), original[name])

    def test_changed_file_is_never_moved(self):
        make_photo(self.p("x1.png"), seed=11)
        shutil.copy(self.p("x1.png"), self.p("x2.png"))
        res = core.scan([self.tmp])
        keeper = res.groups[0].keeper
        extra = [m for m in res.groups[0].members if m != keeper][0]
        # simulate the file changing between scan and move
        make_photo(extra, seed=99)
        out = core.move_aside(res, self.p("holding"))
        self.assertEqual(out["moved"], [])
        self.assertEqual(out["skipped_changed_or_missing"], [extra])
        self.assertTrue(os.path.exists(extra))

    def test_plain_colour_images_do_not_false_match(self):
        from PIL import Image as PImage
        PImage.new("RGB", (16, 16), (200, 120, 40)).save(self.p("orange.png"))
        PImage.new("RGB", (16, 16), (20, 60, 200)).save(self.p("blue.png"))
        PImage.new("RGB", (16, 16), (250, 250, 250)).save(self.p("white.png"))
        res = core.scan([self.tmp])
        self.assertEqual(res.groups, [])
        self.assertEqual(res.singles, 3)

    def test_keeper_is_largest(self):
        make_photo(self.p("large.png"), seed=21, size=(96, 96))
        Image.open(self.p("large.png")).resize((32, 32)).save(self.p("small.png"))
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        self.assertTrue(res.groups[0].keeper.endswith("large.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SafetyRegressionTests(unittest.TestCase):
    """One test per finding from the independent reviews."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="twinpic-safety-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    # --- round 1 ------------------------------------------------------
    def test_scan_skips_holding_folders(self):
        # CRITICAL 1: a holding folder inside a scanned folder must be
        # left alone, so a re-scan can never strip the last copy.
        make_photo(self.p("keep.png"), seed=1)
        os.makedirs(self.p("TwinPic holding"))
        shutil.copy(self.p("keep.png"), self.p("TwinPic holding", "00000-x.png"))
        with open(self.p("TwinPic holding", core.MANIFEST_NAME), "w") as f:
            f.write('{"twinpic": 1}\n')
        res = core.scan([self.tmp])
        self.assertEqual(res.scanned, 1)
        self.assertEqual(res.groups, [])

    def test_move_aside_refuses_existing_manifest(self):
        # CRITICAL 1b: never overwrite an existing manifest.
        make_photo(self.p("a.png"), seed=1)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        holding = self.p("holding")
        os.makedirs(holding)
        with open(os.path.join(holding, core.MANIFEST_NAME), "w") as f:
            f.write('{"twinpic": 1}\n')
        with self.assertRaises(core.ManifestExistsError):
            core.move_aside(res, holding)

    def test_manifest_survives_a_mid_move_failure(self):
        # CRITICAL 2: the manifest grows one line per move; a mid-move
        # error must not strand files - Undo still knows the ones moved.
        for n in ("a.png", "b.png", "c.png"):
            make_photo(self.p(n), seed=5)
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        holding = self.p("holding")
        real_move = core._move_file
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated disk full")
            return real_move(src, dst)

        import twinpic.core as c
        c._move_file = flaky
        try:
            out = c.move_aside(res, holding)
        finally:
            c._move_file = real_move
        self.assertEqual(len(out["moved"]), 1)
        self.assertEqual(len(out["skipped_changed_or_missing"]), 1)
        # the manifest holds exactly the one completed move
        with open(os.path.join(holding, core.MANIFEST_NAME)) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)  # header + one move line
        back = core.undo(holding)
        self.assertEqual(back["problems"], [])
        self.assertEqual(len(back["restored"]), 1)

    def test_cross_drive_move_falls_back_to_verified_copy(self):
        # CRITICAL 2b: os.replace failing (cross-drive) must still move
        # safely: copy, hash-verify, remove.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        import twinpic.core as c
        real_move = c._move_file

        def move_with_exdev(src, dst):
            try:
                raise OSError(18, "Invalid cross-device link")
            except OSError:
                pass
            shutil.copy2(src, dst)
            if c.sha256_of(dst) != c.sha256_of(src):
                os.remove(dst)
                raise OSError("copy did not verify")
            os.remove(src)

        c._move_file = move_with_exdev
        try:
            out = c.move_aside(res, self.p("holding"))
        finally:
            c._move_file = real_move
        self.assertEqual(len(out["moved"]), 1)
        back = c.undo(self.p("holding"))
        self.assertEqual(back["problems"], [])

    def test_anchor_clustering_does_not_chain(self):
        # HIGH 1: A~B and B~C must NOT merge C into A's group when A!~C.
        def photo(path, bits, mean):
            return core.Photo(path=path, size=1, mtime=0, sha256=path,
                              dhash=bits, dhash_v=0, mean=mean, flat=False)
        A = photo("A", 0b000000, 100.0)
        B = photo("B", 0b011100, 100.0)   # 3 bits from A
        C = photo("C", 0b111111, 100.0)   # 3 bits from B, 6 from A
        clusters = core._anchor_cluster([A, B, C], threshold=5)
        self.assertEqual(sorted(len(c) for c in clusters), [1, 2])

    def test_flat_photos_match_exact_bytes_only(self):
        # HIGH 1b: two different flat photos with near-identical
        # brightness must not group; their exact copies still do.
        from PIL import Image as PImage
        PImage.new("RGB", (32, 32), (200, 120, 40)).save(self.p("f1.png"))
        PImage.new("RGB", (32, 32), (190, 115, 45)).save(self.p("f2.png"))
        shutil.copy(self.p("f1.png"), self.p("f1-copy.png"))
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        self.assertEqual(res.groups[0].kind, "exact")
        self.assertEqual(sorted(res.groups[0].members),
                         sorted([self.p("f1.png"), self.p("f1-copy.png")]))

    def test_move_keeps_one_copy_of_every_byte_version(self):
        # HIGH 1c: even inside one "similar" group, a move never strips
        # the last in-place copy of any identical-byte set.
        make_photo(self.p("big.png"), seed=7, size=(128, 128))
        from PIL import Image as PImage
        PImage.open(self.p("big.png")).resize((40, 40)).save(
            self.p("small.png"))
        shutil.copy(self.p("small.png"), self.p("small-copy.png"))
        res = core.scan([self.tmp])
        self.assertEqual(len(res.groups), 1)
        self.assertEqual(res.groups[0].kind, "similar")
        core.move_aside(res, self.p("holding"))
        remaining = [n for n in os.listdir(self.tmp) if n.endswith(".png")]
        # big.png (keeper) and one of the small pair must stay
        self.assertIn("big.png", remaining)
        small_left = [n for n in remaining if n.startswith("small")]
        self.assertEqual(len(small_left), 1)

    def test_corrupt_manifest_is_a_clean_error(self):
        # LOW: damaged manifest must not raise a raw traceback.
        holding = self.p("holding")
        os.makedirs(holding)
        with open(os.path.join(holding, core.MANIFEST_NAME), "w") as f:
            f.write("{ not json")
        with self.assertRaises(core.ManifestError):
            core.undo(holding)
        with open(os.path.join(holding, core.MANIFEST_NAME), "w") as f:
            f.write('{"unexpected": true}')
        with self.assertRaises(core.ManifestError):
            core.undo(holding)

    def test_cli_missing_folder_errors(self):
        # MEDIUM 2: CLI scan of a missing folder must fail loudly.
        from twinpic import cli
        rc = cli.main(["scan", self.p("does-not-exist")])
        self.assertNotEqual(rc, 0)

    def test_license_names_contributors(self):
        # HIGH 2: the license credits the project, never a person.
        with open(os.path.join(os.path.dirname(__file__), "..", "LICENSE")) as f:
            text = f.read()
        self.assertIn("TwinPic contributors", text)

    # --- round 2 ------------------------------------------------------
    def test_cross_drive_undo_restores_with_verified_copy(self):
        # HIGH 2: Undo must work when the holding folder is on another
        # drive (os.replace failing) - copy, hash-verify, remove.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        holding = self.p("holding")
        core.move_aside(res, holding)
        real_replace = os.replace

        def exdev(src, dst):
            if str(dst).endswith(".png"):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(src, dst)

        os.replace = exdev
        try:
            back = core.undo(holding)
        finally:
            os.replace = real_replace
        self.assertEqual(back["problems"], [])
        self.assertEqual(len(back["restored"]), 1)
        # the file is really back, byte-for-byte
        with open(self.p("a.png"), "rb") as f:
            restored_bytes = f.read()
        with open(self.p("b.png"), "rb") as f:
            self.assertEqual(restored_bytes, f.read())

    def test_move_refuses_paths_outside_the_preview(self):
        # HIGH 3: a move may only touch files the scan showed. A file
        # added after the preview must be refused, never moved.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        shutil.copy(self.p("a.png"), self.p("late.png"))  # after the scan
        with self.assertRaises(ValueError):
            core.move_aside(res, self.p("h1"),
                            moves=[self.p("late.png")])
        # an unknown keeper is refused too
        with self.assertRaises(ValueError):
            core.move_aside(res, self.p("h2"),
                            keepers=[self.p("late.png")])
        # and two keepers for one group are refused
        with self.assertRaises(ValueError):
            core.move_aside(res, self.p("h3"),
                            keepers=[self.p("a.png"), self.p("b.png")])

    def test_explicit_moves_and_keepers_are_honoured(self):
        # HIGH 3b: with an explicit list, exactly those files move.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        out = core.move_aside(res, self.p("holding"),
                              keepers=[self.p("b.png")],
                              moves=[self.p("a.png")])
        self.assertEqual(out["moved"], [self.p("a.png")])
        self.assertTrue(os.path.exists(self.p("b.png")))
        self.assertFalse(os.path.exists(self.p("a.png")))

    def test_deleted_keeper_blocks_the_whole_group(self):
        # MEDIUM 1: if the keeper (or a protected copy) changed or
        # vanished since the scan, nothing in that group moves.
        for n in ("a.png", "b.png", "c.png"):
            make_photo(self.p(n), seed=5)
        res = core.scan([self.tmp])
        keeper = res.groups[0].keeper
        os.remove(keeper)
        out = core.move_aside(res, self.p("holding"))
        self.assertEqual(out["moved"], [])
        self.assertEqual(len(out["skipped_unverified"]), 2)
        # the other two copies are untouched
        left = [n for n in os.listdir(self.tmp) if n.endswith(".png")]
        self.assertEqual(len(left), 2)

    def test_holding_collision_never_overwrites(self):
        # MEDIUM 2: a file already in the holding folder is never
        # overwritten; the moved file gets a fresh name instead.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        holding = self.p("holding")
        os.makedirs(holding)
        sentinel = os.path.join(holding, "00000-b.png")
        with open(sentinel, "wb") as f:
            f.write(b"someone else's file - hands off")
        out = core.move_aside(res, holding)
        self.assertEqual(len(out["moved"]), 1)
        with open(sentinel, "rb") as f:
            self.assertEqual(f.read(), b"someone else's file - hands off")
        back = core.undo(holding)
        self.assertEqual(back["problems"], [])

    def test_undo_is_repeatable_after_a_partial_restore(self):
        # MEDIUM 3: a file already back at its original place counts as
        # done, so re-running Undo finishes the job cleanly.
        for n in ("a.png", "b.png", "c.png"):
            make_photo(self.p(n), seed=5)
        res = core.scan([self.tmp])
        keeper = res.groups[0].keeper
        extras = [m for m in res.groups[0].members if m != keeper]
        holding = self.p("holding")
        out = core.move_aside(res, holding)
        self.assertEqual(len(out["moved"]), 2)
        # simulate an interrupted earlier Undo: one file is back already
        _header, moves = core._read_manifest(holding)
        first = moves[0]
        shutil.move(first["to"], first["from"])
        back = core.undo(holding)
        self.assertEqual(back["problems"], [])
        self.assertEqual(len(back["restored"]), 1)
        for n in ("a.png", "b.png", "c.png"):
            self.assertTrue(os.path.exists(self.p(n)))
        # manifest is gone: nothing left to undo
        self.assertFalse(os.path.exists(
            os.path.join(holding, core.MANIFEST_NAME)))

    def test_banded_pictures_do_not_group(self):
        # MEDIUM 5: striped graphics share a horizontal profile but are
        # different pictures; the vertical hash keeps them apart.
        make_stripes(self.p("stripes8.png"), period=8)
        make_stripes(self.p("stripes16.png"), period=16)
        res = core.scan([self.tmp])
        self.assertEqual(res.groups, [])
        self.assertEqual(res.singles, 2)

    def test_vertical_hash_separates_same_horizontal(self):
        # MEDIUM 5b: same horizontal hash, different vertical hash must
        # not group even when brightness matches.
        def photo(path, h, v, mean):
            return core.Photo(path=path, size=1, mtime=0, sha256=path,
                              dhash=h, dhash_v=v, mean=mean, flat=False)
        a = photo("a", 0, 0b00000000, 100.0)
        b = photo("b", 0, 0b11111111, 100.0)
        clusters = core._anchor_cluster([a, b], threshold=5)
        self.assertEqual(sorted(len(c) for c in clusters), [1, 1])
        # but a true twin (both hashes close) still groups
        c = photo("c", 0b000011, 0b00000011, 102.0)
        clusters = core._anchor_cluster([a, c], threshold=5)
        self.assertEqual(sorted(len(x) for x in clusters), [2])

    def test_threshold_range_is_enforced(self):
        # LOW: out-of-range thresholds are clean errors everywhere.
        make_photo(self.p("a.png"), seed=5)
        with self.assertRaises(ValueError):
            core.scan([self.tmp], threshold=65)
        with self.assertRaises(ValueError):
            core.scan([self.tmp], threshold=-1)
        from twinpic import cli
        rc = cli.main(["scan", self.tmp, "--threshold", "99"])
        self.assertEqual(rc, 2)

    def test_odd_manifest_entries_are_clean_errors(self):
        # LOW: malformed manifest lines are a message, not a traceback.
        holding = self.p("holding")
        os.makedirs(holding)
        mpath = os.path.join(holding, core.MANIFEST_NAME)
        with open(mpath, "w") as f:
            f.write('{"twinpic": 1}\n123\n')
        with self.assertRaises(core.ManifestError):
            core.undo(holding)
        with open(mpath, "w") as f:
            f.write('{"twinpic": 1}\n{"from": "x"}\n')
        with self.assertRaises(core.ManifestError):
            core.undo(holding)
        # an unreadable manifest path is a message too
        os.remove(mpath)
        os.mkdir(mpath)
        with self.assertRaises(core.ManifestError):
            core.undo(holding)

    def test_crash_cut_manifest_line_is_ignored(self):
        # LOW: a crash mid-append leaves a cut last line; the completed
        # moves before it must still undo cleanly.
        make_photo(self.p("a.png"), seed=5)
        shutil.copy(self.p("a.png"), self.p("b.png"))
        res = core.scan([self.tmp])
        holding = self.p("holding")
        core.move_aside(res, holding)
        with open(os.path.join(holding, core.MANIFEST_NAME), "a") as f:
            f.write('{"from": "x", "to"')  # cut short by the "crash"
        back = core.undo(holding)
        self.assertEqual(back["problems"], [])
        self.assertEqual(len(back["restored"]), 1)
