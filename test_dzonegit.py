
import pytest
import subprocess
from pathlib import Path

from dzonegit import *


@pytest.fixture(scope="session")
def git_dir(tmpdir_factory):
    d = tmpdir_factory.getbasetemp()
    d.chdir()
    subprocess.call(["git", "init"])
    return d


def test_get_head(git_dir):
    git_dir.chdir()
    assert get_head() == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    git_dir.join("dummy").write("dummy\n")
    subprocess.call(["git", "add", "dummy"])
    subprocess.call(["git", "commit", "-m", "dummy"])
    assert get_head() != "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_check_whitespace_errors(git_dir):
    git_dir.chdir()
    git_dir.join("whitespace").write(" ")
    subprocess.call(["git", "add", "whitespace"])
    with pytest.raises(ValueError):
        check_whitespace_errors(get_head())
    subprocess.call(["git", "rm", "-f", "whitespace"])
    check_whitespace_errors(get_head())


def test_get_file_contents(git_dir):
    git_dir.chdir()
    assert get_file_contents("dummy") == "dummy\n"
    with pytest.raises(subprocess.CalledProcessError):
        get_file_contents('nonexistent')


def test_compile_zone():
    testzone = """
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
    r = compile_zone("example.org", testzone)
    assert not r.success
    assert r.zonehash is None
    assert r.stderr
    r = compile_zone("example.com", testzone)
    assert r.success
    assert r.serial == "1234567890"
    assert r.zonehash
    r2 = compile_zone("example.com", testzone + "\n\n; some comment")
    assert r.zonehash == r2.zonehash


def test_is_serial_increased():
    assert is_serial_increased(1234567890, "2018010100")
    assert is_serial_increased("2018010100", "4018010100")
    assert is_serial_increased("4018010100", "1234567890")
    assert not is_serial_increased(2018010100, "1234567890")


def test_get_altered_files(git_dir):
    git_dir.chdir()
    git_dir.join("dummy").write("dummy2\n")
    git_dir.join("new").write("newfile\n")
    subprocess.call(["git", "add", "dummy", "new"])
    files = set(get_altered_files("HEAD", "AM"))
    assert files == set([Path("dummy"), Path("new")])
    subprocess.call(["git", "checkout", "-f", "HEAD"])
    assert set(get_altered_files("HEAD", "AM")) == set()


def test_get_zone_origin():
    testzone = """
$ORIGIN examPle.com. ;coment
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
$ORIGIN sub
$ORIGIN subsub.example.com.
$ORIGIN example.com.
"""
    assert "example.com" == get_zone_origin(testzone)
    testzone = """
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
$ORIGIN example.com.
ns.example.com.      60 IN A 192.0.2.1
"""
    assert get_zone_origin(testzone) is None


def test_get_zone_name():
    testzone = """
$ORIGIN eXample.com. ;coment
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
"""
    assert "example.com" == get_zone_name("zones/example.com.zone", "")
    assert "example.com" == get_zone_name("zones/example.com.zone", testzone)
    with pytest.raises(ValueError):
        get_zone_name("zones/example.org.zone", testzone)
    testzone = """
$ORIGIN 240/28.2.0.192.in-addr.arpa.
@       60 IN SOA ns hostmaster 1 60 60 60 60
        60 IN NS ns
ns      60 IN A 192.0.2.1
"""
    assert "240/28.2.0.192.in-addr.arpa" == get_zone_name(
            "zones/240-28.2.0.192.in-addr.arpa.zone",
            testzone
            )


def test_check_updated_zones(git_dir):
    git_dir.chdir()
    git_dir.join("dummy.zone").write("")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        check_updated_zones(get_head())
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.com.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    check_updated_zones(get_head())
    subprocess.call(["git", "commit", "-m", "dummy.zone"])
    git_dir.join("dummy.zone").write("""
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        check_updated_zones(get_head())
    git_dir.join("dummy.zone").write("""
$ORIGIN other.
@ 60 IN SOA ns hm 1 60 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    with pytest.raises(ValueError):
        check_updated_zones(get_head())
    git_dir.join("dummy.zone").write("""
$ORIGIN dummy.
@ 60 IN SOA ns hm 2 60 60 60 60
  60 NS ns.example.org.
""")
    subprocess.call(["git", "add", "dummy.zone"])
    check_updated_zones(get_head())