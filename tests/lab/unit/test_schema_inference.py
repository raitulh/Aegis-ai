"""Schema inference, split partitioning and profiling for dataset files (pure, no DB)."""

from __future__ import annotations

import io
import json
import struct
import zipfile

import pytest

from aegis_api.errors import ValidationFailed
from aegis_api.lab.data.inference import (
    infer_cell_type,
    infer_schema,
    merge_types,
    partition_by_column,
    profile_dataset,
)


def _columns(schema: dict) -> dict[str, dict]:
    return {c["name"]: c for c in schema["columns"]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42", "int"),
        ("-7", "int"),
        ("3.14", "float"),
        ("1e-3", "float"),
        (".5", "float"),
        ("inf", "float"),
        ("true", "bool"),
        ("FALSE", "bool"),
        ("2026-09-30", "datetime"),
        ("2026-09-30T12:00:00Z", "datetime"),
        ("2026-09-30 12:00:00+02:00", "datetime"),
        ("2026-13-45", "string"),
        ("hello", "string"),
        ("", None),
        ("NA", None),
        ("null", None),
        ("NaN", None),
    ],
)
def test_cell_types(raw, expected):
    assert infer_cell_type(raw) == expected


def test_merge_types():
    assert merge_types({"int"}) == "int"
    assert merge_types({"int", "float"}) == "float"
    assert merge_types({"int", "string"}) == "string"
    assert merge_types(set()) == "string"
    assert merge_types({"object", "int"}) == "mixed"


CSV = (
    "id,score,passed,when,label,empty\n"
    "1,0.5,true,2026-01-01,a,\n"
    "2,1,false,2026-01-02T10:00:00,b,\n"
    "3,,true,2026-01-03,,\n"
    "4,2.5,false,2026-01-04,d,NA\n"
)


def test_csv_types_nulls_and_rows():
    result = infer_schema("csv", io.BytesIO(CSV.encode()), len(CSV))
    assert result.format == "csv"
    assert result.row_count == 4
    cols = _columns(result.schema)
    assert list(cols) == ["id", "score", "passed", "when", "label", "empty"]
    assert cols["id"]["type"] == "int" and cols["id"]["null_count"] == 0 and not cols["id"]["nullable"]
    assert cols["score"]["type"] == "float" and cols["score"]["null_count"] == 1
    assert cols["passed"]["type"] == "bool"
    assert cols["when"]["type"] == "datetime"
    assert cols["label"]["type"] == "string" and cols["label"]["nullable"]
    assert cols["empty"]["null_count"] == 4 and cols["empty"]["nullable"]
    assert result.schema["sampled_rows"] == 4


def test_csv_sample_is_bounded_but_rows_are_counted():
    data = "x\n" + "".join(f"{i}\n" for i in range(5000)) + "oops\n"
    result = infer_schema("csv", io.BytesIO(data.encode()), len(data), max_rows=1000)
    assert result.row_count == 5001
    assert result.schema["sampled_rows"] == 1000
    assert _columns(result.schema)["x"]["type"] == "int"  # "oops" is beyond the sample


def test_csv_header_dedup_ragged_rows_and_bom():
    data = "﻿a,a,,b\n1,2,3,4\n5,6\n"
    result = infer_schema("csv", io.BytesIO(data.encode("utf-8")), 0)
    assert [c["name"] for c in result.schema["columns"]] == ["a", "a_2", "column_3", "b"]
    assert result.schema["ragged_rows"] == 1
    assert result.row_count == 2


def test_tsv():
    data = b"a\tb\n1\thello\n"
    result = infer_schema("tsv", io.BytesIO(data), len(data))
    assert result.format == "tsv" and result.schema["delimiter"] == "\t"
    assert _columns(result.schema)["b"]["type"] == "string"


def test_jsonl_keys_types_and_missing_keys():
    lines = [{"a": 1, "b": "x"}, {"a": 2.5, "c": True}, {"a": None, "b": "2026-01-01"}, {"d": [1]}]
    data = "\n".join(json.dumps(r) for r in lines).encode()
    result = infer_schema("jsonl", io.BytesIO(data), len(data))
    assert result.row_count == 4
    cols = _columns(result.schema)
    assert cols["a"]["type"] == "float" and cols["a"]["null_count"] == 2
    assert cols["b"]["type"] == "string" and cols["b"]["null_count"] == 2
    assert cols["c"]["type"] == "bool" and cols["c"]["null_count"] == 3
    assert cols["d"]["type"] == "array"


