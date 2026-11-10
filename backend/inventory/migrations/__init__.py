"""Inventory migrations.

Extension parts (P09b ``issues``/``barcodes``, P09c ``bom``/``transformations``) own their paths and
cannot write here: their migration files live in ``inventory/<part>/migrations/`` (no ``__init__``
needed, names ``0101_<part>_*.py`` depending on the latest core migration) and are loaded as part of
this package because the directories are appended to ``__path__`` below.
"""

from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _part in ("issues", "barcodes", "bom", "transformations"):
    _extra = _HERE.parent / _part / "migrations"
    if _extra.is_dir():
        __path__.append(str(_extra))  # noqa: F821 - package __path__ is defined at import time
