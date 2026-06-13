"""
Immutable value objects for the Preflight domain model.

Value objects are identified by their attributes rather than an identity field.
All value objects here are implemented as frozen dataclasses to enforce immutability.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


class ReadinessVerdict(str, Enum):
    """The overall AI deployment readiness verdict for a diagnostic run."""

    GO = "GO"
    NOT_YET = "NOT_YET"
    NOT_READY = "NOT_READY"


class SeverityLevel(str, Enum):
    """Severity classification for issues discovered during analysis."""

    CRITICAL = "CRITICAL"  # blocks deployment
    HIGH = "HIGH"          # significant remediation needed
    MEDIUM = "MEDIUM"      # moderate effort required
    LOW = "LOW"            # nice to fix
    INFO = "INFO"          # informational only


class EffortLevel(str, Enum):
    """Rough effort-level buckets for remediation tasks."""

    TRIVIAL = "TRIVIAL"    # < 1 day
    LOW = "LOW"            # 1-5 days
    MEDIUM = "MEDIUM"      # 1-3 weeks
    HIGH = "HIGH"          # 1-3 months
    CRITICAL = "CRITICAL"  # > 3 months


class SystemType(str, Enum):
    """High-level classification of an enterprise system."""

    ERP = "ERP"
    CRM = "CRM"
    DATA_WAREHOUSE = "DATA_WAREHOUSE"
    DATABASE = "DATABASE"
    MESSAGE_QUEUE = "MESSAGE_QUEUE"
    API = "API"


class ConnectorType(str, Enum):
    """Specific connector/product identifiers supported by Preflight."""

    SAP = "SAP"
    ORACLE_ERP = "ORACLE_ERP"
    DYNAMICS_365 = "DYNAMICS_365"
    NETSUITE = "NETSUITE"
    SALESFORCE = "SALESFORCE"
    HUBSPOT = "HUBSPOT"
    SNOWFLAKE = "SNOWFLAKE"
    DATABRICKS = "DATABRICKS"
    REDSHIFT = "REDSHIFT"
    BIGQUERY = "BIGQUERY"
    POSTGRESQL = "POSTGRESQL"
    MYSQL = "MYSQL"
    SQLSERVER = "SQLSERVER"
    MONGODB = "MONGODB"


@dataclass(frozen=True)
class ReadinessScore:
    """A score from 0-100 representing AI deployment readiness.

    A score of 80+ yields GO, 50-79 yields NOT_YET, and below 50 yields NOT_READY.
    """

    value: float  # 0.0 to 100.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 100.0:
            raise ValueError(f"ReadinessScore must be 0-100, got {self.value}")

    @property
    def verdict(self) -> ReadinessVerdict:
        """Derive the readiness verdict from the numeric score."""
        if self.value >= 80:
            return ReadinessVerdict.GO
        elif self.value >= 50:
            return ReadinessVerdict.NOT_YET
        else:
            return ReadinessVerdict.NOT_READY

    def __str__(self) -> str:
        return f"{self.value:.1f}% ({self.verdict.value})"


@dataclass(frozen=True)
class EffortEstimate:
    """Effort estimate for a remediation item expressed as a day range.

    Attributes:
        min_days: Optimistic estimate in working days.
        max_days: Pessimistic estimate in working days.
        level: Bucketed effort level derived from the day range.
        confidence: Confidence in the estimate (0.0–1.0).
    """

    min_days: int
    max_days: int
    level: EffortLevel
    confidence: float = 0.7  # 0-1 confidence in estimate

    @classmethod
    def from_level(cls, level: EffortLevel) -> "EffortEstimate":
        """Construct a canonical estimate from an :class:`EffortLevel` bucket."""
        ranges = {
            EffortLevel.TRIVIAL: (0, 1),
            EffortLevel.LOW: (1, 5),
            EffortLevel.MEDIUM: (5, 21),
            EffortLevel.HIGH: (21, 90),
            EffortLevel.CRITICAL: (90, 180),
        }
        min_d, max_d = ranges[level]
        return cls(min_days=min_d, max_days=max_d, level=level)


@dataclass(frozen=True)
class EntityField:
    """A field definition within a business entity schema.

    Attributes:
        name: The field name as it appears in the source system.
        data_type: The declared data type (e.g. ``VARCHAR``, ``INTEGER``).
        nullable: Whether the field accepts NULL values.
        is_primary_key: True if this field is part of the primary key.
        is_foreign_key: True if this field is a foreign-key reference.
        description: Optional human-readable description.
    """

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    description: Optional[str] = None


@dataclass(frozen=True)
class ConnectionCredentials:
    """Credentials for connecting to an enterprise system.

    Passwords and tokens are intentionally excluded from this object.
    They are resolved at runtime via a secret manager reference
    (``credential_ref``).

    Attributes:
        system_type: The connector type these credentials target.
        host: Hostname or IP of the target system.
        port: TCP port of the target system.
        database: Target database or schema name.
        username: Service-account username.
        credential_ref: Key in the secret manager where the password/token lives.
    """

    system_type: ConnectorType
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    # password/token intentionally excluded - stored in vault/env
    credential_ref: Optional[str] = None  # reference to secret manager key


@dataclass(frozen=True)
class SchemaFieldMapping:
    """Maps a field from one system to an equivalent field in another system.

    Attributes:
        source_system: Identifier of the originating system.
        source_field: Field name in the source system.
        target_system: Identifier of the destination system.
        target_field: Field name in the target system.
        similarity_score: Semantic/structural similarity (0.0–1.0).
        mapping_type: How the mapping was derived:
            ``exact`` – identical names/types,
            ``semantic`` – meaning-equivalent with different names,
            ``derived`` – computed/transformed from the source,
            ``unmapped`` – no suitable counterpart found.
    """

    source_system: str
    source_field: str
    target_system: str
    target_field: str
    similarity_score: float  # 0-1
    mapping_type: str = "exact"  # exact, semantic, derived, unmapped
