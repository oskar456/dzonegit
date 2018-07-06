
import pytest
import contextlib
import os
import subprocess
from pathlib import Path

from dzonegit import *

@contextlib.contextmanager
def cwd(directory):
    curdir = os.getcwd()
    try:
        os.chdir(Path(__file__).parent / directory)
        yield
    finally:
        os.chdir(curdir)

def test_get_head_empty():
    with cwd("emptyrepo"):
        assert get_head() == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    with cwd("testrepo"):
        assert get_head() == "ca6f091201985bfb3e047b3bba8632235e1c0486"

def test_check_whitespace_errors():
    with cwd("emptyrepo"):
        with pytest.raises(ValueError):
            check_whitespace_errors(get_head())
    with cwd("testrepo"):
            check_whitespace_errors(get_head())

def test_get_file_contents():
    with cwd("testrepo"):
        assert get_file_contents('dummy') == "dummy\n"
        with pytest.raises(subprocess.CalledProcessError):
            get_file_contents('nonexistent')

def test_compile_zone():
    testzone = """
$ORIGIN example.com.
@	60 IN SOA ns hostmaster (
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


def test_get_altered_files():
    with cwd("testrepo"):
        files = set(get_altered_files("HEAD", "A"))
        assert files == set([
                Path("zones/example.org.zone")
                ])

def test_get_zone_origin():
    testzone = """
$ORIGIN examPle.com. ;coment
@	60 IN SOA ns hostmaster 1 60 60 60 60
	60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
$ORIGIN sub
$ORIGIN subsub.example.com.
$ORIGIN example.com.
"""
    assert "example.com" == get_zone_origin(testzone)
    testzone = """
@	60 IN SOA ns hostmaster 1 60 60 60 60
	60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
"""
    assert get_zone_origin(testzone) is None
    testzone = """
@	60 IN SOA ns hostmaster 1 60 60 60 60
	60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
$ORIGIN sub.example.com.
"""
    assert get_zone_origin(testzone, 4) is None


def test_get_zone_name():
    testzone = """
$ORIGIN eXample.com. ;coment
@	60 IN SOA ns hostmaster 1 60 60 60 60
	60 IN NS ns
ns.example.com.      60 IN A 192.0.2.1
"""
    assert "example.com" == get_zone_name("zones/example.com.zone", "")
    assert "example.com" == get_zone_name("zones/example.com.zone", testzone)
    with pytest.raises(ValueError):
        get_zone_name("zones/example.org.zone", testzone)
    testzone = """
$ORIGIN 240/28.2.0.192.in-addr.arpa.
@	60 IN SOA ns hostmaster 1 60 60 60 60
	60 IN NS ns
ns      60 IN A 192.0.2.1
"""
    assert "240/28.2.0.192.in-addr.arpa" == get_zone_name(
            "zones/240-28.2.0.192.in-addr.arpa.zone",
            testzone
            )

def test_check_updated_zones():
    with cwd("emptyrepo"):
        with pytest.raises(ValueError):
            check_updated_zones(get_head())
    with cwd("testrepo"):
        check_updated_zones(get_head())
