"""Supplier self-service portal (F-PORTAL-SUP; owner P11, part P11b) under ``/api/portal/v1/supplier/*``.

Models register under the ``purchasing`` app label (see :mod:`purchasing.requisitions.extension`); the routes
are absolute portal routes mounted by :mod:`purchasing.portal.urls`.
"""

from purchasing.requisitions.extension import install as _install

_install("portal")
