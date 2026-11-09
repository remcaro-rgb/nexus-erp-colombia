"""Frozen service interfaces (drafted at G0, frozen v1 at G1).

Every module codes against these ``typing.Protocol`` classes; implementations
register with :mod:`kernel.services`; contract test doubles live in
:mod:`kernel.testing.fakes`. Changes after G1 need a CCR and a version bump.
"""

from kernel.interfaces.ar import ArOpenItems
from kernel.interfaces.audit import AuditService
from kernel.interfaces.dimensions import Dimensions
from kernel.interfaces.documents import DocumentRenderer, Labels
from kernel.interfaces.files import FileStore
from kernel.interfaces.fiscal import FiscalDocuments, FiscalGateway
from kernel.interfaces.notifications import Notifications, NotificationService
from kernel.interfaces.payroll import PayrollPayables
from kernel.interfaces.periods import PeriodService
from kernel.interfaces.policy import PolicyService
from kernel.interfaces.posting import PostingService
from kernel.interfaces.reporting import ReportQueryService
from kernel.interfaces.scheduler import SchedulerService
from kernel.interfaces.stock import Inventory, StockService
from kernel.interfaces.tax import TaxService
from kernel.interfaces.treasury import PaymentStatus
from kernel.interfaces.workflow import WorkflowService
from kernel.parameters import ParameterProvider

INTERFACES_VERSION = "1.0.0-draft"

#: P01 prompt deliverable 13 + kernel.parameters.
CORE_INTERFACES = (
    PostingService,
    StockService,
    TaxService,
    PolicyService,
    AuditService,
    WorkflowService,
    SchedulerService,
    FiscalGateway,
    DocumentRenderer,
    ReportQueryService,
    NotificationService,
    FileStore,
    ParameterProvider,
    PeriodService,
)

#: Stubs named in FEATURES-ADDENDUM (T-X-QA-07).
FEATURE_STUBS = {
    "ArOpenItems": ArOpenItems,
    "Dimensions": Dimensions,
    "DocumentRenderer": DocumentRenderer,
    "FiscalDocuments": FiscalDocuments,
    "Inventory": Inventory,
    "Labels": Labels,
    "Notifications": Notifications,
    "PaymentStatus": PaymentStatus,
    "PayrollPayables": PayrollPayables,
}

__all__ = [
    "CORE_INTERFACES",
    "FEATURE_STUBS",
    "INTERFACES_VERSION",
    "ArOpenItems",
    "AuditService",
    "Dimensions",
    "DocumentRenderer",
    "FileStore",
    "FiscalDocuments",
    "FiscalGateway",
    "Inventory",
    "Labels",
    "NotificationService",
    "Notifications",
    "ParameterProvider",
    "PaymentStatus",
    "PayrollPayables",
    "PeriodService",
    "PolicyService",
    "PostingService",
    "ReportQueryService",
    "SchedulerService",
    "StockService",
    "TaxService",
    "WorkflowService",
]
