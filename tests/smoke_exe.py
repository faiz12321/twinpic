"""Smoke test for the built program:
python tests/smoke_exe.py <path-to-exe-or-'python run.py'>"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

cmd = shlex.split(sys.argv[1], posix=(os.name != "nt"))
base = os.path.realpath(tempfile.mkdtemp(prefix="smoke",
                                         dir=os.environ.get("TWINPIC_TEST_TMP")))
try:
    from PIL import Image
    os.makedirs(os.path.join(base, "pics"))
    Image.new("RGB", (16, 16), (200, 120, 40)).save(
        os.path.join(base, "pics", "one.png"))
    shutil.copy(os.path.join(base, "pics", "one.png"),
                os.path.join(base, "pics", "two.png"))
    Image.new("RGB", (16, 16), (20, 60, 200)).save(
        os.path.join(base, "pics", "three.png"))
    out = subprocess.run(cmd + ["scan", os.path.join(base, "pics")],
                         capture_output=True, text=True, timeout=120)
    print(out.stdout, out.stderr)
    assert out.returncode == 0 and "twin groups" in out.stdout, "scan failed"
    proc = subprocess.Popen(cmd + ["ui", "--no-browser"],
                            stdout=subprocess.PIPE, text=True)
    url = None
    end = time.time() + 60
    while time.time() < end:
        line = proc.stdout.readline()
        if line.startswith("http://127.0.0.1:"):
            url = line.strip()
            break
    assert url, "ui did not print its link"
    html = urllib.request.urlopen(url, timeout=15).read().decode()
    assert "TwinPic" in html and "__TOKEN__" not in html
    try:
        urllib.request.urlopen(url.split("?")[0], timeout=15)
        raise SystemExit("page opened without the key")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    proc.terminate()
    proc.wait(20)
    print("SMOKE OK")
finally:
    shutil.rmtree(base, True)
