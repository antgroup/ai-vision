import pytest

from agentlens.bootstrap import read_org_id


def test_org_id_strips_unicode_whitespace(tmp_path):
    path = tmp_path / "org"
    path.write_text("\u00a0\u2003org-123\n")
    assert read_org_id(path) == "org-123"


@pytest.mark.parametrize("value", ["", " \n", "first\nsecond", "first\u00a0second"])
def test_org_id_requires_one_nonempty_value(tmp_path, value):
    path = tmp_path / "org"
    path.write_text(value)
    with pytest.raises(ValueError, match="one nonempty"):
        read_org_id(path)


def test_org_id_reports_read_and_decode_errors(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        read_org_id(tmp_path / "absent")
    path = tmp_path / "org"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="cannot read"):
        read_org_id(path)
