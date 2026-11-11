"""Customer self-service portal (F-PORTAL-CUS; owner P10, part P10b) under ``/api/portal/v1/customer/*``.

Models register under the ``sales`` app label and the migrations of this part live in ``portal/migrations``
(see :mod:`sales.portal.extension`); the routes are absolute portal routes mounted by :mod:`sales.portal.urls`.
"""

from sales.portal.extension import install as _install

_install()
