from app.parser import parse_pair


def test_parse_pair():
    assert parse_pair("a = 1") == ("a", "1")
