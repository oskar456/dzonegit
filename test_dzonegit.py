
import pytest
import subprocess
import time
import datetime
import os
from io import StringIO
from pathlib import Path

import dzonegit


@pytest.fixture(scope="session")
def git_dir(tmpdir_factory):
    d = tmpdir_factory.getbasetemp()
    d.chdir()
    subprocess.call(["git", "init"])
    return d


def test_get_head(git_dir):
    git_dir.chdir()
    assert dzonegit.get_head() == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    git_dir.join("dummy").write("dummy\n")
    subprocess.call(["git", "add", "dummy"])
    subprocess.call(["git", "commit", "-m", "dummy"])
    assert dzonegit.get_head() != "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_check_whitespace_errors(git_dir):
    git_dir.chdir()
    git_dir.join("whitespace").write(" ")
    subprocess.call(["git", "add", "whitespace"])
    with pytest.raises(ValueError):
        dzonegit.check_whitespace_errors(dzonegit.get_head())
    subprocess.call(["git", "commit", "-m", "whitespace"])
    with pytest.raises(ValueError):
        dzonegit.check_whitespace_errors("HEAD~", dzonegit.get_head())
    subprocess.call(["git", "rm", "-f", "whitespace"])
    subprocess.call(["git", "commit", "-m", "rm whitespace"])
    dzonegit.check_whitespace_errors(dzonegit.get_head())
    dzonegit.check_whitespace_errors("HEAD~", dzonegit.get_head())


def test_get_file_contents(git_dir):
    git_dir.chdir()
    assert dzonegit.get_file_contents("dummy") == b"dummy\n"
    with pytest.raises(subprocess.CalledProcessError):
        dzonegit.get_file_contents('nonexistent')


def test_compile_zone():
    testzone = b"""
$ORIGIN example.com.
@       60 IN SOA ns hostmaster (
                                1234567890 ; serial
                                3600       ; refresh (1 hour)
                                900        ; retry (15 minutes)
                                1814400    ; expire (3 weeks)
                                60         ; minimum (1 minute)
                                )
        60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
"""
    r = dzonegit.compile_zone("example.org", testzone)
    assert not r.success
    assert r.zonehash is None
    assert r.stderr
    r = dzonegit.compile_zone("example.com", testzone)
    assert r.success
    assert r.serial == "1234567890"
    assert r.zonehash
    r2 = dzonegit.compile_zone("example.com", testzone + b"\n\n; some comment")
    assert r.zonehash == r2.zonehash


def test_is_serial_increased():
    assert dzonegit.is_serial_increased(1234567890, "2018010100")
    assert dzonegit.is_serial_increased("2018010100", "4018010100")
    assert dzonegit.is_serial_increased("4018010100", "1234567890")
    assert not dzonegit.is_serial_increased(2018010100, "1234567890")
    assert not dzonegit.is_serial_increased(1, 1)


def test_get_altered_files(git_dir):
    git_dir.chdir()
    git_dir.join("dummy").write("dummy2\n")
    git_dir.join("new").write("newfile\n")
    subprocess.call(["git", "add", "dummy", "new"])
    files = set(dzonegit.get_altered_files("HEAD", "AM"))
    assert files == {Path("dummy"), Path("new")}
    # Refers to test_check_whitespace_errors
    files = set(dzonegit.get_altered_files("HEAD~", "D", "HEAD"))
    assert files == {Path("whitespace")}
    subprocess.call(["git", "checkout", "-f", "HEAD"])
    assert set(dzonegit.get_altered_files("HEAD", "AM")) == set()


def test_get_zone_origin():
    testzone = b"""
$ORIGIN examPle.com. ;coment
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
$ORIGIN sub
$ORIGIN subsub.example.com.
$ORIGIN example.com.
"""
    assert "example.com" == dzonegit.get_zone_origin(testzone)
    testzone = b"""
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
$ORIGIN example.com.
ns.example.com.      60 IN A 192.0.2.1
"""
    assert dzonegit.get_zone_origin(testzone) is None


def test_get_zone_name():
    testzone = b"""
$ORIGIN eXample.com. ;coment
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
"""
    assert "example.com" == dzonegit.get_zone_name(
        "zones/example.com.zone", "",
    )
    assert "example.com" == dzonegit.get_zone_name(
        "zones/example.com.zone", testzone,
    )
    with pytest.raises(ValueError):
        dzonegit.get_zone_name("zones/example.org.zone", testzone)
    testzone = b"""
$ORIGIN 240/28.2.0.192.in-addr.arpa.
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns      60 IN A 192.0.2.1
"""
    assert "240/28.2.0.192.in-addr.arpa" == dzonegit.get_zone_name(
        "zones/240-28.2.0.192.in-addr.arpa.zone",
        testzone,
    )


def test_replace_serial(git_dir):
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm 1 61 60 60 60
  60 NS ns.example.org.
""")
    dzonegit.replace_serial(Path("dummy.zone"), "1", "60")
    assert git_dir.join("dummy.zone").read() == """
@ 60 IN SOA ns hm 60 61 60 60 60
  60 NS ns.example.org.
"""
    dzonegit.replace_serial(Path("dummy.zone"), "60", "61")
    assert git_dir.join("dummy.zone").read() == """
@ 60 IN SOA ns hm 61 61 60 60 60
  60 NS ns.example.org.
