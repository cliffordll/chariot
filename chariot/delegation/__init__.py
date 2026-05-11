"""Delegation skeletons for Milestone A6."""

from chariot.delegation.models import DelegatedTaskSpec, DelegationRequest, DelegationResult
from chariot.delegation.service import DelegationService

__all__ = [
    "DelegatedTaskSpec",
    "DelegationRequest",
    "DelegationResult",
    "DelegationService",
]
