"""Shared cross-stage data contracts (DTOs + enums).

These mirror the `wound_extraction` table columns and the route enums 1:1
(SPEC.md §C, spec-architecture §D). Every pipeline stage speaks these types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


# ---------------------------------------------------------------- enums
class Drainage(StrEnum):
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"


class Stage(StrEnum):
    S1 = "1"
    S2 = "2"
    S3 = "3"
    S4 = "4"
    UNSTAGEABLE = "unstageable"
    DTI = "DTI"
    NA = "N/A"


class SourceKind(StrEnum):
    NOTE = "note"
    ASSESSMENT = "assessment"
    DIAGNOSIS = "diagnosis"


class ExtractionMethod(StrEnum):
    REGEX_SPN = "regex_spn"
    REGEX_ENVIVE = "regex_envive"
    REGEX_PROSE = "regex_prose"
    SOAP = "soap"
    JSON = "json"
    LLM = "llm"
    MANUAL = "manual"


class Route(StrEnum):
    AUTO = "auto_accept"
    FLAG = "flag_for_review"
    REJECT = "reject"


class NoteFormat(StrEnum):
    ENVIVE = "envive"
    SOAP = "soap_idt"
    PROSE = "prose_shorthand"
    SPN = "labeled_spn"
    ASSESS_FLAT = "assessment_flat_json"
    ASSESS_NARRATIVE = "assessment_narrative"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------- DTOs
@dataclass(frozen=True)
class PatientRef:
    patient_id: str          # 'FA-001'  -> /diagnoses, /coverage
    id: int                  # 1         -> /notes, /assessments
    facility_id: int
    primary_payer_code: str | None = None
    last_modified_at: str | None = None


@dataclass
class FieldEvidence:
    """Per-field provenance for the patient-detail highlight (SPEC R1)."""
    value: str | float | None
    evidence_span: tuple[int, int] | None   # (char_start, char_end) into note_text
    method: ExtractionMethod
    source_conf: float | None = None


@dataclass
class ExtractedWound:
    """One wound from one source. Mirrors `wound_extraction` columns."""
    patient_id: str
    source_kind: SourceKind
    source_note_id: int | None = None
    source_assessment_id: int | None = None
    is_primary: bool = True
    extraction_method: ExtractionMethod = ExtractionMethod.REGEX_PROSE
    wound_type: str | None = None
    wound_type_conf: float | None = None
    stage: str | None = None
    stage_conf: float | None = None
    location: str | None = None
    location_conf: float | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    depth_cm: float | None = None
    measure_conf: float | None = None
    drainage: str | None = None
    drainage_conf: float | None = None
    overall_conf: float | None = None
    evidence: dict[str, FieldEvidence] = field(default_factory=dict)
    extracted_at: str | None = None


@dataclass
class EvidenceNode:
    id: str          # 'dx:L89.143' | 'note:1' | 'assess:55001' | 'wound:primary'
    kind: str        # 'diagnosis' | 'note' | 'assessment' | 'wound'
    label: str


@dataclass
class EvidenceEdge:
    source: str
    target: str
    relation: str    # 'agree' | 'conflict'
    color: str       # 'green' | 'red'


@dataclass
class EvidenceGraph:
    nodes: list[EvidenceNode] = field(default_factory=list)
    edges: list[EvidenceEdge] = field(default_factory=list)
    agreeing_sources: int = 0
