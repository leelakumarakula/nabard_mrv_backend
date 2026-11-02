"""Importing this package registers every table on Base.metadata (used by Alembic and tests)."""

from app.models.audit import AuditEvent
from app.models.community import ClusterMembership, CommunityResourcePerson, DemonstrationSite, PestSurveillanceCluster
from app.models.config import (
    BeneficiaryCategory,
    DemographicField,
    FarmerCategoryAssignment,
    ProgrammeDecision,
    ReportTemplate,
)
from app.models.evidence import EvidenceAsset, EvidenceLink
from app.models.identity import PasswordResetToken, Role, RolePermission, SystemSetting, User, UserScope, user_roles
from app.models.indicator import BaselineRecord, IndicatorDefinition, IndicatorTarget, MonitoringRecord
from app.models.integration import (
    IntegrationFieldMapping,
    IntegrationSource,
    QuarantinedRecord,
    SourceRecordMap,
    SyncRun,
)
from app.models.mrv import (
    CalculationRun,
    CalculationRunInput,
    IndicatorResult,
    Report,
    ReviewComment,
    ReviewTask,
    VerificationDecision,
    VerificationFinding,
)
from app.models.programme import GeoUnit, Organisation, Programme, ProgrammeOrganisation, ProgrammeTarget, ReportingPeriod
from app.models.registry import Farm, Farmer, FarmPlot, ProgrammeParticipation
from app.models.verification import VerificationSample, VerificationSampleItem
from app.models.workstream import (
    Activity,
    Advisory,
    AdvisoryDelivery,
    Device,
    DeviceCalibration,
    FieldRecord,
    FieldRecordParticipant,
    PracticeRecord,
    Workstream,
)

__all__ = [
    "ClusterMembership", "CommunityResourcePerson", "DemonstrationSite", "PestSurveillanceCluster",
    "VerificationSample", "VerificationSampleItem", "BeneficiaryCategory", "DemographicField",
    "FarmerCategoryAssignment", "ProgrammeDecision", "ReportTemplate",
    "Activity", "Advisory", "AdvisoryDelivery", "AuditEvent", "BaselineRecord", "CalculationRun", "CalculationRunInput",
    "Device", "DeviceCalibration", "EvidenceAsset", "EvidenceLink", "Farm", "Farmer", "FarmPlot", "FieldRecord",
    "FieldRecordParticipant", "GeoUnit", "IndicatorDefinition", "IndicatorResult", "IndicatorTarget",
    "IntegrationFieldMapping", "IntegrationSource", "MonitoringRecord", "Organisation", "PasswordResetToken",
    "PracticeRecord", "Programme", "ProgrammeOrganisation", "ProgrammeParticipation", "ProgrammeTarget",
    "QuarantinedRecord", "Report", "ReportingPeriod", "ReviewComment", "ReviewTask", "Role", "RolePermission",
    "SourceRecordMap", "SyncRun", "SystemSetting", "User", "UserScope", "VerificationDecision", "VerificationFinding",
    "Workstream", "user_roles",
]
