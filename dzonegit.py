#!/usr/bin/env python3

import os
import sys
import subprocess
import shlex
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


def get_head(empty=False):
    if not empty:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return r.stdout.decode("ascii").strip()
    # Initial commit: diff against an empty tree object
    return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def check_whitespace_errors(against, revision=None):
    if revision:
        cmd = ["git", "diff-tree", "--check", against, revision, "*.zone"]
    else:
        cmd = ["git", "diff-index", "--check", "--cached", against, "*.zone"]
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


def unixtime_directive(zonedata, unixtime=None):
    """ Filter binary zone data. Replace $UNIXTIME with current unix time. """
    if unixtime is None:
        unixtime = int(time.time())
    return re.sub(
        br'\$UNIXTIME\b',
        str(unixtime).encode("ascii"),
        zonedata,
        flags=re.IGNORECASE,
    )


def check_missing_trailing_dot(zonename, compiled_zonedata):
    badlines = []
    for line in compiled_zonedata.splitlines():
        if re.search(
                r"\sPTR\s+[^\s]*\.{}.$".format(zonename).encode("ascii"),
                line,
                re.I,
        ):
            badlines.append(line.decode("utf-8"))
    if badlines:
        raise HookException(
            "Possibly missing trailing dot after PTR records:\n{}".format(
                "\n".join(badlines),
            ),
            fname=zonename,
        )


