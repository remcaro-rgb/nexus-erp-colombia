"""IAM (P04): identity, tenancy, row/field security, branches, portal identities.

Single authorization path (ADR-004): :mod:`iam.policy` (``authorize``,
``scope_queryset``, ``field_mask``, ``check_sod``), registered as the
``kernel.interfaces.PolicyService`` implementation at app ready.
"""
