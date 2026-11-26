import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from twinpic import ui
from tests.test_core import make_photo


def http_get(url_or_req):
    return urllib.request.urlopen(url_or_req, timeout=10)


def post(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    return http_get(req)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ui._Server(0, "testkey123")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="twinpic-ui-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, path):
        sep = "&" if "?" in path else "?"
        return f"{self.base}{path}{sep}key=testkey123"

    def scan(self, folder):
        return json.loads(http_get(self.url(
            "/api/scan?folders=" + urllib.parse.quote(folder))).read())

    def test_page_served_with_token_only(self):
        html = http_get(self.url("/")).read().decode()
        self.assertIn("TwinPic", html)
        self.assertNotIn("__TOKEN__", html)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(f"{self.base}/")
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(f"{self.base}/?key=wrong")
        self.assertEqual(cm.exception.code, 403)

    def test_scan_api_finds_twins(self):
        make_photo(os.path.join(self.tmp, "p1.png"), seed=42)
        shutil.copy(os.path.join(self.tmp, "p1.png"),
                    os.path.join(self.tmp, "p2.png"))
        res = self.scan(self.tmp)
        self.assertEqual(res["scanned"], 2)
        self.assertEqual(len(res["groups"]), 1)
        self.assertEqual(res["groups"][0]["kind"], "exact")

    def test_scan_api_bad_folder_is_a_clean_error(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(self.url("/api/scan?folders=" +
                              urllib.parse.quote(self.tmp + "/nope")))
        self.assertEqual(cm.exception.code, 400)

    def test_thumbnail(self):
        p = make_photo(os.path.join(self.tmp, "t.png"), seed=7)
        r = http_get(self.url("/thumb?p=" + urllib.parse.quote(p)))
        self.assertEqual(r.headers["Content-Type"], "image/jpeg")
        self.assertGreater(len(r.read()), 500)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(self.url("/thumb?p=" + urllib.parse.quote(
                p.replace(".png", ".txt"))))
        self.assertEqual(cm.exception.code, 404)

    def test_aside_and_undo_api_roundtrip_with_keeper_choice(self):
        make_photo(os.path.join(self.tmp, "a.png"), seed=3)
        shutil.copy(os.path.join(self.tmp, "a.png"),
                    os.path.join(self.tmp, "b.png"))
        self.scan(self.tmp)  # the preview the move is checked against
        holding = os.path.join(self.tmp, "holding")
        keeper = os.path.join(self.tmp, "b.png")
        out = json.loads(post(self.url("/api/aside"), {
            "holding": holding,
            "keepers": [keeper],
            "moves": [os.path.join(self.tmp, "a.png")]}).read())
        self.assertEqual(out["moved"], [os.path.join(self.tmp, "a.png")])
        self.assertTrue(os.path.exists(keeper))
        out = json.loads(post(self.url("/api/undo"),
                              {"holding": holding}).read())
        self.assertEqual(out["problems"], [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "a.png")))

    def test_post_without_key_is_forbidden(self):
        req = urllib.request.Request(f"{self.base}/api/undo",
                                     data=json.dumps(
                                         {"holding": self.tmp}).encode())
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(req)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class UiSecurityRegressionTests(unittest.TestCase):
    """Regression tests from the independent reviews."""

    @classmethod
    def setUpClass(cls):
        cls.server = ui._Server(0, "testkey456")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="twinpic-ui-sec-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, path):
        sep = "&" if "?" in path else "?"
        return f"{self.base}{path}{sep}key=testkey456"

    def scan(self, folder):
        return json.loads(http_get(self.url(
            "/api/scan?folders=" + urllib.parse.quote(folder))).read())

    def test_foreign_host_header_is_forbidden(self):
        req = urllib.request.Request(self.url("/"),
                                     headers={"Host": "evil.example.com"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(req)
        self.assertEqual(cm.exception.code, 403)

    def test_bad_threshold_is_a_clean_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            http_get(self.url("/api/scan?folders=" +
                              urllib.parse.quote(self.tmp) +
                              "&threshold=abc"))
        self.assertEqual(cm.exception.code, 400)

    def test_out_of_range_threshold_is_a_clean_400(self):
        # LOW: range check, not just "is a number".
        for bad in ("-1", "65", "9999"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                http_get(self.url("/api/scan?folders=" +
                                  urllib.parse.quote(self.tmp) +
                                  "&threshold=" + bad))
            self.assertEqual(cm.exception.code, 400)

    def test_aside_needs_a_scan_first(self):
        # HIGH 3: no re-scan at move time; without a preview there is
        # nothing a move is allowed to touch.
        fresh = ui._Server(0, "freshkey")
        t = threading.Thread(target=fresh.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{fresh.server_address[1]}/api/aside"
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post(url + "?key=freshkey",
                     {"holding": os.path.join(self.tmp, "h"),
                      "keepers": [], "moves": []})
            self.assertEqual(cm.exception.code, 400)
        finally:
            fresh.shutdown()
            fresh.server_close()

    def test_aside_refuses_paths_outside_the_preview(self):
        # HIGH 3: a file added after the scan must be refused, and a
        # group gets exactly one keeper.
        make_photo(os.path.join(self.tmp, "a.png"), seed=11)
        shutil.copy(os.path.join(self.tmp, "a.png"),
                    os.path.join(self.tmp, "b.png"))
        self.scan(self.tmp)
        late = os.path.join(self.tmp, "late.png")
        shutil.copy(os.path.join(self.tmp, "a.png"), late)  # after scan
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.url("/api/aside"),
                 {"holding": os.path.join(self.tmp, "h1"),
                  "keepers": [], "moves": [late]})
        self.assertEqual(cm.exception.code, 400)
        self.assertTrue(os.path.exists(late))  # untouched
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.url("/api/aside"),
                 {"holding": os.path.join(self.tmp, "h2"),
                  "keepers": [os.path.join(self.tmp, "a.png"),
                              os.path.join(self.tmp, "b.png")],
                  "moves": []})
        self.assertEqual(cm.exception.code, 400)

    def test_keeper_matched_by_path_not_position(self):
        # MEDIUM 1: keeper choices apply by path even with several groups.
        make_photo(os.path.join(self.tmp, "a1.png"), seed=11)
        shutil.copy(os.path.join(self.tmp, "a1.png"),
                    os.path.join(self.tmp, "a2.png"))
        make_photo(os.path.join(self.tmp, "b1.png"), seed=22)
        shutil.copy(os.path.join(self.tmp, "b1.png"),
                    os.path.join(self.tmp, "b2.png"))
        self.scan(self.tmp)
        keeper = os.path.join(self.tmp, "b2.png")
        out = json.loads(post(self.url("/api/aside"), {
            "holding": os.path.join(self.tmp, "holding"),
            "keepers": [keeper],
            "moves": [os.path.join(self.tmp, "a1.png"),
                      os.path.join(self.tmp, "b1.png")]}).read())
        self.assertTrue(os.path.exists(keeper))
        self.assertNotIn(keeper, out["moved"])

    def test_undo_damaged_manifest_is_a_clean_400(self):
        holding = os.path.join(self.tmp, "holding")
        os.makedirs(holding)
        with open(os.path.join(holding, "twinpic-manifest.json"), "w") as f:
            f.write("{ broken")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.url("/api/undo"), {"holding": holding})
        self.assertEqual(cm.exception.code, 400)

    def test_undo_missing_manifest_is_a_clean_404(self):
        holding = os.path.join(self.tmp, "holding")
        os.makedirs(holding)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(self.url("/api/undo"), {"holding": holding})
        self.assertEqual(cm.exception.code, 404)