def compile_zone(zonename, zonedata, unixtime=None, missing_dot=False):
    """ Compile the zone. Return tuple with results."""
    CompileResults = namedtuple(
        "CompileResults", "success, serial, zonehash, stderr",
    )
    r = subprocess.run(
        ["/usr/bin/env", "named-compilezone", "-o", "-", zonename, "/dev/stdin"],
        input=unixtime_directive(zonedata, unixtime),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr = r.stderr.decode("utf-8")
    m = re.search(r"^zone.*loaded serial ([0-9]*)$", stderr, re.MULTILINE)
    if r.returncode == 0 and m:
        serial = m.group(1)
        if missing_dot:
            check_missing_trailing_dot(zonename, r.stdout)
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
    cmd = ["git", "diff", "--name-only", "-z", "--no-renames"]
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
    Unless disabled, throw a HookException if filename and zone ORIGIN differ
    more than in slashes.
    """
    stemname = Path(path).stem.lower()
    originname = get_zone_origin(zonedata)
    if originname:
        tt = str.maketrans("", "", "/_,:-+*%^&#$")
        sn, on = [s.translate(tt) for s in [stemname, originname]]
        if sn != on and not get_config("dzonegit.allowfancynames", bool):
            raise HookException(
                "Zone origin {o} differs from zone file.".format(o=originname),
                fname=path,
            )
        return originname
    else:
        return stemname


def check_updated_zones(
        against,
        revision=None,
        autoupdate_serial=False,
        missing_dot=False,
):
    """ Check whether all updated zone files compile. """
    unixtime = int(time.time())
    for f in get_altered_files(against, "AMCR", revision):
        if not f.suffix == ".zone":
            continue
        print("Checking file {f}".format(f=f))
        zonedata = get_file_contents(f, revision)
        zname = get_zone_name(f, zonedata)
        rnew = compile_zone(zname, zonedata, unixtime, missing_dot)
        if not rnew.success:
            raise HookException(
                "New zone version does not compile",
                f, rnew.stderr,
            )
        try:
            zonedata = get_file_contents(f, against)
            zname = get_zone_name(f, zonedata)
            rold = compile_zone(zname, zonedata, unixtime-1)

            if (rold.success and rold.zonehash != rnew.zonehash and not
                    is_serial_increased(rold.serial, rnew.serial)):
                errmsg = "Zone contents changed without increasing serial."
                diagmsg = "Old revision {}, serial {}, new serial {}".format(
                    against, rold.serial, rnew.serial,
                )

                if autoupdate_serial:
                    newserial = get_increased_serial(rnew.serial)
                    if replace_serial(f, rnew.serial, newserial):
                        errmsg += " Serial has been automatically increased."
                        errmsg += " Check and recommit."
                    else:
                        errmsg += " Autoupdate of serial number failed."
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
        return False
    path.write_text(updated)
    return True


def get_zone_wildcards(name):
    """ A generator of wildcards out of a zone name.
    For a DNS name, returns series of:
     - the name itself
     - the name with first label substitued as *
     - the name with first label dropped and second substittuted as *
     - ...
     - single *
"""
    yield name
    labels = name.split(".")
    while labels:
        labels[0] = "*"
        yield ".".join(labels)
        labels.pop(0)


def template_config(checkoutpath, template, blacklist=set(), whitelist=set()):
    """ Recursively find all *.zone files and template config file using
    a simple JSON based template like this:

    {
      "header": "# Managed by dzonegit, do not edit.\n",
      "footer": "",
      "item": " - zone: \"$zonename\"\n   file: \"$zonefile\"\n   $zonevar\n",
      "defaultvar": "template: default",
      "zonevars": {
        "example.com": "template: signed",
        "*.com": "template: dotcom",
        "*": "template: uberdefault"
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
    zones = dict()
    mapping = {"datetime": datetime.datetime.now().strftime("%c")}
    if headertpl.template:
        out.append(headertpl.substitute(mapping))
    for f in sorted(Path(checkoutpath).glob("**/*.zone")):
        zonename = get_zone_name(f, f.read_bytes())
        if whitelist and not any(
                n in whitelist
                for n in get_zone_wildcards(zonename)
        ):
            print(
                "WARNING: Ignoring zone {} - not whitelisted for "
                "this repository.".format(zonename),
            )
            continue
        if any(n in blacklist for n in get_zone_wildcards(zonename)):
            print(
                "WARNING: Ignoring zone {} - blacklisted for "
                "this repository.".format(zonename),
            )
            continue
        if zonename in zones:
            print(
                "WARNING: Duplicate zone file found for zone {}. "
                "Using file {}, ignoring {}.".format(
                    zonename, zones[zonename],
                    f.relative_to(checkoutpath),
                ),
            )
            continue
        zones[zonename] = f.relative_to(checkoutpath)
        for name in get_zone_wildcards(zonename):
            if name in zonevars:
                zonevar = zonevars[name]
                break
        else:
            zonevar = defaultvar
        out.append(itemtpl.substitute(
            mapping, zonename=zonename,
            zonefile=str(f), zonerelfile=str(f.relative_to(checkoutpath)), zonevar=zonevar,
        ))
    if footertpl.template:
        out.append(footertpl.substitute(mapping))
    return "\n".join(out)


def load_set_file(path):
    if path is None:
        return set()
    with open(path) as inf:
        return {
            l.strip() for l in inf
            if not l.strip().startswith("#") and len(l) > 1
        }


def do_commit_checks(
        against,
        revision=None,
        autoupdate_serial=False,
        missing_dot=False,
):
    try:
        if not get_config("dzonegit.ignorewhitespaceerrors", bool):
            check_whitespace_errors(against, revision=revision)
        check_updated_zones(
            against, revision=revision,
            autoupdate_serial=autoupdate_serial,
            missing_dot=missing_dot,
        )
    except HookException as e:
        print(e)
        raise SystemExit(1)


def pre_commit():
    against = get_head()
    autoupdate_serial = not get_config("dzonegit.noserialupdate", bool)
    missing_dot = not get_config("dzonegit.nomissingdotcheck", bool)
    do_commit_checks(
        against,
        autoupdate_serial=autoupdate_serial,
        missing_dot=missing_dot,
    )


def update(argv=sys.argv):
    if "GIT_DIR" not in os.environ:
        raise SystemExit("Don't run this hook from the command line")
    if len(argv) < 4:
        raise SystemExit(
            "Usage: {} <ref> <oldrev> <newrev>".format(argv[0]),
        )
    refname, against, revision = argv[1:4]

    if against == "0000000000000000000000000000000000000000":
        against = get_head(True)  # Empty commit

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
            against = get_head(True)  # Empty commit
        do_commit_checks(against, revision)


def post_receive(stdin=sys.stdin):
    """Checkout the repository to a path specified in the config.
    Re-generate config files using defined templates. Issue reload
    commands for modified zone files, issue reconfig command if zones were
    added or delefed.
    """
    suffixes = list(str(n) if n else "" for n in range(10))
    blacklist = load_set_file(get_config("dzonegit.zoneblacklist"))
    whitelist = load_set_file(get_config("dzonegit.zonewhitelist"))
    checkoutpath = get_config("dzonegit.checkoutpath")
    if not checkoutpath:
        raise SystemExit("Checkout path not defined. Nothing to do.")

    print("Checking out repository into {}…".format(checkoutpath))
    Path(checkoutpath).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "checkout", "-f", "master"],
        check=True,
        env=dict(os.environ, GIT_WORK_TREE=checkoutpath),
        stderr=subprocess.DEVNULL,
    )
    for s in suffixes:
        cfpath = get_config("dzonegit.conffilepath{}".format(s))
        tplpath = get_config("dzonegit.conffiletemplate{}".format(s))
        if cfpath is None or tplpath is None:
            continue
        print("Templating config file {}…".format(cfpath))
        Path(cfpath).write_text(
            template_config(
                checkoutpath,
                Path(tplpath).read_text(),
                blacklist=blacklist,
                whitelist=whitelist,
            ),
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
            against = get_head(True)  # Empty commit
        should_reconfig = [
            f for f in get_altered_files(against, "ACDRU", revision)
            if f.suffix == ".zone"
        ]
        zones_to_reload = [
            get_zone_name(f, (checkoutpath / f).read_bytes())
            for f in get_altered_files(against, "M", revision)
            if f.suffix == ".zone"
        ]
        if should_reconfig:
            print("Zone list change detected, reloading configuration")
            for s in suffixes:
                reconfigcmd = get_config("dzonegit.reconfigcmd{}".format(s))
                if reconfigcmd:
                    print("Calling {}…".format(reconfigcmd))
                    subprocess.run(reconfigcmd, shell=True)

        for z in zones_to_reload:
            for s in suffixes:
                zonereloadcmd = get_config(
                    "dzonegit.zonereloadcmd{}".format(s),
                )
                if zonereloadcmd:
                    cmd = shlex.split(zonereloadcmd)
                    cmd.append(z)
                    print("Calling {}…".format(" ".join(cmd)))
                    subprocess.run(cmd)


def smudge_serial(
        bstdin=sys.stdin.buffer,
        bstdout=sys.stdout.buffer,
        unixtime=None,
):
    """Replace all $UNIXTIME directives with current unix time."""
    bstdout.write(unixtime_directive(bstdin.read(), unixtime))


def get_action(argv=sys.argv):
    name = Path(argv[0]).name
    if "pre-commit" in name:
        return pre_commit
    if "update" in name:
        return update
    if "pre-receive" in name:
        return pre_receive
    if "post-receive" in name:
        return post_receive
    if "smudge" in name:
        return smudge_serial


def main():
    action = get_action()
    if action is None and len(sys.argv) > 1:
        sys.argv.pop(0)
        action = get_action()
    if action:
        action()
    else:
        sys.exit("No valid command found")


if __name__ == "__main__":
    main()
