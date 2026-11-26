"""TwinPic command line.

  python run.py                 (opens the window - the normal way)
  python run.py scan <folder> [folder...] [--threshold N] [--include-cloud]
  python run.py aside <folder> [folder...] --holding <dir> [same options]
  python run.py undo --holding <dir>
  python run.py ui [--no-browser] [--port N]
"""

import argparse
import os
import sys

from . import core


def _check_folders(folders) -> list:
    return [f for f in folders if not os.path.isdir(f)]


def _scan_or_message(args):
    """Run the scan; a bad threshold or folder is a message, not a crash."""
    bad = _check_folders(args.folders)
    if bad:
        print(f"folder not found: {bad[0]}", file=sys.stderr)
        return None
    try:
        return core.scan(args.folders, threshold=args.threshold,
                         include_cloud=args.include_cloud)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return None


def _cmd_scan(args) -> int:
    res = _scan_or_message(args)
    if res is None:
        return 2
    print(f"Scanned {res.scanned} photos.")
    if res.cloud_skipped:
        print(f"Skipped {len(res.cloud_skipped)} photos in cloud-synced folders "
              "(use --include-cloud if you really want those).")
    if res.unreadable:
        print(f"Could not read {len(res.unreadable)} files (skipped):")
        for p in res.unreadable:
            print(f"  - {p}")
    if not res.groups:
        print("No twin photos found. Your folders look clean.")
        return 0
    n_extra = sum(len(g.members) - 1 for g in res.groups)
    print(f"Found {len(res.groups)} twin groups ({n_extra} extra copies):")
    for g in res.groups:
        label = "same bytes" if g.kind == "exact" else "look the same"
        print(f"\n  {label} - keep: {g.keeper}")
        for m in g.members:
            if m != g.keeper:
                print(f"      extra: {m}")
    if res.singles:
        print(f"\n({res.singles} photos have no twins.)")
    return 0


def _cmd_aside(args) -> int:
    res = _scan_or_message(args)
    if res is None:
        return 2
    if not res.groups:
        print("No twin photos found. Nothing to move.")
        return 0
    try:
        out = core.move_aside(res, args.holding)
    except core.ManifestExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except core.ManifestError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Moved {len(out['moved'])} extra copies into {args.holding}.")
    print("Nothing was deleted. To put everything back:")
    print(f"  python run.py undo --holding \"{args.holding}\"")
    if out["skipped_changed_or_missing"]:
        print("Skipped (changed or missing since scan):")
        for p in out["skipped_changed_or_missing"]:
            print(f"  - {p}")
    if out["skipped_unverified"]:
        print("Left in place (their group's keeper changed since scan - "
              "scan again to refresh):")
        for p in out["skipped_unverified"]:
            print(f"  - {p}")
    return 0


def _cmd_undo(args) -> int:
    try:
        out = core.undo(args.holding)
    except FileNotFoundError:
        print(f"no TwinPic holding folder found at {args.holding}",
              file=sys.stderr)
        return 1
    except core.ManifestError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Put back {len(out['restored'])} photos.")
    if out["problems"]:
        print("Some could not be put back:")
        for p in out["problems"]:
            print(f"  - {p}")
        return 1
    return 0


def _cmd_ui(args) -> int:
    from . import ui
    return ui.serve(port=args.port, open_browser=not args.no_browser)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="twinpic",
        description="Find photos that look the same. "
                    "Move extras aside safely; undo anytime.")
    sub = ap.add_subparsers(dest="cmd")

    def add_scan_flags(p):
        p.add_argument("--threshold", type=int, default=5,
                       help="how alike is alike (0 = identical hash, "
                            "5 = default, up to 64)")
        p.add_argument("--include-cloud", action="store_true",
                       help="also scan OneDrive/Dropbox folders "
                            "(off by default)")

    p_scan = sub.add_parser("scan", help="look for twin photos "
                                         "(changes nothing)")
    p_scan.add_argument("folders", nargs="+")
    add_scan_flags(p_scan)
    p_scan.set_defaults(fn=_cmd_scan)

    p_aside = sub.add_parser("aside", help="move extra copies into "
                                           "a holding folder")
    p_aside.add_argument("folders", nargs="+")
    p_aside.add_argument("--holding", required=True)
    add_scan_flags(p_aside)
    p_aside.set_defaults(fn=_cmd_aside)

    p_undo = sub.add_parser("undo", help="put everything back from "
                                         "a holding folder")
    p_undo.add_argument("--holding", required=True)
    p_undo.set_defaults(fn=_cmd_undo)

    p_ui = sub.add_parser("ui", help="open the TwinPic window")
    p_ui.add_argument("--no-browser", action="store_true",
                      help="do not open a browser tab, just print the link")
    p_ui.add_argument("--port", type=int, default=0,
                      help="port to listen on (0 = pick a free one)")
    p_ui.set_defaults(fn=_cmd_ui)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args(["ui"])
    return args.fn(args)
