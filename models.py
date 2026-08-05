"""Shared job annotation schema for scraping and review."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any


ANNOTATION_FIELDS = (
    "title",
    "company",
    "location",
    "employment_type",
    "remote",
    "salary",
    "skills",
    "requirements",
    "description",
    "source_url",
)


@dataclass
class JobAnnotation:
    """Structured fields extracted (and optionally corrected) from a job posting."""

    title: str = ""
    company: str = ""
    location: str = ""
    employment_type: str = ""
    remote: str = ""
    salary: str = ""
    skills: str = ""
    requirements: str = ""
    description: str = ""
    source_url: str = ""
    scraped_at: str = ""
    raw_text: str = ""
    notes: str = ""
    reviewed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobAnnotation":
        allowed = {f.name for f in fields(cls)}
        cleaned = {k: v for k, v in data.items() if k in allowed}
        return cls(**cleaned)

    @classmethod
    def empty(cls, source_url: str = "") -> "JobAnnotation":
        return cls(
            source_url=source_url,
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class JobRecord:
    """A persisted scraped job with editable annotations."""

    id: str
    annotations: JobAnnotation
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "annotations": self.annotations.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        return cls(
            id=data["id"],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            annotations=JobAnnotation.from_dict(data.get("annotations", {})),
        )
