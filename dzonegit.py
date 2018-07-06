#!/usr/bin/env python3

import subprocess
import re
from collections import namedtuple
from hashlib import sha256
from pathlib import Path


def get_head():
    r = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8"
            )
    if r.returncode == 0:
        return r.stdout.strip()
    else:
        # Initial commit: diff against an empty tree object
        return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def check_whitespace_errors(against):
    r = subprocess.run(
            ["git", "diff-index", "--check", "--cached", against],
            )
    if r.returncode != 0:
        raise ValueError("Whitespace errors")


def get_file_contents(path, revision=""):
    """ Return contents of a file in staged env or in some revision. """
    r = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            check=True
            )
    return r.stdout


def compile_zone(zonename, zonedata):
    """ Compile the zone. Return tuple with results."""
    CompileResults = namedtuple("CompileResults", "success, serial, "
                                "zonehash, stderr")
    r = subprocess.run(
            ["/usr/sbin/named-compilezone", "-o", "-", zonename, "/dev/stdin"],
            input=zonedata,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            )
    m = re.search(r"^zone.*loaded serial ([0-9]*)$", r.stderr, re.MULTILINE)
    if r.returncode == 0 and m:
        serial = m.group(1)
        zonehash = sha256(r.stdout.encode("utf-8")).hexdigest()
        return CompileResults(True, serial, zonehash, r.stderr)
    else:
        return CompileResults(False, None, None, r.stderr)


def is_serial_increased(old, new):
    """ Return true if serial number was increased using RFC 1982 logic. """
    old, new = (int(n) for n in [old, new])
    diff = (new - old) % 2**32
    return 0 < diff < (2**31 - 1)


def get_altered_files(against, diff_filter=None):
    """ Return list of changed files. """
    cmd = ["git", "diff", "--cached", "--name-only", "-z"]
    if diff_filter:
        cmd.append(f"--diff-filter={diff_filter}")
    cmd.append(against)
    r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            check=True
            )
    return (Path(p) for p in r.stdout.rstrip("\0").split("\0"))


def get_zone_origin(zonedata, maxlines=10):
    """
    Parse $ORIGIN directive in first maxlines lines of zone file.
    Return zone name without the trailing dot.
    """
    for i, line in enumerate(zonedata.splitlines()):
        if i >= maxlines:
            break
        m = re.match(r"^\$ORIGIN\s+([^ ]+)\.\s*(;.*)?$", line)
        if m:
            return m.group(1).lower()


def get_zone_name(path, zonedata):
    """
    Try to guess zone name from either filename or the first $ORIGIN.
    Throw a ValueError if filename and zone ORIGIN differ more than
    in slashes.
    """
    stemname = Path(path).stem.lower()
    originname = get_zone_origin(zonedata)
    if originname:
        tt = str.maketrans("", "", "/_,:-+*%^&#$")
        sn, on = [s.translate(tt) for s in [stemname, originname]]
        if sn != on:
            raise ValueError('Zone origin and zone file name differ.',
                             originname, stemname)
        return originname
    else:
        return stemname


def check_updated_zones(against):
    """ Check whether all updated zone files compile. """
    for f in get_altered_files(against, "AM"):
        if not f.suffix == ".zone":
            continue
        print(f"Checking file {f}")
        zonedata = get_file_contents(f)
        zname = get_zone_name(f, zonedata)
        rnew = compile_zone(zname, zonedata)
        if not rnew.success:
            raise ValueError("New zone version does not compile", str(f),
                             rnew.stderr)
        try:
            zonedata = get_file_contents(f, against)
            zname = get_zone_name(f, zonedata)
            rold = compile_zone(zname, zonedata)

            if (rold.success and rold.zonehash != rnew.zonehash and not
                    is_serial_increased(rold.serial, rnew.serial)):
                raise ValueError("Zone contents changed without "
                                 "increasing serial", f)
        except subprocess.CalledProcessError:
            pass    # Old version of zone did not exist


def main():
    against = get_head()
    try:
        check_whitespace_errors(against)
        check_updated_zones(against)
    except ValueError as e:
        print("\n".join(e.args))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
