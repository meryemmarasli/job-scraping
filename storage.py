"""SQLite-backed storage for scraped job annotations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import JobAnnotation, JobRecord

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "jobs.db"


class JobStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    annotations_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create(self, annotation: JobAnnotation) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        record = JobRecord(
            id=str(uuid.uuid4()),
            annotations=annotation,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, created_at, updated_at, annotations_json) VALUES (?, ?, ?, ?)",
                (
                    record.id,
                    record.created_at,
                    record.updated_at,
                    json.dumps(record.annotations.to_dict()),
                ),
            )
            conn.commit()
        return record

    def create_many(self, annotations: list[JobAnnotation]) -> list[JobRecord]:
        return [self.create(ann) for ann in annotations]

    def existing_keys(self) -> tuple[set[str], set[str]]:
        """Return (source_urls, title::company identities) already in the store."""
        urls: set[str] = set()
        identities: set[str] = set()
        for job in self.list_jobs():
            ann = job.annotations
            if ann.source_url:
                urls.add(ann.source_url.rstrip("/").lower())
            identities.add(f"{ann.title.lower().strip()}::{ann.company.lower().strip()}")
        return urls, identities

    def create_many_skip_duplicates(
        self, annotations: list[JobAnnotation]
    ) -> tuple[list[JobRecord], int]:
        """Insert jobs that are not already stored. Returns (created, skipped_count)."""
        urls, identities = self.existing_keys()
        created: list[JobRecord] = []
        skipped = 0
        for ann in annotations:
            url_key = ann.source_url.rstrip("/").lower() if ann.source_url else ""
            identity = f"{ann.title.lower().strip()}::{ann.company.lower().strip()}"
            if (url_key and url_key in urls) or identity in identities:
                skipped += 1
                continue
            record = self.create(ann)
            created.append(record)
            if url_key:
                urls.add(url_key)
            identities.add(identity)
        return created, skipped

    def list_jobs(self) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, annotations_json FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at, updated_at, annotations_json FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update_annotations(self, job_id: str, annotation: JobAnnotation) -> JobRecord | None:
        existing = self.get(job_id)
        if not existing:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET updated_at = ?, annotations_json = ? WHERE id = ?",
                (now, json.dumps(annotation.to_dict()), job_id),
            )
            conn.commit()
        return self.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM jobs")
            conn.commit()

    def export_json(self, reviewed_only: bool = False) -> list[dict[str, Any]]:
        jobs = self.list_jobs()
        payload = []
        for job in jobs:
            if reviewed_only and not job.annotations.reviewed:
                continue
            payload.append(job.to_dict())
        return payload

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            annotations=JobAnnotation.from_dict(json.loads(row["annotations_json"])),
        )
