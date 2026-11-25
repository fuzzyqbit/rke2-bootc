import pytest
from conftest import REPO, run
S = str(REPO / "node/scripts/rke2-version.sh")

@pytest.mark.parametrize("field,expected", [
    ("rpm", "1.34.3~rke2r1"), ("majmin", "1.34"), ("tag", "v1.34.3-rke2r1")])
def test_fields(field, expected):
    r = run([S, field, "v1.34.3+rke2r1"])
    assert (r.returncode, r.stdout.strip()) == (0, expected)

@pytest.mark.parametrize("bad", ["1.34.3+rke2r1", "v1.34+rke2r1", "v1.34.3", "latest", ""])
def test_bad_version_exits_2(bad):
    r = run([S, "rpm", bad])
    assert r.returncode == 2 and "version" in r.stderr.lower()

def test_bad_field_exits_2():
    assert run([S, "nope", "v1.34.3+rke2r1"]).returncode == 2