"""
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm (
                60 ; serial
                60 ; refresh
                60 ; retry
                60 ; expire
                60 ; minimum
                )
  60 NS ns.example.org.
""")
    dzonegit.replace_serial(Path("dummy.zone"), "60", "6000000")
    assert git_dir.join("dummy.zone").read() == """
@ 60 IN SOA ns hm (
                6000000 ; serial
                60 ; refresh
                60 ; retry
                60 ; expire
                60 ; minimum
                )
  60 NS ns.example.org.
"""


def test_check_updated_zones(git_dir):
    git_dir.chdir()
    git_dir.join("dummy.zone").write("")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones(dzonegit.get_head())
    subprocess.call(["git", "commit", "-m", "empty dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones("HEAD~", "HEAD")
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.com.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    dzonegit.check_updated_zones(dzonegit.get_head())
    subprocess.call(["git", "commit", "-m", "dummy.zone"])
    dzonegit.check_updated_zones("HEAD~", "HEAD")
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones(dzonegit.get_head())
    subprocess.call(["git", "commit", "-m", "updated dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones("HEAD~", "HEAD")
    git_dir.join("dummy.zone").write("""
$ORIGIN other.
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones(dzonegit.get_head())
    git_dir.join("dummy.zone").write("""
$ORIGIN dummy.
@ 60 IN SOA ns hm 1 61 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        dzonegit.check_updated_zones("HEAD", autoupdate_serial=True)
    subprocess.call(["git", "add", "dummy.zone"])
    dzonegit.check_updated_zones(dzonegit.get_head())
    subprocess.call(["git", "commit", "-m", "final dummy.zone"])
    dzonegit.check_updated_zones("HEAD~", "HEAD")


def test_get_increased_serial():
    assert "2" == dzonegit.get_increased_serial(1)
    assert str(int(time.time())) == dzonegit.get_increased_serial(1234567890)
    todayser = datetime.date.today().strftime("%Y%m%d00")
    assert todayser == dzonegit.get_increased_serial("2018010100")
    assert str(int(todayser) + 1) == dzonegit.get_increased_serial(todayser)


def test_get_config():
    subprocess.call(["git", "config", "test.bool", "TRUE"])
    subprocess.call(["git", "config", "test.bool2", "fAlSe"])
    subprocess.call(["git", "config", "test.int", "42"])
    assert "TRUE" == dzonegit.get_config("test.bool")
    assert dzonegit.get_config("test.bool", bool)
    assert not dzonegit.get_config("test.bool2", bool)
    assert 42 == dzonegit.get_config("test.int", int)


def test_update(git_dir):
    git_dir.chdir()
    os.environ.update({"GIT_DIR": str(git_dir.join(".git"))})
    with pytest.raises(SystemExit):
        dzonegit.update(["update", "refs/heads/slave", "0", "0"])
    dzonegit.update([
        "update", "refs/heads/master",
        "0"*40, dzonegit.get_head(),
    ])


def test_pre_receive(git_dir):
    git_dir.chdir()
    revisions = "{} {} ".format(
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        dzonegit.get_head(),
    )
    stdin = StringIO(revisions + "refs/heads/slave\n")
    with pytest.raises(SystemExit):
        dzonegit.pre_receive(stdin)
    stdin = StringIO(revisions + "refs/heads/master\n")
    dzonegit.pre_receive(stdin)


def test_post_receive(git_dir):
    git_dir.chdir()
    head = dzonegit.get_head()
    revisions = "{} {} refs/heads/master\n".format(
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        head,
    )
    stdin = StringIO(revisions)
    codir = git_dir.mkdir("co")
    subprocess.call(["git", "config", "dzonegit.checkoutpath", str(codir)])
    subprocess.call([
        "git", "config", "dzonegit.reconfigcmd",
        "echo TEST >{}/test".format(codir),
    ])
    dzonegit.post_receive(stdin)
    assert codir.join("dummy.zone").check()
    assert codir.join("test").read() == "TEST\n"


def test_template_config(git_dir):
    template = r"""{
  "header": "# Managed by dzonegit on $datetime, do not edit.\n",
  "footer": "# This is the end",
  "item": " - zone: \"$zonename\"\n   file: \"$zonefile\"\n   $zonevar\n",
  "defaultvar": "template: default",
  "zonevars": {
    "example.com": "template: signed",
    "*": "template: dummy"
  }
}"""
    output = dzonegit.template_config(str(git_dir), template)
    assert output.startswith("# Managed by dzonegit")
    assert " - zone: \"dummy\"\n   file: \"" in output
    assert "   template: dummy" in output
    assert output.endswith("# This is the end")
    output = dzonegit.template_config(
        str(git_dir),
        template,
        whitelist=set("a"),
    )
    assert " - zone: \"dummy\"\n   file: \"" not in output
    output = dzonegit.template_config(
        str(git_dir),
        template,
        blacklist=set("*"),
    )
    assert " - zone: \"dummy\"\n   file: \"" not in output


def test_load_set_file(git_dir):
    git_dir.join("dummy").write("dummy\n\n # Comment")
    s = dzonegit.load_set_file("dummy")
    assert s == {"dummy"}


def test_get_zone_wildcards():
    assert list(dzonegit.get_zone_wildcards("a.long.zone.name")) == [
        "a.long.zone.name", "*.long.zone.name",
        "*.zone.name", "*.name", "*",
    ]
