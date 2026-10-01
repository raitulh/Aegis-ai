"""Pure unit tests: metrics, evaluator sandbox, sanitization, validators, identifiers."""

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile

import pytest

from app.core.errors import ValidationFailed
from app.core.markdown import plain_text, render_markdown
from app.core.security import make_signed_payload, read_signed_payload
from app.core.validators import is_public_hostname, safe_filename, validate_external_url, validate_zip_archive
from app.evaluation import metrics as m
from app.evaluation.registry import config_hash, normalize_evaluation_config
from app.evaluation.sandbox import get_sandbox
from app.modules.credentials.certificates import is_well_formed, new_public_id, render_template_text
from app.modules.leaderboards.service import result_label


def test_metrics_known_values() -> None:
    assert m.accuracy(["a", "b", "a"], ["a", "b", "b"]) == pytest.approx(2 / 3)
    assert m.rmse([1.0, 2.0, 3.0], [1.0, 2.0, 5.0]) == pytest.approx((4 / 3) ** 0.5)
    assert m.mae([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.5)
    assert m.roc_auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(0.75)
    assert m.log_loss([1, 0], [0.9, 0.1]) == pytest.approx(0.10536, rel=1e-3)
    assert m.macro_f1(["x", "y", "x", "y"], ["x", "y", "y", "y"]) == pytest.approx((2 / 3 + 0.8) / 2)


def test_roc_auc_single_class_is_undefined() -> None:
    with pytest.raises(m.MetricUndefined):
        m.roc_auc([1, 1, 1], [0.2, 0.4, 0.9])


def test_evaluation_config_normalization_and_hash() -> None:
    cfg = normalize_evaluation_config({"metric": "rmse", "id_column": "id", "target_column": "y", "secondary_metrics": ["mae", "rmse"]})
    assert cfg["secondary_metrics"] == ["mae"]
    assert config_hash(cfg, "abc") == config_hash(dict(cfg), "abc") != config_hash(cfg, "abd")
    with pytest.raises(ValidationFailed):
        normalize_evaluation_config({"metric": "not-a-metric"})
    with pytest.raises(ValidationFailed):
        normalize_evaluation_config({"metric": "accuracy", "id_column": "x", "target_column": "x"})


def _run_sandbox(submission: str, truth: str, metric: str = "accuracy") -> dict:
    workdir = tempfile.mkdtemp()
    with open(os.path.join(workdir, "submission.csv"), "w") as fh:
        fh.write(submission)
    with open(os.path.join(workdir, "ground_truth.csv"), "w") as fh:
        fh.write(truth)
    cfg = normalize_evaluation_config({"metric": metric, "id_column": "id", "target_column": "target"})
    with open(os.path.join(workdir, "job.json"), "w") as fh:
        json.dump({"config": cfg, "max_rows": 1000}, fh)
    sb = get_sandbox()
    report = sb.run(workdir, "validate")
    if report.get("ok"):
        report["score"] = sb.run(workdir, "score")
    return report


def test_sandbox_scores_public_and_private_splits() -> None:
    truth = "id,target,Usage\na,1,Public\nb,0,Public\nc,1,Private\nd,0,Private\n"
    out = _run_sandbox("id,target\na,1\nb,0\nc,0\nd,0\n", truth)
    assert out["ok"]
    assert out["score"]["public"] == 1.0 and out["score"]["private"] == 0.5


@pytest.mark.parametrize("submission,code", [
    ("id,target\na,1\na,0\nb,1\nc,1\n", "duplicate_id"),
    ("id,target\na,1\nb,0\n", "missing_ids"),
    ("id,wrong\na,1\nb,0\nc,1\nd,0\n", "missing_column"),
    ("id,target\na,1\nb,0\nc,1\nd,0\nz,1\n", "unexpected_ids"),
])
def test_sandbox_rejects_invalid_submissions(submission: str, code: str) -> None:
    out = _run_sandbox(submission, "id,target\na,1\nb,0\nc,1\nd,0\n")
    assert not out["ok"]
    assert any(e["code"] == code for e in out["errors"]), out


def test_markdown_is_sanitized() -> None:
    html = render_markdown("# Title\n<script>alert(1)</script>\n[x](javascript:alert(1)) ![i](data:image/png;base64,AAA)\n<img src=x onerror=alert(1)>")
    # Raw HTML is escaped (rendered as text), dangerous URLs never become attributes.
    assert "<script" not in html and "<img src=x" not in html and 'href="javascript' not in html and 'src="data:' not in html
    assert "<h2>" in html and "<h1>" not in html  # user content never owns the page <h1>
    link = render_markdown("[site](https://example.com)")
    assert 'rel="nofollow noopener noreferrer ugc"' in link
    assert plain_text("**bold** and `code`") == "bold and code"


def test_url_validation_and_ssrf_guard() -> None:
    assert validate_external_url("https://example.com/x") == "https://example.com/x"
    for bad in ("javascript:alert(1)", "ftp://example.com", "https://user:pw@example.com", "//example.com", "https://exa mple.com"):
        with pytest.raises(ValidationFailed):
            validate_external_url(bad)
    for host in ("127.0.0.1", "10.0.0.8", "169.254.169.254", "localhost", "[::1]", "metadata.google.internal"):
        assert not is_public_hostname(host)
    assert is_public_hostname("api.github.com")


def test_safe_filenames() -> None:
    assert safe_filename("train data (v2).csv") == "train data _v2_.csv" or safe_filename("train data (v2).csv").endswith(".csv")
    for bad in ("../../etc/passwd", "a/b.csv", "a\\b.csv", ".env", ""):
        with pytest.raises(ValidationFailed):
            safe_filename(bad)


def test_zip_bomb_and_traversal_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "x")
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    with pytest.raises(ValidationFailed):
        validate_zip_archive(path=path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.txt", "0" * 50_000_000)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    with pytest.raises(ValidationFailed):
        validate_zip_archive(path=path)
    os.unlink(path)


def test_certificate_ids_are_checksummed() -> None:
    pid = new_public_id()
    assert is_well_formed(pid) and is_well_formed(pid.lower())
    tampered = pid[:-1] + ("0" if pid[-1] != "0" else "1")
    assert not is_well_formed(tampered)
    assert not is_well_formed("DB-XXXX-YYYY-ZZ")


def test_certificate_templates_only_substitute_whitelisted_placeholders() -> None:
    out = render_template_text("Hi {recipient} {recipient.__class__} {event}", {"recipient": "Ada", "event": "Cup"})
    assert out == "Hi Ada {recipient.__class__} Cup"


def test_signed_payloads_expire_and_are_purpose_bound() -> None:
    token = make_signed_payload({"x": 1}, "download", 60)
    assert read_signed_payload(token, "download") == {"x": 1, **{k: v for k, v in read_signed_payload(token, "download").items() if k != "x"}}
    assert read_signed_payload(token, "unsubscribe") is None
    assert read_signed_payload(token + "x", "download") is None
    assert read_signed_payload(make_signed_payload({"x": 1}, "download", -1), "download") is None


def test_result_labels_are_factual() -> None:
    assert result_label(1, 50) == "Winner"
    assert result_label(3, 50) == "Top 3"
    assert result_label(7, 50) == "Top 10"
    assert result_label(3, 3) == "Participant"  # no "Top 3" when only three teams competed
    assert result_label(None, 10) == "Participant"
