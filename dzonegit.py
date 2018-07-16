#!/usr/bin/env python3

import os
import sys
import subprocess
import re
import time
import datetime
import json
from collections import namedtuple
from hashlib import sha256
from pathlib import Path
from string import Template


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


def check_updated_zones(against, revision=None, autoupdate_serial=False):
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
                errmsg = "Zone contents changed without increasing serial."
                diagmsg = "Old revision {}, serial {}, new serial {}".format(
                    against, rold.serial, rnew.serial,
                )

                if autoupdate_serial:
                    newserial = get_increased_serial(rnew.serial)
                    replace_serial(f, rnew.serial, newserial)
                    errmsg += " Serial has been automatically increased."
                    errmsg += " Check and recommit."
                raise HookException(
                    errmsg,
                    fname=f,
                    stderr=diagmsg,
                )
        except subprocess.CalledProcessError:
            pass    # Old version of zone did not exist


def get_config(name, type_=None):
    cmd = ["git", "config", ]
    if type_ == bool:
        cmd.append("--bool")
    elif type_ == int:
        cmd.append("--int")
    elif type_:
        raise ValueError("Invalid type supplied")
    cmd.append(name)
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
    )
    if r.returncode != 0:
        return None
    if type_ == bool:
        return r.stdout == b"true\n"
    elif type_ == int:
        return int(r.stdout)
    else:
        return r.stdout.decode("utf-8").rstrip("\n")


def replace_serial(path, oldserial, newserial):
    contents = path.read_text()
    updated, count = re.subn(
        r'(^.*\sSOA\s.+?\s){}([^0-9])'.format(oldserial),
        r'\g<1>{}\g<2>'.format(newserial),
        contents,
        count=1,
        flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    if count != 1:
        raise HookException("Cannot update zone serial number")
    path.write_text(updated)


def template_config(checkoutpath, template):
    """ Recursively find all *.zone files and template config file using
    a simple JSON based template like this:

    {
      "header": "# Managed by dzonegit, do not edit.\n",
      "footer": "",
      "item": " - zone: \"$zonename\"\n   file: \"$zonefile\"\n   $zonevar\n",
      "defaultvar": "template: default",
      "zonevars": {
        "example.com": "template: signed"
      }
    }

    Available placeholders are:
      - $datetime - timestamp of file creation
      - $zonename - zone name, without trailing dot
      - $zonefile - full path to zone file
      - $zonevar - per-zone specific variables, content of `defaultvar` if
                   not defined for current zone
    """
    tpl = json.loads(template)
    headertpl = Template(tpl.get("header", ""))
    footertpl = Template(tpl.get("footer", ""))
    itemtpl = Template(tpl.get("item", ""))
    defaultvar = tpl.get("defaultvar", "")
    zonevars = tpl.get("zonevars", dict())
    out = list()
    zones = set()
    mapping = {"datetime": datetime.datetime.now().strftime("%c")}
    out.append(headertpl.substitute(mapping))
    for f in sorted(Path(checkoutpath).glob("**/*.zone")):
        zonename = get_zone_name(f, f.read_bytes())
        if zonename in zones:
            continue  # Safety net in case duplicate zone file is found
        zones.add(zonename)
        zonevar = zonevars[zonename] if zonename in zonevars else defaultvar
        out.append(itemtpl.substitute(
            mapping, zonename=zonename,
            zonefile=str(f), zonevar=zonevar,
        ))
    out.append(footertpl.substitute(mapping))
    return "\n".join(out)


def do_commit_checks(against, revision=None, autoupdate_serial=False):
    try:
        if not get_config("dzonegit.ignorewhitespaceerrors", bool):
            check_whitespace_errors(against, revision=revision)
        check_updated_zones(
            against, revision=revision,
            autoupdate_serial=autoupdate_serial,
        )
    except HookException as e:
        print(e)
        raise SystemExit(1)


def pre_commit():
    against = get_head()
    autoupdate_serial = not get_config("dzonegit.noserialupdate", bool)
    do_commit_checks(against, autoupdate_serial=autoupdate_serial)


def update(argv=sys.argv):
    if "GIT_DIR" not in os.environ:
        raise SystemExit("Don't run this hook from the command line")
    if len(argv) < 4:
        raise SystemExit(
            "Usage: {} <ref> <oldrev> <newrev>".format(argv[0]),
        )
    refname, against, revision = argv[1:4]

    if against == "0000000000000000000000000000000000000000":
        against = get_head()  # Empty commit

    if refname != "refs/heads/master":
        raise SystemExit("Nothing else than master branch is accepted here")
    do_commit_checks(against, revision)


def pre_receive(stdin=sys.stdin):
    if stdin.isatty():
        raise SystemExit("Don't run this hook from the command line")
    for line in stdin:
        against, revision, refname = line.rstrip().split(" ")
        if refname != "refs/heads/master":
            raise SystemExit(
                "Nothing else than master branch "
                "is accepted here",
            )
        if against == "0000000000000000000000000000000000000000":
            against = get_head()  # Empty commit
        do_commit_checks(against, revision)


def post_receive(stdin=sys.stdin):
    """Checkout the repository to a path specified in the config.
    Re-generate config files using defined templates. Issue reload
    commands for modified zone files, issue reconfig command if zones were
    added or delefed.
    """
    suffixes = list(str(n) if n else "" for n in range(10))
    checkoutpath = get_config("dzonegit.checkoutpath")
    if checkoutpath:
        print("Checking out repository into {}…".format(checkoutpath))
        subprocess.run(
            ["git", "checkout", "-f", "master"],
            check=True,
            env=dict(os.environ, GIT_WORK_TREE=checkoutpath),
        )
        for s in suffixes:
            cfpath = get_config("dzonegit.conffilepath{}".format(s))
            tplpath = get_config("dzonegit.conffiletemplate{}".format(s))
            if cfpath is None or tplpath is None:
                continue
            print("Templating config file {}…".format(cfpath))
            Path(cfpath).write_text(
                template_config(checkoutpath, Path(tplpath).read_text()),
            )

    if stdin.isatty():
        raise SystemExit(
            "Standard input should be redirected. Not issuing any reload "
            "commands.",
        )
    for line in stdin:
        against, revision, refname = line.rstrip().split(" ")
        if refname != "refs/heads/master":
            continue
        if against == "0000000000000000000000000000000000000000":
            against = get_head()  # Empty commit
        # TODO reloads


def main():
    name = Path(sys.argv[0]).name
    print(name)
    if name == "pre-commit":
        pre_commit()
    elif name == "update":
        update()
    elif name == "pre-receive":
        pre_receive()
    elif name == "post-receive":
        post_receive()
    else:
        sys.exit("No valid command found")


if __name__ == "__main__":
    main()
