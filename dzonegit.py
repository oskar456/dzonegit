#!/usr/bin/env python3

import sys
import subprocess
import re
from collections import namedtuple
from hashlib import sha256
from pathlib import Path


class HookException(ValueError):
    """Exception raised when there is an error in input data.

    Attribures:
        message -- the cause of problem
        fname -- affected file
        stderr -- output of the specific checker
    """

    def __init__(self, message, fname=None, stderr=None):
        self.message = message
        self.fname = fname
        self.stderr = stderr

    def __str__(self):
        r = list()
        if self.fname:
            r.append(f"{self.fname}: ")
        r.append(self.message)
        r.append("\n")
        if self.stderr:
            r.append("\n")
            r.append(self.stderr)
            r.append("\n\n")
        return "".join(r)


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
            stderr=subprocess.PIPE
            )
    if r.returncode != 0:
        raise HookException("Whitespace errors", r.stderr)


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
    if r.stdout:
        return (Path(p) for p in r.stdout.rstrip("\0").split("\0"))
    else:
        return list()


def get_zone_origin(zonedata):
    """
    Parse $ORIGIN directive before the SOA record.
    Return zone name without the trailing dot.
    """
    for line in zonedata.splitlines():
        if re.match(r"^[^\s;]+\s+([0-9]+\s+)?(IN\s+)?SOA\s+", line, re.I):
            break
        m = re.match(r"^\$ORIGIN\s+([^ ]+)\.\s*(;.*)?$", line, re.I)
        if m:
            return m.group(1).lower()


def get_zone_name(path, zonedata):
    """
    Try to guess zone name from either filename or the first $ORIGIN.
    Throw a HookException if filename and zone ORIGIN differ more than
    in slashes.
    """
    stemname = Path(path).stem.lower()
    originname = get_zone_origin(zonedata)
    if originname:
        tt = str.maketrans("", "", "/_,:-+*%^&#$")
        sn, on = [s.translate(tt) for s in [stemname, originname]]
        if sn != on:
            raise HookException(f"Zone origin {originname} differs from "
                                "zone file.", fname=path)
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
            raise HookException("New zone version does not compile",
                                f, rnew.stderr)
        try:
            zonedata = get_file_contents(f, against)
            zname = get_zone_name(f, zonedata)
            rold = compile_zone(zname, zonedata)

            if (rold.success and rold.zonehash != rnew.zonehash and not
                    is_serial_increased(rold.serial, rnew.serial)):
                raise HookException("Zone contents changed without "
                                    "increasing serial", fname=f)
        except subprocess.CalledProcessError:
            pass    # Old version of zone did not exist


def pre_commit():
    against = get_head()
    try:
        check_whitespace_errors(against)
        check_updated_zones(against)
    except HookException as e:
        print(e)
        raise SystemExit(1)


def main():
    name = Path(sys.argv[0]).name
    print(name)
    if name == "pre-commit":
        pre_commit()
    else:
        sys.exit("No valid command found")


if __name__ == "__main__":
    main()
