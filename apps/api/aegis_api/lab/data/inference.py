"""Dataset schema inference, split partitioning and profiling — pure, streaming, bounded memory.

* CSV/TSV: header + per-column type inference (``int``/``float``/``bool``/``datetime``/``string``) and
  null counts over the first ``max_rows`` rows; the full file is streamed once more for ``row_count``.
* JSONL: key union with per-key types and null counts (missing keys count as null).
* JSON: arrays of records are treated like JSONL (bounded by :data:`JSON_PARSE_LIMIT`); objects list keys.
* NPY/NPZ: shapes and dtypes from array headers (no numpy needed, pickled dtypes are never loaded).
* Parquet: footer metadata when ``pyarrow`` is installed; otherwise the schema is reported unavailable.
* Archives: entry listing (bounded).

Nothing here touches the database or storage; callers pass file objects (seekable spools or streams).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import re
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, BinaryIO, cast

from aegis_api.errors import ValidationFailed
from aegis_api.lab.data.uploads import JSON_PARSE_LIMIT, NPY_MAX_HEADER_BYTES, parse_npy_header

DEFAULT_SAMPLE_ROWS = 1000
MAX_COLUMNS = 2000
MAX_JSON_KEYS = 500
MAX_ARCHIVE_LISTING = 1000
MAX_DISTINCT_TRACKED = 10_000
NULL_TOKENS = frozenset({"", "na", "n/a", "nan", "null", "none", "nil", "#n/a", "-"})
BOOL_TOKENS = frozenset({"true", "false"})
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$|^[+-]?(inf|infinity)$", re.IGNORECASE)
_DATE_HINT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
SPLIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class InferredSchema:
    format: str
    schema: dict[str, Any]
    row_count: int | None


# -- scalar typing ----------------------------------------------------------------------------------
def _is_datetime(text: str) -> bool:
    if not _DATE_HINT_RE.match(text):
        return False
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        try:
            date.fromisoformat(candidate[:10])
            return len(candidate) == 10
        except ValueError:
            return False


def infer_cell_type(raw: str) -> str | None:
    """Type of one delimited-text cell, or ``None`` for a null token."""
    text = raw.strip()
    lowered = text.lower()
    if lowered in NULL_TOKENS:
        return None
    if lowered in BOOL_TOKENS:
        return "bool"
    if _INT_RE.match(text):
        return "int"
    if _FLOAT_RE.match(text):
        return "float"
    if _is_datetime(text):
        return "datetime"
    return "string"


def json_value_type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "datetime" if _is_datetime(value) else "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def merge_types(types: set[str]) -> str:
    """Collapse the set of observed types for one column into a single type."""
    if not types:
        return "string"
    if len(types) == 1:
        return next(iter(types))
    if types <= {"int", "float"}:
        return "float"
    return "string" if types <= {"int", "float", "bool", "datetime", "string"} else "mixed"


@dataclass
class _ColumnStats:
    name: str
    types: set[str] = field(default_factory=set)
    null_count: int = 0
    observed: int = 0

    def add(self, kind: str | None) -> None:
        self.observed += 1
        if kind is None:
            self.null_count += 1
        else:
            self.types.add(kind)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": merge_types(self.types),
            "nullable": self.null_count > 0 or not self.types,
            "null_count": self.null_count,
        }


def _unique_headers(header: list[str]) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(header[:MAX_COLUMNS]):
        name = raw.strip()[:200] or f"column_{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        seen.setdefault(name, 1)
        names.append(name)
    return names


def _text(fileobj: BinaryIO) -> io.TextIOWrapper:
    if hasattr(fileobj, "seekable") and fileobj.seekable():
        fileobj.seek(0)
    return io.TextIOWrapper(fileobj, encoding="utf-8-sig", errors="strict", newline="")


def _release(wrapper: io.TextIOWrapper[Any]) -> None:
    try:
        raw: Any = wrapper.detach()
    except ValueError:
        return
    if hasattr(raw, "seekable") and raw.seekable():
        raw.seek(0)


# -- delimited text ---------------------------------------------------------------------------------
def infer_delimited(
    fileobj: BinaryIO,
    *,
    delimiter: str = ",",
    max_rows: int = DEFAULT_SAMPLE_ROWS,
    count_rows: bool = True,
) -> InferredSchema:
    wrapper = _text(fileobj)
    try:
        reader = csv.reader(wrapper, delimiter=delimiter)
        header = next(reader, None)
        if header is None:
            raise ValidationFailed("Delimited file has no header row")
        names = _unique_headers(header)
        stats = [_ColumnStats(n) for n in names]
        rows = sampled = ragged = 0
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            rows += 1
            if len(row) != len(names):
                ragged += 1
            if sampled < max_rows:
                sampled += 1
                for index, column in enumerate(stats):
                    column.add(infer_cell_type(row[index]) if index < len(row) else None)
            elif not count_rows:
                break
    except (csv.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(f"Could not parse delimited file: {str(exc)[:120]}") from exc
    finally:
        _release(wrapper)
    fmt = "csv" if delimiter == "," else "tsv"
    return InferredSchema(
        format=fmt,
        schema={
            "format": fmt,
            "delimiter": delimiter,
            "columns": [c.as_dict() for c in stats],
            "sampled_rows": sampled,
            "ragged_rows": ragged,
            "truncated_columns": len(header) > MAX_COLUMNS,
        },
        row_count=rows if count_rows else None,
    )


# -- JSON / JSONL -----------------------------------------------------------------------------------
class _RecordStats:
    def __init__(self) -> None:
        self.keys: dict[str, _ColumnStats] = {}
        self.record_types: set[str] = set()
        self.sampled = 0
        self.truncated_keys = False

    def add(self, record: Any) -> None:
        self.sampled += 1
        if not isinstance(record, dict):
            self.record_types.add(json_value_type(record) or "null")
            return
        self.record_types.add("object")
        for key in record:
            if key not in self.keys:
                if len(self.keys) >= MAX_JSON_KEYS:
                    self.truncated_keys = True
                    continue
                column = _ColumnStats(str(key)[:200])
                column.null_count = column.observed = self.sampled - 1  # absent in earlier records
                self.keys[key] = column
        for key, column in self.keys.items():
            column.add(json_value_type(record.get(key)))

    def as_schema(self, fmt: str) -> dict[str, Any]:
        return {
            "format": fmt,
            "record_types": sorted(self.record_types),
            "columns": [c.as_dict() for c in self.keys.values()],
            "sampled_rows": self.sampled,
            "truncated_columns": self.truncated_keys,
        }


def infer_jsonl(fileobj: BinaryIO, *, max_rows: int = DEFAULT_SAMPLE_ROWS, count_rows: bool = True) -> InferredSchema:
    stats = _RecordStats()
    rows = 0
    if hasattr(fileobj, "seekable") and fileobj.seekable():
        fileobj.seek(0)
    try:
        for index, line in enumerate(fileobj):
            text = line.decode("utf-8-sig" if index == 0 else "utf-8").strip()
            if not text:
                continue
            rows += 1
            if stats.sampled < max_rows:
                stats.add(json.loads(text))
            elif not count_rows:
                break
    except (ValueError, RecursionError) as exc:
        raise ValidationFailed("Could not parse JSON Lines file") from exc
    finally:
        if hasattr(fileobj, "seekable") and fileobj.seekable():
            fileobj.seek(0)
    return InferredSchema("jsonl", stats.as_schema("jsonl"), rows if count_rows else None)


def infer_json(fileobj: BinaryIO, size: int, *, max_rows: int = DEFAULT_SAMPLE_ROWS) -> InferredSchema:
    if size > JSON_PARSE_LIMIT:
        return InferredSchema("json", {"format": "json", "note": "document too large for inline inference"}, None)
    wrapper = _text(fileobj)
    try:
        document = json.load(wrapper)
    except (ValueError, RecursionError) as exc:
        raise ValidationFailed("Could not parse JSON file") from exc
    finally:
        _release(wrapper)
    if isinstance(document, list):
        stats = _RecordStats()
        for record in document[:max_rows]:
            stats.add(record)
        return InferredSchema("json", {**stats.as_schema("json"), "top_level": "array"}, len(document))
    if isinstance(document, dict):
        keys = [str(k)[:200] for k in list(document)[:MAX_JSON_KEYS]]
        return InferredSchema(
            "json",
            {"format": "json", "top_level": "object", "keys": keys, "truncated_keys": len(document) > MAX_JSON_KEYS},
            None,
        )
    return InferredSchema("json", {"format": "json", "top_level": json_value_type(document) or "null"}, None)


# -- arrays, parquet, archives ----------------------------------------------------------------------
def _npy_summary(header: dict[str, Any]) -> dict[str, Any]:
    return {"dtype": str(header["descr"]), "shape": list(header["shape"]), "fortran_order": header["fortran_order"]}


def infer_npy(fileobj: BinaryIO) -> InferredSchema:
    if hasattr(fileobj, "seekable") and fileobj.seekable():
        fileobj.seek(0)
    header = parse_npy_header(fileobj.read(NPY_MAX_HEADER_BYTES + 12))
    shape = list(header["shape"])
    return InferredSchema("npy", {"format": "npy", **_npy_summary(header)}, shape[0] if shape else 1)


def infer_npz(fileobj: BinaryIO) -> InferredSchema:
    arrays: dict[str, Any] = {}
    with zipfile.ZipFile(fileobj) as archive:
        for info in archive.infolist()[:MAX_ARCHIVE_LISTING]:
            if info.is_dir():
                continue
            with archive.open(info) as member:
                header = parse_npy_header(member.read(NPY_MAX_HEADER_BYTES + 12))
            arrays[info.filename.removesuffix(".npy")[:200]] = _npy_summary(header)
    fileobj.seek(0)
    return InferredSchema("npz", {"format": "npz", "arrays": arrays}, None)


def infer_parquet(fileobj: BinaryIO) -> InferredSchema:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return InferredSchema("parquet", {"format": "parquet", "note": "schema extraction requires pyarrow"}, None)
    fileobj.seek(0)
    try:
        metadata = pq.ParquetFile(fileobj).metadata
        columns = [
            {"name": metadata.schema.column(i).name, "type": str(metadata.schema.column(i).physical_type).lower()}
            for i in range(min(metadata.num_columns, MAX_COLUMNS))
        ]
        return InferredSchema("parquet", {"format": "parquet", "columns": columns}, int(metadata.num_rows))
    except Exception as exc:
        raise ValidationFailed("Could not read Parquet metadata") from exc
    finally:
        fileobj.seek(0)


def infer_archive(fileobj: BinaryIO, kind: str) -> InferredSchema:
    entries: list[dict[str, Any]] = []
    count = 0
    fileobj.seek(0)
    if kind == "zip":
        with zipfile.ZipFile(fileobj) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                count += 1
                if len(entries) < MAX_ARCHIVE_LISTING:
                    entries.append({"name": info.filename[:300], "size": info.file_size})
    else:
        with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
            for member in tar:
                if not member.isreg():
                    continue
                count += 1
                if len(entries) < MAX_ARCHIVE_LISTING:
                    entries.append({"name": member.name[:300], "size": member.size})
    fileobj.seek(0)
    fmt = "zip" if kind == "zip" else "tar.gz"
    return InferredSchema(fmt, {"format": fmt, "files": entries, "file_count": count}, count)


def infer_text(fileobj: BinaryIO) -> InferredSchema:
    fileobj.seek(0)
    lines = sum(1 for _ in fileobj)
    fileobj.seek(0)
    return InferredSchema("text", {"format": "text"}, lines)


KIND_TO_FORMAT = {
    "csv": "csv",
    "tsv": "tsv",
    "json": "json",
    "jsonl": "jsonl",
    "parquet": "parquet",
    "npy": "npy",
    "npz": "npz",
    "zip": "zip",
    "tar_gz": "tar.gz",
    "text": "text",
    "markdown": "text",
}


def infer_schema(
    kind: str,
    fileobj: BinaryIO,
    size: int,
    *,
    max_rows: int = DEFAULT_SAMPLE_ROWS,
    count_rows: bool = True,
) -> InferredSchema:
    """Infer the schema of a validated, seekable dataset file of upload ``kind``."""
    if kind in ("csv", "tsv"):
        return infer_delimited(
            fileobj, delimiter="," if kind == "csv" else "\t", max_rows=max_rows, count_rows=count_rows
        )
    if kind == "jsonl":
        return infer_jsonl(fileobj, max_rows=max_rows, count_rows=count_rows)
    if kind == "json":
        return infer_json(fileobj, size, max_rows=max_rows)
    if kind == "npy":
        return infer_npy(fileobj)
    if kind == "npz":
        return infer_npz(fileobj)
    if kind == "parquet":
        return infer_parquet(fileobj)
    if kind in ("zip", "tar_gz"):
        return infer_archive(fileobj, kind)
    if kind in ("text", "markdown"):
        return infer_text(fileobj) if count_rows else InferredSchema("text", {"format": "text"}, None)
    return InferredSchema(KIND_TO_FORMAT.get(kind, "binary"), {"format": KIND_TO_FORMAT.get(kind, "binary")}, None)


# -- split partitioning -----------------------------------------------------------------------------
@dataclass
class Partition:
    file: BinaryIO
    rows: int = 0


def _new_spool() -> BinaryIO:
    return cast(BinaryIO, tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b"))


def partition_by_column(
    fileobj: BinaryIO,
    kind: str,
    column: str,
    split_names: Iterable[str],
) -> dict[str, Partition]:
    """Split a CSV/TSV/JSONL file into one spooled file per value of ``column``.

    Every row must carry one of ``split_names``; the header is repeated in each CSV/TSV partition.
    Callers own (and must close) the returned files.
    """
    names = set(split_names)
    partitions = {name: Partition(_new_spool()) for name in names}
    unknown: set[str] = set()
    try:
        if kind in ("csv", "tsv"):
            _partition_delimited(fileobj, "," if kind == "csv" else "\t", column, partitions, unknown)
        elif kind == "jsonl":
            _partition_jsonl(fileobj, column, partitions, unknown)
        else:
            raise ValidationFailed("A split column is only supported for CSV, TSV and JSONL datasets")
        if unknown:
            shown = ", ".join(sorted(unknown)[:10])
            raise ValidationFailed(f"Rows reference splits that were not declared: {shown}")
        for partition in partitions.values():
            partition.file.seek(0)
        return partitions
    except BaseException:
        for partition in partitions.values():
            partition.file.close()
        raise


def _partition_delimited(
    fileobj: BinaryIO, delimiter: str, column: str, partitions: dict[str, Partition], unknown: set[str]
) -> None:
    wrapper = _text(fileobj)
    writers: dict[str, tuple[io.TextIOWrapper[Any], Any]] = {}
    try:
        reader = csv.reader(wrapper, delimiter=delimiter)
        header = next(reader, None)
        if header is None:
            raise ValidationFailed("Delimited file has no header row")
        stripped = [h.strip() for h in header]
        if column not in stripped:
            raise ValidationFailed(f"Split column '{column[:80]}' is not in the header")
        index = stripped.index(column)
        for name, partition in partitions.items():
            out = io.TextIOWrapper(partition.file, encoding="utf-8", newline="")
            writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
            writer.writerow(header)
            writers[name] = (out, writer)
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            value = row[index].strip() if index < len(row) else ""
            target = writers.get(value)
            if target is None:
                if len(unknown) < 100:
                    unknown.add(value[:40] or "(empty)")
                continue
            target[1].writerow(row)
            partitions[value].rows += 1
    except (csv.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(f"Could not parse delimited file: {str(exc)[:120]}") from exc
    finally:
        for out, _writer in writers.values():
            out.flush()
            out.detach()
        _release(wrapper)


def _partition_jsonl(fileobj: BinaryIO, column: str, partitions: dict[str, Partition], unknown: set[str]) -> None:
    fileobj.seek(0)
    try:
        for index, line in enumerate(fileobj):
            text = line.decode("utf-8-sig" if index == 0 else "utf-8").strip()
            if not text:
                continue
            record = json.loads(text)
            value = record.get(column) if isinstance(record, dict) else None
            key = str(value) if value is not None else ""
            partition = partitions.get(key)
            if partition is None:
                if len(unknown) < 100:
                    unknown.add(key[:40] or "(missing)")
                continue
            partition.file.write(text.encode("utf-8") + b"\n")
            partition.rows += 1
    except (ValueError, RecursionError) as exc:
        raise ValidationFailed("Could not parse JSON Lines file") from exc
    finally:
        fileobj.seek(0)


# -- full profiling (async activity) ----------------------------------------------------------------
@dataclass
class _NumericStats:
    count: int = 0
    minimum: float | None = None
    maximum: float | None = None
    total: float = 0.0

    def add(self, value: float) -> None:
        if math.isnan(value) or math.isinf(value):
            return
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def as_dict(self) -> dict[str, Any]:
        if not self.count:
            return {}
        return {"min": self.minimum, "max": self.maximum, "mean": self.total / self.count}


@dataclass
class _ProfileColumn:
    stats: _ColumnStats
    numeric: _NumericStats = field(default_factory=_NumericStats)
    distinct: set[str] = field(default_factory=set)
    distinct_overflow: bool = False

    def add(self, raw: Any, kind: str | None) -> None:
        self.stats.add(kind)
        if kind is None:
            return
        if kind in ("int", "float") and not isinstance(raw, bool):
            with contextlib.suppress(TypeError, ValueError):
                self.numeric.add(float(raw))
        if not self.distinct_overflow:
            self.distinct.add(str(raw)[:200])
            if len(self.distinct) > MAX_DISTINCT_TRACKED:
                self.distinct_overflow = True
                self.distinct.clear()

    def as_dict(self) -> dict[str, Any]:
        data = self.stats.as_dict()
        data.update(self.numeric.as_dict())
        data["distinct"] = None if self.distinct_overflow else len(self.distinct)
        data["distinct_capped"] = self.distinct_overflow
        return data


def profile_dataset(
    kind: str,
    fileobj: BinaryIO,
    *,
    progress: Callable[[int], None] | None = None,
    progress_every: int = 50_000,
) -> dict[str, Any]:
    """One full streaming pass over a CSV/TSV/JSONL dataset: exact row count + per-column statistics."""
    columns: dict[str, _ProfileColumn] = {}
    rows = 0
    if kind in ("csv", "tsv"):
        wrapper = io.TextIOWrapper(fileobj, encoding="utf-8-sig", errors="strict", newline="")
        try:
            reader = csv.reader(wrapper, delimiter="," if kind == "csv" else "\t")
            header = next(reader, None) or []
            names = _unique_headers(header)
            ordered = [_ProfileColumn(_ColumnStats(n)) for n in names]
            columns = dict(zip(names, ordered, strict=True))
            for row in reader:
                if not row or (len(row) == 1 and not row[0].strip()):
                    continue
                rows += 1
                for index, column in enumerate(ordered):
                    raw = row[index] if index < len(row) else ""
                    column.add(raw.strip(), infer_cell_type(raw))
                if progress and rows % progress_every == 0:
                    progress(rows)
        except (csv.Error, UnicodeDecodeError) as exc:
            raise ValidationFailed(f"Could not parse delimited file: {str(exc)[:120]}") from exc
        finally:
            _release(wrapper)
    elif kind == "jsonl":
        try:
            for index, line in enumerate(fileobj):
                text = line.decode("utf-8-sig" if index == 0 else "utf-8").strip()
                if not text:
                    continue
                rows += 1
                record = json.loads(text)
                if isinstance(record, dict):
                    for key in record:
                        if key not in columns and len(columns) < MAX_JSON_KEYS:
                            column = _ProfileColumn(_ColumnStats(str(key)[:200]))
                            column.stats.null_count = column.stats.observed = rows - 1
                            columns[key] = column
                    for key, column in columns.items():
                        value = record.get(key)
                        column.add(value, json_value_type(value))
                if progress and rows % progress_every == 0:
                    progress(rows)
        except (ValueError, RecursionError) as exc:
            raise ValidationFailed("Could not parse JSON Lines file") from exc
    else:
        raise ValidationFailed(f"Profiling is not supported for '{kind}' datasets")
    return {"format": kind, "row_count": rows, "columns": [c.as_dict() for c in columns.values()]}
