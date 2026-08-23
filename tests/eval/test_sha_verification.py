import pytest

from eval.runner import GoldShaMismatchError, verify_gold_sha


def test_verify_gold_sha_passes_on_matching_hash(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text('{"a": 1}\n', encoding="utf-8")
    import hashlib

    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    actual = verify_gold_sha(path, expected_sha256=expected)

    assert actual == expected


def test_verify_gold_sha_aborts_on_mismatch(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text('{"a": 1}\n', encoding="utf-8")

    with pytest.raises(GoldShaMismatchError):
        verify_gold_sha(path, expected_sha256="0" * 64)


def test_verify_gold_sha_is_case_insensitive(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text('{"a": 1}\n', encoding="utf-8")
    import hashlib

    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    verify_gold_sha(path, expected_sha256=expected.upper())
