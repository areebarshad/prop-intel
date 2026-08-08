"""Controlled vocabularies shared by models, extraction prompts, and the API.

These are stored as strings rather than native Postgres enums: adding a signal
type or asset class is routine here, and a native enum turns that into a
migration with a table lock. The tradeoff is that the database won't reject a
bad value, so extraction validates against these on the way in.
"""

from __future__ import annotations

from enum import StrEnum


class FirmType(StrEnum):
    DEVELOPER = "developer"
    BROKERAGE = "brokerage"
    INVESTOR = "investor"
    PROPERTY_MANAGER = "property_manager"
    GENERAL_CONTRACTOR = "general_contractor"
    UNKNOWN = "unknown"


class AssetClass(StrEnum):
    MULTIFAMILY = "multifamily"
    INDUSTRIAL = "industrial"
    LOGISTICS = "logistics"
    DATA_CENTER = "data_center"
    OFFICE = "office"
    RETAIL = "retail"
    MIXED_USE = "mixed_use"
    HOSPITALITY = "hospitality"
    SENIOR_HOUSING = "senior_housing"
    LAND = "land"


class ProjectStatus(StrEnum):
    RUMORED = "rumored"
    PROPOSED = "proposed"
    REZONING = "rezoning"
    PERMITTED = "permitted"
    UNDER_CONSTRUCTION = "under_construction"
    DELIVERED = "delivered"
    STALLED = "stalled"
    CANCELLED = "cancelled"


class SourceKind(StrEnum):
    FIRM_SITE = "firm_site"
    PERMIT_PORTAL = "permit_portal"
    ZONING_MINUTES = "zoning_minutes"
    NEWS = "news"
    CAREERS = "careers"
    PRESS_RELEASE = "press_release"
    OPEN_DATA_API = "open_data_api"


class ContentType(StrEnum):
    HTML = "html"
    PDF = "pdf"
    JSON = "json"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    FAILED = "failed"
    SKIPPED_SCANNED = "skipped_scanned"  # PDF with no extractable text layer
    SKIPPED_EMPTY = "skipped_empty"


class AliasType(StrEnum):
    DBA = "dba"
    # Permit applicants are almost always single-purpose LLCs
    # ("Reston Station Phase III LLC"), not the parent developer.
    LLC_HOLDING = "llc_holding"
    FORMER_NAME = "former_name"
    ABBREVIATION = "abbreviation"
    MISSPELLING = "misspelling"


class SignalType(StrEnum):
    # Atomic — written by per-document extraction.
    LAND_ACQUISITION = "land_acquisition"
    PERMIT_FILING = "permit_filing"
    ZONING_CHANGE = "zoning_change"
    KEY_HIRE = "key_hire"
    DEPARTURE = "departure"
    NEW_PROJECT = "new_project"
    JOINT_VENTURE = "joint_venture"
    # Derived — written by trend and anomaly detection over windows of the above.
    HIRING_SURGE = "hiring_surge"
    ASSET_CLASS_PIVOT = "asset_class_pivot"
    GEOGRAPHIC_EXPANSION = "geographic_expansion"
    PERMIT_VOLUME_ANOMALY = "permit_volume_anomaly"


class RelationType(StrEnum):
    JOINT_VENTURE = "joint_venture"
    SHARED_PARCEL = "shared_parcel"
    GEOGRAPHIC_OVERLAP = "geographic_overlap"
    SHARED_EXECUTIVE = "shared_executive"
    CO_FILING = "co_filing"


class RoleType(StrEnum):
    EXECUTIVE = "executive"
    BROKER = "broker"
    DEVELOPER = "developer"
    ACQUISITIONS = "acquisitions"
    CONSTRUCTION = "construction"
    OTHER = "other"


class UserTier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class AlertChannel(StrEnum):
    EMAIL = "email"
    SLACK = "slack"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class ResolutionMethod(StrEnum):
    """How a raw mention got linked to a firm — kept for auditability.

    When a competitor-intelligence claim is challenged, "which rule linked this
    LLC to this developer" is the first question.
    """

    EXACT = "exact"
    ALIAS = "alias"
    FUZZY = "fuzzy"
    CORROBORATED = "corroborated"  # fuzzy + shared address/phone/parcel
    LLM = "llm"
    MANUAL = "manual"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"  # queued for review rather than guessed
    UNMATCHED = "unmatched"
    REJECTED = "rejected"
