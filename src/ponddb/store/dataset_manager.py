# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Dataset manager — file storage, metadata, and DuckDB auto-registration."""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb


def _normalize_name(filename: str) -> str:
    """Derive a lowercase SQL-safe table name from a filename stem."""
    stem = Path(filename).stem
    name = stem.lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


@dataclass
class DatasetInfo:
    name: str
    file_path: str
    format: str
    size_bytes: int
    row_count: int
    columns: list[str]
    created_at: str


class DatasetManager:
    """Manages uploaded dataset files and their DuckDB registration."""

    def __init__(self, data_root: str) -> None:
        self.data_root = data_root
        self._datasets: dict[str, DatasetInfo] = {}

    def _ensure_data_root(self) -> None:
        os.makedirs(self.data_root, exist_ok=True)

    def has_dataset(self, name: str) -> bool:
        return name in self._datasets

    def upload(self, content: bytes, original_filename: str) -> DatasetInfo:
        """Save a file and return its metadata.

        Raises:
            ValueError: empty content or unsupported file type
            FileExistsError: dataset name already registered
        """
        ext = Path(original_filename).suffix.lower()
        if ext not in (".csv", ".parquet"):
            raise ValueError(f"Unsupported file type: {ext!r}. Only csv and parquet are supported.")
        if not content:
            raise ValueError("File is empty")

        name = _normalize_name(original_filename)
        if name in self._datasets:
            raise FileExistsError(f"Dataset already exists: {name!r}")

        self._ensure_data_root()
        file_path = os.path.join(self.data_root, f"{name}{ext}")
        with open(file_path, "wb") as fh:
            fh.write(content)

        fmt = "csv" if ext == ".csv" else "parquet"
        size_bytes = len(content)

        # Use DuckDB to introspect schema and row count
        row_count = 0
        columns: list[str] = []
        try:
            conn = duckdb.connect(":memory:")
            if fmt == "csv":
                rel = conn.execute(f"SELECT * FROM read_csv_auto('{file_path}')")
            else:
                rel = conn.execute(f"SELECT * FROM read_parquet('{file_path}')")
            if rel and rel.description:
                columns = [desc[0] for desc in rel.description]
            rows = rel.fetchall() if rel else []
            row_count = len(rows)
            conn.close()
        except Exception:
            pass

        created_at = datetime.now(timezone.utc).isoformat()
        info = DatasetInfo(
            name=name,
            file_path=file_path,
            format=fmt,
            size_bytes=size_bytes,
            row_count=row_count,
            columns=columns,
            created_at=created_at,
        )
        self._datasets[name] = info
        return info

    def list_datasets(self) -> list[DatasetInfo]:
        return list(self._datasets.values())

    def get_dataset(self, name: str) -> Optional[DatasetInfo]:
        return self._datasets.get(name)

    def delete_dataset(self, name: str) -> bool:
        """Remove a dataset. Returns True if found, False otherwise."""
        if name not in self._datasets:
            return False
        info = self._datasets.pop(name)
        try:
            os.remove(info.file_path)
        except FileNotFoundError:
            pass
        return True

    def register_in_session(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Load all registered datasets as TABLEs in the given connection.

        Must be called BEFORE ``enable_external_access`` is set to false,
        because ``read_csv_auto`` / ``read_parquet`` need file I/O access.
        Data is copied into in-memory tables so queries work after hardening.
        """
        for info in self._datasets.values():
            try:
                if info.format == "csv":
                    conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {info.name} AS "
                        f"SELECT * FROM read_csv_auto('{info.file_path}')"
                    )
                else:
                    conn.execute(
                        f"CREATE TABLE IF NOT EXISTS {info.name} AS "
                        f"SELECT * FROM read_parquet('{info.file_path}')"
                    )
            except Exception:
                pass
