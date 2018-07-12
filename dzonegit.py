#!/usr/bin/env python3

import os
import sys
import subprocess
import re
import time
import datetime
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
            r.append("{fname}: ".format(fname=self.fname))
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
    )
    if r.returncode == 0:
        return r.stdout.decode("utf-8").strip()
    else:
        # Initial commit: diff against an empty tree object
        return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def check_whitespace_errors(against, revision=None):
    if revision:
        cmd = ["git", "diff-tree", "--check", against, revision]
    else:
        cmd = ["git", "diff-index", "--check", "--cached", against]
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if r.returncode != 0:
        raise HookException(
            "Whitespace errors",
            stderr=r.stdout.decode("utf-8"),
        )


def check_tree_whitespace_errors(tree1, tree2):
    r = subprocess.run(
        ["git", "diff-tree", "--check", tree1, tree2],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if r.returncode != 0:
        raise HookException(
            "Whitespace errors",
            stderr=r.stdout.decode("utf-8"),
        )


def get_file_contents(path, revision=None):
    """ Return contents of a file in staged env or in some revision. """
    revision = "" if revision is None else revision
    r = subprocess.run(
        ["git", "show", "{r}:{p}".format(r=revision, p=path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return r.stdout


def compile_zone(zonename, zonedata):
    """ Compile the zone. Return tuple with results."""
    CompileResults = namedtuple(
        "CompileResults", "success, serial, zonehash, stderr",
    )
    r = subprocess.run(
        ["/usr/sbin/named-compilezone", "-o", "-", zonename, "/dev/stdin"],
        input=zonedata,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr = r.stderr.decode("utf-8")
    m = re.search(r"^zone.*loaded serial ([0-9]*)$", stderr, re.MULTILINE)
    if r.returncode == 0 and m:
        serial = m.group(1)
        zonehash = sha256(r.stdout).hexdigest()
        return CompileResults(True, serial, zonehash, stderr)
    else:
        return CompileResults(False, None, None, stderr)


def is_serial_increased(old, new):
    """ Return true if serial number was increased using RFC 1982 logic. """
    old, new = (int(n) for n in [old, new])
    diff = (new - old) % 2**32
    return 0 < diff < (2**31 - 1)


def get_increased_serial(old):
    """ Return increased serial number, automatically recognizing the type. """
    old = int(old)
    now = int(time.time())
    todayserial = int(datetime.date.today().strftime("%Y%m%d00"))
    # Note to my future self: This is expected to break on 2034-06-16
    # as unix timestamp will become in the same range as YYMMDDnn serial
    if 1e9 < old < now:
        # Serial is unix timestamp
        return str(now)
    elif 2e9 < old < todayserial:
        # Serial is YYYYMMDDnn, updated before today
        return str(todayserial)
    else:
        # No pattern recognized, just increase the number
        return str(old + 1)


def get_altered_files(against, diff_filter=None, revision=None):
    """ Return list of changed files.
        If revision is None, list changes between staging area and
        revision. Otherwise differences between two revisions are computed.
    """
    cmd = ["git", "diff", "--name-only", "-z"]
    if diff_filter:
        cmd.append("--diff-filter={}".format(diff_filter))
    if revision:
        cmd.append(against)
        cmd.append(revision)
    else:
        cmd.append("--cached")
        cmd.append(against)

    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    if r.stdout:
        return (Path(p)
                for p in r.stdout.decode("utf-8").rstrip("\0").split("\0"))
    else:
        return list()


def get_zone_origin(zonedata):
    """
    Parse $ORIGIN directive before the SOA record.
    Return zone name without the trailing dot.
    """
    for line in zonedata.splitlines():
        if re.match(br"^[^\s;]+\s+([0-9]+\s+)?(IN\s+)?SOA\s+", line, re.I):
            break
        m = re.match(br"^\$ORIGIN\s+([^ ]+)\.\s*(;.*)?$", line, re.I)
        if m:
            return m.group(1).decode("utf-8").lower()


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
            raise HookException(
                "Zone origin {o} differs from zone file.".format(o=originname),
                fname=path,
            )
        return originname
    else:
        return stemname


def check_updated_zones(against, revision=None):
    """ Check whether all updated zone files compile. """
    for f in get_altered_files(against, "AM", revision):
        if not f.suffix == ".zone":
            continue
        print("Checking file {f}".format(f=f))
        zonedata = get_file_contents(f, revision)
        zname = get_zone_name(f, zonedata)
        rnew = compile_zone(zname, zonedata)
        if not rnew.success:
            raise HookException(
                "New zone version does not compile",
                f, rnew.stderr,
            )
        try:
            zonedata = get_file_contents(f, against)
            zname = get_zone_name(f, zonedata)
            rold = compile_zone(zname, zonedata)

            if (rold.success and rold.zonehash != rnew.zonehash and not
                    is_serial_increased(rold.serial, rnew.serial)):
                errmsg = "Old revision {}, serial {}, new serial {}".format(
                    against, rold.serial, rnew.serial,
                )
                raise HookException(
                    "Zone contents changed without increasing serial",
                    fname=f,
                    stderr=errmsg,
                )
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


def update():
    if "GIT_DIR" not in os.environ:
        raise SystemExit("Don't run this hook from command line")
    if len(sys.argv) < 4:
        raise SystemExit(
            "Usage: {} <ref> <oldrev> <newrev>".format(sys.argv[0]),
        )
    refname, against, revision = sys.argv[1:4]

    if refname != "refs/heads/master":
        raise SystemExit("Nothing else except master branch is accepted here")
    try:
        check_whitespace_errors(against, revision)
        check_updated_zones(against, revision)
    except HookException as e:
        print(e)
        raise SystemExit(1)


def main():
    name = Path(sys.argv[0]).name
    print(name)
    if name == "pre-commit":
        pre_commit()
    elif name == "update":
        update()
    else:
        sys.exit("No valid command found")


if __name__ == "__main__":
    main()