def test_json_array_and_object():
    data = json.dumps([{"a": 1}, {"a": 2}]).encode()
    result = infer_schema("json", io.BytesIO(data), len(data))
    assert result.row_count == 2 and result.schema["top_level"] == "array"
    data = json.dumps({"k1": 1, "k2": [1]}).encode()
    result = infer_schema("json", io.BytesIO(data), len(data))
    assert result.row_count is None and result.schema["keys"] == ["k1", "k2"]


def _npy(shape: tuple[int, ...], descr: str = "<f8") -> bytes:
    header = f"{{'descr': '{descr}', 'fortran_order': False, 'shape': {shape!r}, }}".encode()
    header += b" " * (64 - ((10 + len(header) + 1) % 64)) + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header


def test_npy_and_npz():
    result = infer_schema("npy", io.BytesIO(_npy((7, 3))), 0)
    assert result.row_count == 7 and result.schema["shape"] == [7, 3] and result.schema["dtype"] == "<f8"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.npy", _npy((2,), "<i8"))
    buf.seek(0)
    result = infer_schema("npz", buf, 0)
    assert result.schema["arrays"]["x"]["shape"] == [2]


def test_archive_listing_and_text():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "x\n1\n")
        zf.writestr("dir/", "")
    buf.seek(0)
    result = infer_schema("zip", buf, 0)
    assert result.schema["file_count"] == 1 and result.schema["files"][0]["name"] == "a.csv"
    assert infer_schema("text", io.BytesIO(b"a\nb\nc\n"), 6).row_count == 3


def test_parquet_without_pyarrow_reports_unavailable():
    result = infer_schema("parquet", io.BytesIO(b"PAR1" + b"\x00" * 8 + b"PAR1"), 16)
    assert result.format == "parquet"


def test_invalid_csv_is_rejected():
    with pytest.raises(ValidationFailed):
        infer_schema("csv", io.BytesIO(b"a,b\n\xff\xfe\n"), 6)


# -- partitioning -----------------------------------------------------------------------------------
def test_partition_csv_by_split_column():
    data = b"x,split\n1,train\n2,test\n3,train\n\n4,train\n"
    parts = partition_by_column(io.BytesIO(data), "csv", "split", ["train", "test"])
    try:
        assert parts["train"].rows == 3 and parts["test"].rows == 1
        assert parts["train"].file.read() == b"x,split\n1,train\n3,train\n4,train\n"
        assert parts["test"].file.read() == b"x,split\n2,test\n"
    finally:
        for p in parts.values():
            p.file.close()


def test_partition_jsonl_by_split_column():
    data = b'{"x": 1, "s": "train"}\n{"x": 2, "s": "holdout"}\n'
    parts = partition_by_column(io.BytesIO(data), "jsonl", "s", ["train", "holdout"])
    try:
        assert parts["holdout"].rows == 1
        assert json.loads(parts["holdout"].file.read()) == {"x": 2, "s": "holdout"}
    finally:
        for p in parts.values():
            p.file.close()


def test_partition_rejects_undeclared_values_and_missing_column():
    with pytest.raises(ValidationFailed) as info:
        partition_by_column(io.BytesIO(b"x,split\n1,train\n2,dev\n"), "csv", "split", ["train"])
    assert "dev" in info.value.message
    with pytest.raises(ValidationFailed):
        partition_by_column(io.BytesIO(b"x,y\n1,2\n"), "csv", "split", ["train"])
    with pytest.raises(ValidationFailed):
        partition_by_column(io.BytesIO(b"PAR1"), "parquet", "split", ["train"])


# -- profiling --------------------------------------------------------------------------------------
def test_profile_csv_full_pass():
    data = b"a,b\n1,x\n2,y\n3,x\n,z\n"
    seen: list[int] = []
    profile = profile_dataset("csv", io.BytesIO(data), progress=seen.append, progress_every=2)
    assert profile["row_count"] == 4
    cols = {c["name"]: c for c in profile["columns"]}
    assert cols["a"]["min"] == 1 and cols["a"]["max"] == 3 and cols["a"]["mean"] == 2
    assert cols["a"]["null_count"] == 1
    assert cols["b"]["distinct"] == 3
    assert seen == [2, 4]


def test_profile_jsonl():
    data = b'{"a": 1}\n{"a": 3, "b": "q"}\n'
    profile = profile_dataset("jsonl", io.BytesIO(data))
    cols = {c["name"]: c for c in profile["columns"]}
    assert profile["row_count"] == 2
    assert cols["a"]["mean"] == 2
    assert cols["b"]["null_count"] == 1
    with pytest.raises(ValidationFailed):
        profile_dataset("npy", io.BytesIO(b""))
