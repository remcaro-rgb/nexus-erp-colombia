"""HR / time keeping / employee portal errors (codes registered in ``contracts/errors.yaml``)."""

from __future__ import annotations

from kernel.errors import Conflict, NotFound, ValidationFailed


class VacationBalanceInsufficient(ValidationFailed):
    code, title = "vacation_balance_insufficient", "Vacation balance insufficient"


class PortalDocumentNotOwned(NotFound):
    """404 (never 403): no existence oracle for another employee's documents."""

    code, title = "portal_document_not_owned", "Document not owned by the portal account"


class ConsentRequired(ValidationFailed):
    code, title = "consent_required", "Data processing consent required (Ley 1581)"


class ContractOverlap(ValidationFailed):
    code, title = "contract_overlap", "Employment contracts overlap"


class InvalidStateTransition(Conflict):
    code, title = "invalid_state_transition", "Action not allowed in the current state"


class ReasonRequired(ValidationFailed):
    code, title = "reason_required", "Reason required"
