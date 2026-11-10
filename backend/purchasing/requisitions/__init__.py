"""Internal requisitions, consolidation, sourcing and purchase requisitions (F-INV-REQ; owner P11, part P11b).

Models register under the ``purchasing`` app label and the migrations of this part live in
``requisitions/migrations`` (see :mod:`purchasing.requisitions.extension`).
"""

from purchasing.requisitions.extension import install as _install

_install("requisitions")
