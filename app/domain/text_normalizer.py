r"""
Unicode normalization for the parsing layer.

Customers using Chinese IMEs frequently type full-width punctuation and digits
(／ ６ １４ ：, full-width space) that the date / guest / pet parsers' regexes —
which assume half-width `/`, `\d`, `\s` — silently fail to match. NFKC folds
those compatibility forms onto their ASCII equivalents:

    ／(U+FF0F) -> /     ０-９(U+FF10-19) -> 0-9
    　(U+3000) -> space ：(U+FF1A) -> :

NFKC leaves CJK ideographs (入住, 退房, 沒水, 大人 ...) untouched, so the Chinese
keywords every parser relies on are unaffected.

This normalizes ONLY the working copy fed to the parsers. The customer's
original text is preserved upstream (InboundMessage.text / raw_text / owner
push) so the stored record keeps exactly what they sent.
"""

import unicodedata


def normalize_for_parsing(text: str) -> str:
    """Return an NFKC-normalized copy of `text` for regex-based parsing."""
    return unicodedata.normalize("NFKC", text)
