"""Model package.

Importing every model here is what makes Alembic autogenerate see the full
metadata — a model that is only imported lazily by a service is invisible at
migration time and silently never gets a table.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.alert import Alert, AlertDelivery, Digest
from app.models.document import DocumentChunk, EntityCandidate, RawDocument
from app.models.firm import Firm, FirmAlias, FirmRelationship
from app.models.person import JobPosting, Person
from app.models.project import Permit, PropertyProject
from app.models.signal import Signal, TrendWindow
from app.models.source import Source
from app.models.user import ApiKey, User

__all__ = [
    "Alert",
    "AlertDelivery",
    "ApiKey",
    "Base",
    "Digest",
    "DocumentChunk",
    "EntityCandidate",
    "Firm",
    "FirmAlias",
    "FirmRelationship",
    "JobPosting",
    "Permit",
    "Person",
    "PropertyProject",
    "RawDocument",
    "Signal",
    "Source",
    "TrendWindow",
    "User",
]
