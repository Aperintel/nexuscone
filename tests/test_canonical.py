"""Canonical serialisation and hashing tests."""

from nexuscone.canonical import canonical_json, sha256_hex


def test_canonical_json_sorts_keys() -> None:
    payload = {"b": 2, "a": 1, "c": 3}
    assert canonical_json(payload) == '{"a":1,"b":2,"c":3}'


def test_canonical_json_no_whitespace() -> None:
    payload = {"x": [1, 2, 3], "y": "hello"}
    out = canonical_json(payload)
    assert " " not in out


def test_canonical_json_deterministic_across_input_order() -> None:
    a = canonical_json({"x": 1, "y": [1, 2, 3]})
    b = canonical_json({"y": [1, 2, 3], "x": 1})
    assert a == b


def test_canonical_json_handles_nested_objects() -> None:
    payload = {"outer": {"inner": [3, 2, 1], "k": "v"}, "a": 1}
    out = canonical_json(payload)
    assert out == '{"a":1,"outer":{"inner":[3,2,1],"k":"v"}}'


def test_sha256_empty_string_known_vector() -> None:
    assert sha256_hex("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_is_deterministic() -> None:
    assert sha256_hex("nexuscone") == sha256_hex("nexuscone")


def test_sha256_differs_on_different_input() -> None:
    assert sha256_hex("a") != sha256_hex("b")
