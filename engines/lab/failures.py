"""Failure intelligence: deterministic classification, signatures, similarity, recovery playbook and lessons.

Everything here is pure and reproducible. A failure is described by :class:`FailureSignals` (what the
platform observed: exit code, OOM flag, traceback, metrics, policy decision, statistical outcome …) and
classified by an *ordered* rule table — the first matching rule decides the :class:`FailureType`, every
matching rule is reported in ``matched_rules`` so the evidence shows all signals that fired.

A model (e.g. the FailureAnalyzerAgent) may *assist* the diagnosis later, but the rule classification is
always recorded alongside it (AGENTS.md rule 1).

Signatures: :func:`normalize_traceback` strips volatile details (paths, line numbers, addresses, ids,
numbers, quoted values) so the same failure produces the same :func:`failure_signature` across runs,
machines and code edits that only shift line numbers.

Recovery: :func:`propose_recovery` maps a classification to a conservative, bounded
:class:`RecoveryProposal`. It never exceeds the supplied resource limits, never invents dependency
versions and never auto-recovers policy failures or legitimate negative results.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.states import FailureType

FAILURE_ENGINE_VERSION = "failures-1.0.0"

FailureStage = Literal[
    "execution", "evaluation", "agent", "tool", "policy", "reproduction", "statistics", "data", "design", "strategy"
]

#: Upper bound on the characters of any text signal that the rules scan (tail is kept: it is the informative end).
MAX_SIGNAL_CHARS = 64_000
#: Minimum number of repeated failures of one strategy before it is treated as a strategy failure.
STRATEGY_REPEAT_THRESHOLD = 3


# =============================================================================================
# Inputs and outputs
# =============================================================================================
class FailureSignals(BaseModel):
    """Everything the platform observed about a failure. All fields are optional except ``stage``."""

    model_config = ConfigDict(extra="forbid")

    stage: FailureStage = "execution"
    exit_code: int | None = None
    oom_killed: bool = False
    timed_out: bool = False
    traceback: str | None = None
    logs: str | None = Field(default=None, description="Tail of the job logs.")
    error_message: str | None = None
    error_class: str | None = None
    metrics: dict[str, Any] | None = None
    metrics_file_present: bool | None = None
    policy_decision: str | None = Field(default=None, description="allow | deny | require_approval")
    provider_error_class: str | None = None
    reproduction_verdict: str | None = None
    p_value: float | None = None
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    adequately_powered: bool | None = None
    power: float | None = Field(default=None, ge=0.0, le=1.0)
    effect_direction: Literal["expected", "opposite", "none"] | None = None
    n_seeds: int | None = Field(default=None, ge=0)
    required_seeds: int | None = Field(default=None, ge=0)
    validation_issue_codes: list[str] = Field(
        default_factory=list,
        description="Error-severity design-validation codes (codes prefixed 'warning:' are ignored).",
    )
    repeated_failures_for_strategy: int = Field(default=0, ge=0)
    tool_name: str | None = None


class Classification(BaseModel):
    failure_type: FailureType
    subtype: str
    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: list[str]
    root_cause: str
    evidence: dict[str, Any]
    signature: str
    engine_version: str = FAILURE_ENGINE_VERSION

    @property
    def rule_id(self) -> str:
        return self.matched_rules[0]


# =============================================================================================
# Text analysis helpers
# =============================================================================================
_FRAME_RE = re.compile(r'File "(?P<path>[^"\n]+)", line (?P<line>\d+)(?:, in (?P<func>[^\s]+))?')
_EXC_LINE_RE = re.compile(
    r"^(?P<cls>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt|Iteration|Warning|Denied|Exceeded|"
    r"Unavailable|Invalid|Required))(?:\s*:\s?(?P<msg>.*))?$"
)
_TRACEBACK_HEADER = "Traceback (most recent call last):"
_MISSING_MODULE_RE = re.compile(r"No module named ['\"]?(?P<mod>[A-Za-z_][\w.]*)['\"]?")
_CANNOT_IMPORT_RE = re.compile(r"cannot import name ['\"]?[\w.]+['\"]? from ['\"]?(?P<mod>[A-Za-z_][\w.]*)['\"]?")
_MISSING_PATH_RE = re.compile(r"No such file or directory:?\s*(?:'(?P<q1>[^'\n]+)'|\"(?P<q2>[^\"\n]+)\"|(?P<bare>\S+))")
_LIBRARY_PATH_RE = re.compile(r"(site-packages|dist-packages|/lib/python\d|<frozen |\\lib\\)")
_DATA_PATH_MARKERS = ("/workspace/input", "/workspace/data", "/data/", "/datasets/", "dataset")
_DATA_PATH_PREFIXES = ("data/", "input/", "datasets/", "./data/", "./input/")

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_HEX_ADDR_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_LONG_HEX_RE = re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{16,}\b")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s\"'<>|*?]+\\)*(?P<base>[^\\\s\"'<>|*?]*)")
_POSIX_PATH_RE = re.compile(r"(?<![\w.:/~])~?(?:/[^\s/\"'<>:,;()\[\]{}]+)+/?")
_TMP_NAME_RE = re.compile(r"\btmp[a-zA-Z0-9_]{4,}\b")
_SQ_VALUE_RE = re.compile(r"(?<!\w)'[^'\n]*'(?!\w)")
_DQ_VALUE_RE = re.compile(r"(?<!\w)\"[^\"\n]*\"(?!\w)")
_NUMBER_RE = re.compile(r"(?<![A-Za-z_<])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_CARET_LINE_RE = re.compile(r"^[\s^~]+$")
_WS_RE = re.compile(r"[ \t]+")
_TOKEN_RE = re.compile(r"<\w+>|[A-Za-z0-9_]+")


def _tail(text: str | None, limit: int = MAX_SIGNAL_CHARS) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[-limit:]


def _basename(path: str) -> str:
    stripped = path.rstrip("/\\")
    for sep in ("/", "\\"):
        if sep in stripped:
            stripped = stripped.rsplit(sep, 1)[-1]
    return stripped or path


def _last_traceback_block(text: str) -> str:
    idx = text.rfind(_TRACEBACK_HEADER)
    return text[idx:] if idx >= 0 else text


def _parse_frames(text: str) -> list[tuple[str, str]]:
    """``(basename, function)`` for every frame of the *last* traceback block, outermost first."""
    frames: list[tuple[str, str]] = []
    for match in _FRAME_RE.finditer(_last_traceback_block(text)):
        frames.append((_basename(match.group("path")), match.group("func") or "?"))
    return frames


def _innermost_frame_path(text: str) -> str | None:
    last: str | None = None
    for match in _FRAME_RE.finditer(_last_traceback_block(text)):
        last = match.group("path")
    return last


def _parse_exception_line(text: str) -> tuple[str, str] | None:
    """Return ``(qualified class, message)`` of the last exception line in ``text``."""
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith("File "):
            continue
        match = _EXC_LINE_RE.match(line)
        if match:
            return match.group("cls"), (match.group("msg") or "").strip()
    return None


def _normalize_line(line: str) -> str:
    frame = _FRAME_RE.search(line)
    if frame and line.lstrip().startswith("File "):
        func = frame.group("func") or "?"
        return f'File "{_basename(frame.group("path"))}", line N, in {func}'
    out = _TS_RE.sub("<ts>", line)
    out = _UUID_RE.sub("<uuid>", out)
    out = _HEX_ADDR_RE.sub("<hex>", out)
    out = _LONG_HEX_RE.sub("<hex>", out)
    out = _WIN_PATH_RE.sub(lambda m: m.group("base") or "<path>", out)
    out = _POSIX_PATH_RE.sub(lambda m: _basename(m.group(0)) or "<path>", out)
    out = _SQ_VALUE_RE.sub("'<v>'", out)
    out = _DQ_VALUE_RE.sub('"<v>"', out)
    out = _TMP_NAME_RE.sub("<tmp>", out)
    out = _NUMBER_RE.sub("N", out)
    return _WS_RE.sub(" ", out).strip()


def normalize_traceback(text: str | None) -> str:
    """Strip volatile details from a traceback / error text while keeping its shape.

    Absolute paths become basenames (temp directories disappear with them), line numbers and other numbers
    become ``N``, hex addresses ``<hex>``, UUIDs ``<uuid>``, timestamps ``<ts>``, quoted values ``'<v>'``
    and temp names ``<tmp>``. Exception types and frame function names are preserved. Caret marker lines
    are dropped and whitespace is collapsed.
    """
    if not text:
        return ""
    lines: list[str] = []
    for raw in _tail(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip() or _CARET_LINE_RE.match(raw):
            continue
        normalized = _normalize_line(raw)
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _signature_core(text: str | None) -> str:
    """The stable part of an error text: frame (file, function) chain + exception type + normalized message."""
    if not text:
        return ""
    body = _tail(text)
    frames = _parse_frames(body)
    exc = _parse_exception_line(body)
    if exc is not None:
        cls, msg = exc
        chain = "|".join(f"{name}:{func}" for name, func in frames)
        return f"{chain}||{cls.rsplit('.', 1)[-1]}: {_normalize_line(msg)}"
    lines = normalize_traceback(body).split("\n")
    return "\n".join(lines[-20:])


def failure_signature(
    failure_type: str, subtype: str, text: str | None = None, *, discriminator: str | None = None
) -> str:
    """Deterministic 32-hex-char signature of a failure (sha256 over type, subtype and the normalized text).

    ``discriminator`` optionally separates failures whose normalized text is identical but whose remedy
    differs (e.g. the name of a missing module, which quoted-value normalization removes).
    """
    parts = [str(failure_type), str(subtype), _signature_core(text), (discriminator or "").strip().lower()]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _shingles(text: str | None, k: int = 3) -> frozenset[tuple[str, ...]]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(normalize_traceback(text))]
    if not tokens:
        return frozenset()
    if len(tokens) < k:
        return frozenset({tuple(tokens)})
    return frozenset(tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1))


def _jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a_text: str | None, b_text: str | None) -> float:
    """Token-shingle (k=3) Jaccard similarity of two normalized error texts, in ``[0, 1]``.

    Empty inputs carry no information and score ``0.0``.
    """
    return round(_jaccard(_shingles(a_text), _shingles(b_text)), 6)


def rank_similar(
    target: str | None,
    candidates: Iterable[tuple[str, str | None, str | None]],
    threshold: float = 0.5,
    *,
    target_signature: str | None = None,
    limit: int | None = None,
) -> list[tuple[str, float]]:
    """Score ``(id, signature, text)`` candidates against ``target``; identical signatures score 1.0.

    Returns ``(id, score)`` pairs with ``score >= threshold`` ordered by score (desc) then id (asc).
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be within [0, 1]")
    target_shingles = _shingles(target)
    scored: dict[str, float] = {}
    for cand_id, signature, text in candidates:
        if target_signature and signature and signature == target_signature:
            score = 1.0
        else:
            score = round(_jaccard(target_shingles, _shingles(text)), 6)
        if score >= threshold and score > scored.get(str(cand_id), -1.0):
            scored[str(cand_id)] = score
    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit] if limit is not None else ranked


def find_similar(
    target: str | None,
    candidates: Iterable[tuple[str, str | None, str | None]],
    threshold: float = 0.5,
    *,
    target_signature: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Ids of candidates similar to ``target`` (see :func:`rank_similar`), most similar first."""
    return [
        cand_id
        for cand_id, _ in rank_similar(target, candidates, threshold, target_signature=target_signature, limit=limit)
    ]


# =============================================================================================
# Derived facts
# =============================================================================================
def _is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


_NON_FINITE_STRINGS = frozenset({"nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"})


def non_finite_metric_keys(metrics: Mapping[str, Any] | None, *, max_items: int = 1000) -> list[str]:
    """Dotted paths of metric values that are NaN/±inf (floats or their string spellings), sorted."""
    if not metrics:
        return []
    found: list[str] = []
    stack: list[tuple[str, Any]] = [(str(k), v) for k, v in metrics.items()]
    seen = 0
    while stack and seen < max_items:
        path, value = stack.pop()
        seen += 1
        if isinstance(value, Mapping):
            stack.extend((f"{path}.{k}", v) for k, v in value.items())
        elif isinstance(value, list | tuple):
            stack.extend((f"{path}[{i}]", v) for i, v in enumerate(value))
        elif (isinstance(value, float) and not math.isfinite(value)) or (
            isinstance(value, str) and value.strip().lower() in _NON_FINITE_STRINGS
        ):
            found.append(path)
    return sorted(found)


def _is_data_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in _DATA_PATH_MARKERS) or lowered.startswith(_DATA_PATH_PREFIXES)


@dataclass
class _Facts:
    signals: FailureSignals
    text: str
    exc_qualname: str | None
    exc_type: str | None
    exc_message: str
    frames: list[tuple[str, str]]
    innermost_path: str | None
    in_library: bool
    module: str | None
    missing_path: str | None
    non_finite: list[str]
    validation_codes: list[str]
    has_error: bool
    values: dict[str, Any] = field(default_factory=dict)

    def contains(self, *needles: str) -> bool:
        return any(needle in self.text for needle in needles)

    def icontains(self, *needles: str) -> bool:
        lowered = self.text.lower()
        return any(needle.lower() in lowered for needle in needles)


def _derive_facts(signals: FailureSignals) -> _Facts:
    traceback = _tail(signals.traceback)
    logs = _tail(signals.logs)
    message = _tail(signals.error_message, 8_000)
    text = "\n".join(part for part in (signals.error_class or "", message, traceback, logs) if part)

    exc: tuple[str, str] | None = None
    for source in (traceback, message, logs):
        if source:
            exc = _parse_exception_line(source)
            if exc:
                break
    qualname: str | None = signals.error_class or (exc[0] if exc else None)
    exc_type = qualname.rsplit(".", 1)[-1] if qualname else None
    exc_message = exc[1] if exc else (message.strip() if message else "")

    frame_source = traceback or logs
    frames = _parse_frames(frame_source) if frame_source else []
    innermost = _innermost_frame_path(frame_source) if frame_source else None

    module: str | None = None
    mod_match = _MISSING_MODULE_RE.search(text) or _CANNOT_IMPORT_RE.search(text)
    if mod_match:
        module = mod_match.group("mod").split(".")[0]

    missing_path: str | None = None
    path_match = _MISSING_PATH_RE.search(text)
    if path_match:
        missing_path = path_match.group("q1") or path_match.group("q2") or path_match.group("bare")

    codes = sorted(
        {
            code.strip().upper()
            for code in signals.validation_issue_codes
            if code.strip() and not code.strip().lower().startswith(("warning:", "warn:"))
        }
    )
    has_error = bool(
        signals.error_message
        or signals.error_class
        or signals.traceback
        or (signals.exit_code not in (None, 0))
        or signals.oom_killed
        or signals.timed_out
    )
    facts = _Facts(
        signals=signals,
        text=text,
        exc_qualname=qualname,
        exc_type=exc_type,
        exc_message=exc_message,
        frames=frames,
        innermost_path=innermost,
        in_library=bool(innermost and _LIBRARY_PATH_RE.search(innermost)),
        module=module,
        missing_path=missing_path,
        non_finite=non_finite_metric_keys(signals.metrics),
        validation_codes=codes,
        has_error=has_error,
    )
    facts.values = {
        "stage": signals.stage,
        "exit_code": signals.exit_code if signals.exit_code is not None else "unknown",
        "exc_type": exc_type or "an error",
        "exc_message": _normalize_line(exc_message)[:200] if exc_message else "no message",
        "module": module or "unknown",
        "path": _basename(missing_path) if missing_path else "unknown",
        "metrics": ", ".join(facts.non_finite[:10]) or "none",
        "tool_name": signals.tool_name or "unknown",
        "provider_error_class": signals.provider_error_class or exc_type or "unknown",
        "verdict": signals.reproduction_verdict or "unknown",
        "p_value": _fmt(signals.p_value),
        "alpha": _fmt(signals.alpha),
        "power": _fmt(signals.power),
        "n_seeds": signals.n_seeds if signals.n_seeds is not None else "unknown",
        "required_seeds": signals.required_seeds if signals.required_seeds is not None else "unknown",
        "repeated": signals.repeated_failures_for_strategy,
        "codes": ", ".join(codes) or "none",
        "policy_decision": signals.policy_decision or "deny",
        "frame": f"{frames[-1][0]}:{frames[-1][1]}" if frames else "unknown location",
    }
    return facts


def _fmt(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "missing"
    return f"{value:.4g}"


# =============================================================================================
# Rule table
# =============================================================================================
@dataclass(frozen=True)
class FailureRule:
    id: str
    failure_type: FailureType
    subtype: str | Callable[[_Facts], str]
    confidence: float | Callable[[_Facts], float]
    root_cause: str
    predicate: Callable[[_Facts], bool]

    def resolve_subtype(self, facts: _Facts) -> str:
        return self.subtype(facts) if callable(self.subtype) else self.subtype

    def resolve_confidence(self, facts: _Facts) -> float:
        value = self.confidence(facts) if callable(self.confidence) else self.confidence
        return round(min(1.0, max(0.0, value)), 4)


_DENY_DECISIONS = frozenset({"deny", "denied", "block", "blocked"})
_CODE_RUNTIME_EXCEPTIONS = frozenset(
    {"NameError", "UnboundLocalError", "TypeError", "AttributeError", "IndexError", "KeyError", "ZeroDivisionError"}
)
_CODE_GENERIC_EXCEPTIONS = frozenset(
    {
        "ValueError",
        "RuntimeError",
        "AssertionError",
        "RecursionError",
        "NotImplementedError",
        "FileNotFoundError",
        "OverflowError",
        "StopIteration",
        "LookupError",
        "OSError",
        "PermissionError",
    }
)
_SHAPE_PATTERNS = re.compile(
    r"shapes? .{0,60}not aligned|size mismatch|shape mismatch|dimension mismatch|dimensions? must match|"
    r"inconsistent numbers of samples|shapes cannot be multiplied|could not be broadcast together|"
    r"does not match length of index|expected .{0,40}features|has \d+ features, but|input dimension",
    re.IGNORECASE,
)
_EMPTY_DATA_PATTERNS = re.compile(
    r"EmptyDataError|No columns to parse from file|Found array with 0 sample|empty dataset|dataset is empty|"
    r"with 0 rows|zero-size array",
    re.IGNORECASE,
)
_TYPE_CONVERSION_PATTERNS = re.compile(
    r"could not convert string to float|invalid literal for int\(\)|Unable to parse string|"
    r"could not convert .{0,40} to numeric|cannot convert float NaN to integer",
    re.IGNORECASE,
)


def _model_subtype(facts: _Facts) -> str:
    cls = (facts.signals.provider_error_class or facts.exc_type or "").lower()
    if "ratelimit" in cls or "rate_limit" in cls or "quota" in cls or "429" in cls:
        return "rate_limited"
    if "timeout" in cls or "deadline" in cls:
        return "timeout"
    if "unavailable" in cls or "connection" in cls or "overload" in cls or "503" in cls:
        return "unavailable"
    if "context" in cls or "toolong" in cls or "too_long" in cls or "length" in cls:
        return "context_overflow"
    if "safety" in cls or "blocked" in cls or "filter" in cls or "refus" in cls:
        return "content_blocked"
    if "auth" in cls or "permission" in cls or "401" in cls or "403" in cls:
        return "auth_error"
    return "provider_error"


def _code_runtime_confidence(facts: _Facts) -> float:
    return 0.6 if facts.in_library else 0.8


def _hypothesis_confidence(facts: _Facts) -> float:
    power = facts.signals.power
    base = 0.6 + 0.35 * power if power is not None else 0.75
    if facts.signals.effect_direction == "opposite":
        base += 0.03
    return min(0.97, base)


def _strategy_confidence(facts: _Facts) -> float:
    extra = facts.signals.repeated_failures_for_strategy - STRATEGY_REPEAT_THRESHOLD
    return 0.8 + min(0.15, 0.03 * max(0, extra))


def _design_subtype(facts: _Facts) -> str:
    return facts.validation_codes[0].lower() if len(facts.validation_codes) == 1 else "multiple_issues"


def _p_finite(facts: _Facts) -> bool:
    p = facts.signals.p_value
    return p is not None and math.isfinite(p)


def _has_exception(facts: _Facts, *names: str) -> bool:
    return facts.exc_type in names or any(f"{name}:" in facts.text or f"{name}\n" in facts.text for name in names)


_FT = FailureType

#: Ordered rule table. The first matching rule decides; all matches are reported.
RULES: tuple[FailureRule, ...] = (
    # --- governance ---------------------------------------------------------------------------
    FailureRule(
        "policy.denied",
        _FT.POLICY_FAILURE,
        "denied",
        0.97,
        "The action was denied by governance policy (decision: {policy_decision}); it must not be retried "
        "automatically.",
        lambda f: (f.signals.policy_decision or "").lower() in _DENY_DECISIONS or f.exc_type == "PolicyDenied",
    ),
    FailureRule(
        "policy.budget_exceeded",
        _FT.POLICY_FAILURE,
        "budget_exceeded",
        0.95,
        "A governance budget or quota was exhausted ({exc_type}).",
        lambda f: f.exc_type in {"BudgetExceeded", "QuotaExceeded"},
    ),
    FailureRule(
        "policy.approval_required",
        _FT.POLICY_FAILURE,
        "approval_required",
        0.9,
        "The action requires a human approval that was not granted.",
        lambda f: (f.signals.policy_decision or "").lower() == "require_approval" or f.exc_type == "ApprovalRequired",
    ),
    FailureRule(
        "policy.stage",
        _FT.POLICY_FAILURE,
        "denied",
        0.7,
        "The failure occurred during policy evaluation ({exc_type}).",
        lambda f: f.signals.stage == "policy",
    ),
    # --- strategy (explicit) -------------------------------------------------------------------
    FailureRule(
        "strategy.repeated_failures",
        _FT.STRATEGY_FAILURE,
        "repeated_failures",
        _strategy_confidence,
        "The strategy produced {repeated} failures; the strategy itself is the likely cause.",
        lambda f: f.signals.stage == "strategy"
        and f.signals.repeated_failures_for_strategy >= STRATEGY_REPEAT_THRESHOLD,
    ),
    # --- design validation ---------------------------------------------------------------------
    FailureRule(
        "design.validation_errors",
        _FT.EXPERIMENT_DESIGN_FAILURE,
        _design_subtype,
        0.92,
        "The experiment design failed validation ({codes}).",
        lambda f: bool(f.validation_codes),
    ),
    # --- model provider ------------------------------------------------------------------------
    FailureRule(
        "model.invalid_output",
        _FT.MODEL_FAILURE,
        "invalid_output",
        0.9,
        "The model returned output that did not satisfy the required schema.",
        lambda f: "LLMOutputInvalid" in {f.exc_type, f.signals.provider_error_class},
    ),
    FailureRule(
        "model.provider_error",
        _FT.MODEL_FAILURE,
        _model_subtype,
        0.9,
        "The model provider call failed ({provider_error_class}).",
        lambda f: bool(f.signals.provider_error_class),
    ),
    FailureRule(
        "model.unavailable",
        _FT.MODEL_FAILURE,
        "unavailable",
        0.88,
        "No model provider was available for the request ({exc_type}).",
        lambda f: f.exc_type == "ModelUnavailable",
    ),
    # --- tools ---------------------------------------------------------------------------------
    FailureRule(
        "tool.timeout",
        _FT.TOOL_FAILURE,
        "timeout",
        0.9,
        "The tool '{tool_name}' timed out.",
        lambda f: f.signals.stage == "tool" and f.signals.timed_out,
    ),
    FailureRule(
        "tool.error",
        _FT.TOOL_FAILURE,
        "error",
        0.85,
        "The tool '{tool_name}' failed with {exc_type}: {exc_message}.",
        lambda f: f.signals.stage == "tool",
    ),
    FailureRule(
        "tool.named_error",
        _FT.TOOL_FAILURE,
        "error",
        0.7,
        "The tool '{tool_name}' failed with {exc_type}: {exc_message}.",
        lambda f: bool(f.signals.tool_name) and f.has_error and f.signals.stage in {"agent", "tool"},
    ),
    # --- evaluator -----------------------------------------------------------------------------
    FailureRule(
        "evaluation.evaluator_error",
        _FT.EVALUATION_FAILURE,
        "evaluator_error",
        0.85,
        "The evaluator failed with {exc_type}: {exc_message}.",
        lambda f: f.signals.stage == "evaluation"
        and bool(f.signals.error_message or f.signals.error_class or f.signals.traceback),
    ),
    # --- resources -----------------------------------------------------------------------------
    FailureRule(
        "resource.oom_killed",
        _FT.RESOURCE_FAILURE,
        "oom",
        0.97,
        "The process was killed by the out-of-memory killer (exit code {exit_code}); the memory limit was exceeded.",
        lambda f: f.signals.oom_killed,
    ),
    FailureRule(
        "resource.gpu_oom",
        _FT.RESOURCE_FAILURE,
        "gpu_oom",
        0.93,
        "The GPU ran out of memory.",
        lambda f: f.icontains("CUDA out of memory", "CUBLAS_STATUS_ALLOC_FAILED", "cuda.OutOfMemoryError")
        or (f.exc_type == "OutOfMemoryError" and f.icontains("cuda", "gpu")),
    ),
    FailureRule(
        "resource.memory_error",
        _FT.RESOURCE_FAILURE,
        "oom",
        0.9,
        "The process ran out of memory ({exc_type}).",
        lambda f: f.exc_type == "MemoryError"
        or f.contains("MemoryError", "Cannot allocate memory", "std::bad_alloc", "Unable to allocate"),
    ),
    FailureRule(
        "resource.timeout",
        _FT.RESOURCE_FAILURE,
        "timeout",
        0.95,
        "The job exceeded its time limit and was stopped.",
        lambda f: f.signals.timed_out,
    ),
    FailureRule(
        "resource.disk_full",
        _FT.RESOURCE_FAILURE,
        "disk_full",
        0.93,
        "The job ran out of disk space.",
        lambda f: f.icontains("No space left on device", "Disk quota exceeded", "[Errno 28]"),
    ),
    FailureRule(
        "resource.sigkill",
        _FT.RESOURCE_FAILURE,
        "oom",
        0.75,
        "The process was killed with SIGKILL (exit code {exit_code}), most likely by the out-of-memory killer.",
        lambda f: f.signals.exit_code in (137, -9),
    ),
    # --- data ----------------------------------------------------------------------------------
    FailureRule(
        "data.missing_input",
        _FT.DATA_FAILURE,
        "missing_input",
        0.88,
        "An input data file was not found ({path}); the dataset may not be mounted.",
        lambda f: bool(f.missing_path and _is_data_path(f.missing_path))
        and (f.exc_type in {"FileNotFoundError", "OSError", "IOError"} or f.contains("No such file or directory")),
    ),
    FailureRule(
        "data.empty_dataset",
        _FT.DATA_FAILURE,
        "empty_dataset",
        0.85,
        "The dataset (or a split of it) was empty.",
        lambda f: bool(_EMPTY_DATA_PATTERNS.search(f.text)),
    ),
    FailureRule(
        "data.parse_error",
        _FT.DATA_FAILURE,
        "parse_error",
        0.85,
        "The input data could not be parsed ({exc_type}).",
        lambda f: f.exc_type in {"ParserError", "ParseError"}
        or f.contains("Error tokenizing data", "_csv.Error", "csv.Error"),
    ),
    FailureRule(
        "data.encoding",
        _FT.DATA_FAILURE,
        "encoding_error",
        0.85,
        "The input data has an unexpected text encoding ({exc_type}).",
        lambda f: _has_exception(f, "UnicodeDecodeError"),
    ),
    FailureRule(
        "data.type_conversion",
        _FT.DATA_FAILURE,
        "type_conversion",
        0.82,
        "A data value could not be converted to the expected type: {exc_message}.",
        lambda f: bool(_TYPE_CONVERSION_PATTERNS.search(f.text)),
    ),
    FailureRule(
        "data.shape_mismatch",
        _FT.DATA_FAILURE,
        "shape_mismatch",
        0.78,
        "Array or tensor shapes did not match: {exc_message}.",
        lambda f: bool(_SHAPE_PATTERNS.search(f.text)),
    ),
    # --- code ----------------------------------------------------------------------------------
    FailureRule(
        "code.dependency",
        _FT.CODE_FAILURE,
        "dependency",
        0.92,
        "A required Python module could not be imported (module: {module}).",
        lambda f: f.exc_type in {"ModuleNotFoundError", "ImportError"} or f.module is not None,
    ),
    FailureRule(
        "code.syntax",
        _FT.CODE_FAILURE,
        "syntax",
        0.95,
        "The code has a syntax error ({exc_type}: {exc_message}).",
        lambda f: f.exc_type in {"SyntaxError", "IndentationError", "TabError"},
    ),
    FailureRule(
        "code.runtime",
        _FT.CODE_FAILURE,
        "runtime",
        _code_runtime_confidence,
        "The code raised {exc_type} at {frame}: {exc_message}.",
        lambda f: f.exc_type in _CODE_RUNTIME_EXCEPTIONS,
    ),
    FailureRule(
        "code.runtime_generic",
        _FT.CODE_FAILURE,
        "runtime",
        0.6,
        "The code raised {exc_type} at {frame}: {exc_message}.",
        lambda f: f.exc_type in _CODE_GENERIC_EXCEPTIONS,
    ),
    # --- evaluation outputs --------------------------------------------------------------------
    FailureRule(
        "evaluation.metrics_missing",
        _FT.EVALUATION_FAILURE,
        "metrics_missing",
        0.9,
        "The run exited successfully but did not write its metrics file.",
        lambda f: f.signals.exit_code == 0 and f.signals.metrics_file_present is False,
    ),
    FailureRule(
        "evaluation.non_finite_metrics",
        _FT.EVALUATION_FAILURE,
        "non_finite_metrics",
        0.88,
        "Reported metrics contain non-finite values (NaN/inf): {metrics}.",
        lambda f: bool(f.non_finite),
    ),
    FailureRule(
        "evaluation.metrics_empty",
        _FT.EVALUATION_FAILURE,
        "metrics_empty",
        0.8,
        "The metrics file was present but empty.",
        lambda f: f.signals.metrics_file_present is True and f.signals.metrics is not None and not f.signals.metrics,
    ),
    # --- reproduction --------------------------------------------------------------------------
    FailureRule(
        "reproduction.not_reproduced",
        _FT.REPRODUCIBILITY_FAILURE,
        "not_reproduced",
        0.9,
        "An independent reproduction did not reproduce the original result (verdict: {verdict}).",
        lambda f: (f.signals.reproduction_verdict or "").lower() == "not_reproduced",
    ),
    FailureRule(
        "reproduction.partially_reproduced",
        _FT.REPRODUCIBILITY_FAILURE,
        "partially_reproduced",
        0.75,
        "An independent reproduction only partially reproduced the original result (verdict: {verdict}).",
        lambda f: (f.signals.reproduction_verdict or "").lower() in {"partially_reproduced", "partially", "partial"},
    ),
    # --- statistics ----------------------------------------------------------------------------
    FailureRule(
        "statistics.insufficient_seeds",
        _FT.STATISTICAL_FAILURE,
        "insufficient_seeds",
        0.85,
        "Only {n_seeds} seeds were run but the statistical plan requires {required_seeds}.",
        lambda f: f.signals.n_seeds is not None
        and f.signals.required_seeds is not None
        and f.signals.n_seeds < f.signals.required_seeds,
    ),
    FailureRule(
        "statistics.underpowered",
        _FT.STATISTICAL_FAILURE,
        "underpowered",
        0.8,
        "The comparison was not adequately powered (power: {power}); no conclusion can be drawn.",
        lambda f: f.signals.adequately_powered is False,
    ),
    FailureRule(
        "statistics.p_value_missing",
        _FT.STATISTICAL_FAILURE,
        "p_value_missing",
        0.8,
        "The statistical analysis produced no valid p-value.",
        lambda f: f.signals.stage == "statistics" and not _p_finite(f),
    ),
    # --- hypothesis (legitimate negative results) ----------------------------------------------
    FailureRule(
        "hypothesis.opposite_effect",
        _FT.HYPOTHESIS_FAILURE,
        "opposite_effect",
        _hypothesis_confidence,
        "An adequately powered test found a significant effect opposite to the hypothesis (p = {p_value}, "
        "alpha = {alpha}); this is a legitimate negative result.",
        lambda f: f.signals.adequately_powered is True
        and f.signals.effect_direction == "opposite"
        and _p_finite(f)
        and (f.signals.p_value or 0.0) < f.signals.alpha,
    ),
    FailureRule(
        "hypothesis.non_significant",
        _FT.HYPOTHESIS_FAILURE,
        "non_significant",
        _hypothesis_confidence,
        "An adequately powered test found no significant effect (p = {p_value}, alpha = {alpha}); this is a "
        "legitimate negative result.",
        lambda f: f.signals.adequately_powered is True and _p_finite(f) and (f.signals.p_value or 0.0) >= f.signals.alpha,
    ),
    # --- strategy (any stage) ------------------------------------------------------------------
    FailureRule(
        "strategy.repeated_failures_any_stage",
        _FT.STRATEGY_FAILURE,
        "repeated_failures",
        0.6,
        "The strategy has failed {repeated} times; the strategy itself may be the cause.",
        lambda f: f.signals.repeated_failures_for_strategy >= STRATEGY_REPEAT_THRESHOLD,
    ),
)

_FALLBACK_RULE = "fallback.unknown"


def classify_failure(signals: FailureSignals) -> Classification:
    """Classify a failure with the ordered :data:`RULES` table (first match wins; all matches reported)."""
    facts = _derive_facts(signals)
    matched = [rule for rule in RULES if rule.predicate(facts)]
    if matched:
        winner = matched[0]
        failure_type = winner.failure_type
        subtype = winner.resolve_subtype(facts)
        confidence = winner.resolve_confidence(facts)
        root_cause = winner.root_cause.format_map(facts.values)
        matched_ids = [rule.id for rule in matched]
    else:
        failure_type = FailureType.CODE_FAILURE
        subtype = "unknown"
        confidence = 0.3 if facts.has_error else 0.15
        detail = (
            f"{facts.values['exc_type']}: {facts.values['exc_message']}"
            if facts.exc_type
            else f"exit code {facts.values['exit_code']}"
        )
        root_cause = f"No classification rule matched ({detail}); manual investigation is required."
        matched_ids = [_FALLBACK_RULE]

    evidence: dict[str, Any] = {"engine_version": FAILURE_ENGINE_VERSION, "rule": matched_ids[0], "stage": signals.stage}
    optional: dict[str, Any] = {
        "exit_code": signals.exit_code,
        "oom_killed": signals.oom_killed or None,
        "timed_out": signals.timed_out or None,
        "exception_type": facts.exc_qualname,
        "exception_message": facts.values["exc_message"] if facts.exc_message else None,
        "innermost_frame": {"file": facts.frames[-1][0], "function": facts.frames[-1][1]} if facts.frames else None,
        "in_library_code": facts.in_library if facts.frames else None,
        "module": facts.module,
        "missing_path": _basename(facts.missing_path) if facts.missing_path else None,
        "non_finite_metrics": facts.non_finite or None,
        "validation_issue_codes": facts.validation_codes or None,
        "policy_decision": signals.policy_decision,
        "provider_error_class": signals.provider_error_class,
        "tool_name": signals.tool_name,
        "reproduction_verdict": signals.reproduction_verdict,
        "p_value": signals.p_value if signals.p_value is not None and math.isfinite(signals.p_value) else None,
        "adequately_powered": signals.adequately_powered,
        "power": signals.power,
        "n_seeds": signals.n_seeds,
        "required_seeds": signals.required_seeds,
        "repeated_failures_for_strategy": signals.repeated_failures_for_strategy or None,
    }
    evidence.update({key: value for key, value in optional.items() if value is not None})
    secondary: list[str] = []
    for rule in matched[1:]:
        if rule.failure_type != failure_type and rule.failure_type.value not in secondary:
            secondary.append(rule.failure_type.value)
    if secondary:
        evidence["secondary_types"] = secondary

    signature_text = signals.traceback or signals.error_message or signals.logs
    discriminator = facts.module if subtype == "dependency" else None
    return Classification(
        failure_type=failure_type,
        subtype=subtype,
        confidence=confidence,
        matched_rules=matched_ids,
        root_cause=root_cause,
        evidence=evidence,
        signature=failure_signature(failure_type.value, subtype, signature_text, discriminator=discriminator),
    )


# =============================================================================================
# Recovery playbook
# =============================================================================================
class RecoveryAction(StrEnum):
    INCREASE_MEMORY = "increase_memory"
    REDUCE_BATCH_SIZE = "reduce_batch_size"
    INCREASE_DISK = "increase_disk"
    INCREASE_TIMEOUT = "increase_timeout"
    ADD_DEPENDENCY = "add_dependency"
    DEPENDENCY_PIN_REQUIRED = "dependency_pin_required"
    FIX_CODE = "fix_code"
    VERIFY_DATASET_MOUNTS = "verify_dataset_mounts"
    INSPECT_DATA = "inspect_data"
    ADD_NUMERIC_GUARDS = "add_numeric_guards"
    WRITE_METRICS_FILE = "write_metrics_file"
    INSPECT_EVALUATOR = "inspect_evaluator"
    INCREASE_SEEDS = "increase_seeds"
    REVISE_STATISTICAL_PLAN = "revise_statistical_plan"
    RUN_STATISTICAL_TEST = "run_statistical_test"
    PIN_ENVIRONMENT = "pin_environment"
    HUMAN_REVIEW = "human_review_required"
    RECORD_NEGATIVE_RESULT = "record_negative_result"
    RETRY_LATER = "retry_later"
    REVISE_OUTPUT_SCHEMA = "revise_output_schema"
    RETRY_TOOL = "retry_tool"
    INSPECT_TOOL = "inspect_tool"
    REVISE_DESIGN = "revise_design"
    REVIEW_STRATEGY = "review_strategy"
    NO_AUTOMATIC_FIX = "no_automatic_fix"
    MANUAL_INVESTIGATION = "manual_investigation"


#: Import name → PyPI distribution name for modules whose import name is ambiguous or commonly missing.
IMPORT_PACKAGE_MAP: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python-headless",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "skimage": "scikit-image",
    "torch": "torch",
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
}

#: Environment flags that make common numeric stacks deterministic.
DETERMINISTIC_FLAGS: tuple[str, ...] = (
    "PYTHONHASHSEED=0",
    "CUBLAS_WORKSPACE_CONFIG=:4096:8",
    "OMP_NUM_THREADS=1",
    "TF_DETERMINISTIC_OPS=1",
)
DEFAULT_REPRODUCTION_SEEDS = 3

_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]*$")
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class RecoveryContext(BaseModel):
    """The experiment's current configuration and the platform limits a recovery must respect.

    ``limits`` accepts ``memory_mb``/``max_memory_mb``, ``disk_mb``/``max_disk_mb``,
    ``timeout_seconds``/``max_timeout_seconds`` and ``seeds``/``max_seeds``.
    """

    model_config = ConfigDict(extra="forbid")

    resources: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    known_versions: dict[str, str] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=list)
    statistical_plan: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    image_digest: str | None = None
    deterministic_flags: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecoveryProposal(BaseModel):
    action: RecoveryAction
    spec_patch: dict[str, Any] = Field(default_factory=dict, description="JSON merge-patch for the experiment spec.")
    rationale: str
    requires_approval: bool
    confidence: float = Field(ge=0.0, le=1.0)
    failure_type: FailureType
    subtype: str

    @property
    def automatic(self) -> bool:
        """True when the proposal carries a concrete patch that may be applied without a human."""
        return bool(self.spec_patch) and not self.requires_approval


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _limit(limits: Mapping[str, Any], key: str) -> int | None:
    for candidate in (key, f"max_{key}"):
        value = _as_int(limits.get(candidate))
        if value is not None and value > 0:
            return value
    return None


def _normalize_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str | None:
    match = _REQ_NAME_RE.match(requirement)
    return _normalize_dist(match.group(1)) if match else None


def merge_patch(target: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply an RFC 7386 JSON merge-patch (``None`` deletes a key; nested mappings merge; lists replace)."""
    result: dict[str, Any] = dict(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping):
            existing = result.get(key)
            result[key] = merge_patch(existing if isinstance(existing, Mapping) else {}, value)
        else:
            result[key] = value
    return result


def _proposal(
    classification: Classification,
    action: RecoveryAction,
    rationale: str,
    *,
    patch: dict[str, Any] | None = None,
    requires_approval: bool = False,
    factor: float = 0.8,
) -> RecoveryProposal:
    return RecoveryProposal(
        action=action,
        spec_patch=patch or {},
        rationale=rationale,
        requires_approval=requires_approval,
        confidence=round(min(1.0, max(0.0, classification.confidence * factor)), 4),
        failure_type=classification.failure_type,
        subtype=classification.subtype,
    )


def _halve_batch(classification: Classification, ctx: RecoveryContext, reason: str) -> RecoveryProposal | None:
    batch = _as_int(ctx.parameters.get("batch_size"))
    if batch is None or batch < 2:
        return None
    new_batch = batch // 2
    return _proposal(
        classification,
        RecoveryAction.REDUCE_BATCH_SIZE,
        f"{reason} Halving parameters.batch_size from {batch} to {new_batch} reduces peak memory.",
        patch={"parameters": {"batch_size": new_batch}},
        factor=0.7,
    )


def _scale_resource(
    classification: Classification,
    ctx: RecoveryContext,
    *,
    key: str,
    action: RecoveryAction,
    label: str,
    allow_batch_fallback: bool,
) -> RecoveryProposal:
    current = _as_int(ctx.resources.get(key))
    cap = _limit(ctx.limits, key)
    if current is None or current <= 0:
        fallback = _halve_batch(classification, ctx, f"The current {label} allocation is unknown.")
        if allow_batch_fallback and fallback:
            return fallback
        return _proposal(
            classification,
            RecoveryAction.NO_AUTOMATIC_FIX,
            f"The current {label} allocation is unknown, so no bounded increase can be proposed.",
            requires_approval=True,
            factor=0.5,
        )
    if cap is not None and current >= cap:
        reason = f"{label.capitalize()} is already at the platform limit ({cap} MB)."
        fallback = _halve_batch(classification, ctx, reason) if allow_batch_fallback else None
        if fallback:
            return fallback
        return _proposal(
            classification,
            RecoveryAction.NO_AUTOMATIC_FIX,
            f"{reason} The workload must be reduced by a human or agent (no automatic fix).",
            requires_approval=True,
            factor=0.5,
        )
    new_value = current * 2 if cap is None else min(current * 2, cap)
    rationale = f"Doubling {label} from {current} MB to {new_value} MB"
    rationale += f" (capped at the {cap} MB limit)." if cap is not None else " (no platform limit supplied)."
    return _proposal(
        classification,
        action,
        rationale,
        patch={"resources": {key: new_value}},
        requires_approval=cap is None,
        factor=0.8,
    )


def _recover_timeout(classification: Classification, ctx: RecoveryContext) -> RecoveryProposal:
    current = ctx.timeout_seconds if ctx.timeout_seconds is not None else _as_int(ctx.resources.get("timeout_seconds"))
    cap = _limit(ctx.limits, "timeout_seconds")
    if current is None or current <= 0:
        return _proposal(
            classification,
            RecoveryAction.NO_AUTOMATIC_FIX,
            "The current timeout is unknown, so no bounded increase can be proposed.",
            requires_approval=True,
            factor=0.5,
        )
    if cap is not None and current >= cap:
        return _proposal(
            classification,
            RecoveryAction.NO_AUTOMATIC_FIX,
            f"The timeout is already at the platform limit ({cap} s); the workload must be reduced (no automatic "
            "fix).",
            requires_approval=True,
            factor=0.5,
        )
    new_value = current * 2 if cap is None else min(current * 2, cap)
    rationale = f"Doubling the timeout from {current} s to {new_value} s"
    rationale += f" (capped at the {cap} s limit)." if cap is not None else " (no platform limit supplied)."
    return _proposal(
        classification,
        RecoveryAction.INCREASE_TIMEOUT,
        rationale,
        patch={"timeout_seconds": new_value},
        requires_approval=cap is None,
        factor=0.75,
    )


def _recover_dependency(classification: Classification, ctx: RecoveryContext) -> RecoveryProposal:
    module = str(classification.evidence.get("module") or "")
    if not _MODULE_NAME_RE.match(module):
        return _proposal(
            classification,
            RecoveryAction.DEPENDENCY_PIN_REQUIRED,
            "The missing module could not be identified; a human must add and pin the dependency.",
            requires_approval=True,
            factor=0.5,
        )
    mapped = IMPORT_PACKAGE_MAP.get(module)
    package = mapped or module
    known = {_normalize_dist(name): version for name, version in ctx.known_versions.items()}
    version = known.get(_normalize_dist(package))
    existing = {_requirement_name(dep) for dep in ctx.dependencies}
    if _normalize_dist(package) in existing:
        return _proposal(
            classification,
            RecoveryAction.NO_AUTOMATIC_FIX,
            f"'{package}' is already declared but '{module}' still failed to import; the image or the declared "
            "version must be checked by a human.",
            requires_approval=True,
            factor=0.5,
        )
    if not version or not _VERSION_RE.match(version) or not _PACKAGE_NAME_RE.match(package):
        return _proposal(
            classification,
            RecoveryAction.DEPENDENCY_PIN_REQUIRED,
            f"Module '{module}' is missing (distribution '{package}'"
            + ("" if mapped else ", unverified name")
            + "); no pinned version is known, so a human must pin it (versions are never guessed).",
            requires_approval=True,
            factor=0.6,
        )
    requirement = f"{package}=={version}"
    rationale = f"Add the pinned dependency {requirement} for missing module '{module}'."
    if not mapped:
        rationale += " The import name is not in the known import→package map, so the distribution name needs review."
    return _proposal(
        classification,
        RecoveryAction.ADD_DEPENDENCY,
        rationale,
        patch={"environment": {"dependencies": [*ctx.dependencies, requirement]}},
        requires_approval=not mapped,
        factor=0.9 if mapped else 0.6,
    )


def _fill_seeds(seeds: Sequence[int], target: int) -> list[int]:
    out: list[int] = []
    for seed in seeds:
        if seed not in out:
            out.append(seed)
    candidate = 0
    while len(out) < target:
        if candidate not in out:
            out.append(candidate)
        candidate += 1
    return out


def _recover_statistics(classification: Classification, ctx: RecoveryContext) -> RecoveryProposal:
    if classification.subtype == "p_value_missing":
        return _proposal(
            classification,
            RecoveryAction.RUN_STATISTICAL_TEST,
            "Run the planned statistical test on the collected per-seed metrics before drawing conclusions.",
            factor=0.7,
        )
    planned = _as_int(ctx.statistical_plan.get("n_seeds"))
    current = len(dict.fromkeys(ctx.seeds))
    cap = _limit(ctx.limits, "seeds")
    target = planned if planned is not None else None
    if target is not None and cap is not None:
        target = min(target, cap)
    if target is None or target <= current:
        return _proposal(
            classification,
            RecoveryAction.REVISE_STATISTICAL_PLAN,
            "The run already has as many seeds as the plan allows; a larger sample needs a revised statistical "
            "plan approved by a human.",
            requires_approval=True,
            factor=0.6,
        )
    new_seeds = _fill_seeds(ctx.seeds, target)
    rationale = f"Increase the number of seeds from {current} to {target} to satisfy the statistical plan"
    rationale += f" (capped at the {cap}-seed limit)." if cap is not None and planned and planned > cap else "."
    return _proposal(classification, RecoveryAction.INCREASE_SEEDS, rationale, patch={"seeds": new_seeds}, factor=0.8)


def _recover_reproducibility(classification: Classification, ctx: RecoveryContext) -> RecoveryProposal:
    patch: dict[str, Any] = {}
    notes: list[str] = []
    if ctx.image_digest:
        patch["environment"] = {"image_digest": ctx.image_digest}
        notes.append(f"pin the image digest {ctx.image_digest}")
    else:
        notes.append("the image digest is unknown and must be resolved before re-running")
    flags = list(dict.fromkeys([*ctx.deterministic_flags, *DETERMINISTIC_FLAGS]))
    reproducibility: dict[str, Any] = {"deterministic_flags": flags}
    if ctx.image_digest:
        reproducibility["require_digest_pin"] = True
    patch["reproducibility"] = reproducibility
    notes.append("enable deterministic execution flags")
    if not ctx.seeds:
        planned = _as_int(ctx.statistical_plan.get("n_seeds")) or DEFAULT_REPRODUCTION_SEEDS
        cap = _limit(ctx.limits, "seeds")
        count = min(planned, cap) if cap is not None else planned
        patch["seeds"] = list(range(max(1, count)))
        notes.append(f"fix the seeds to {patch['seeds']}")
    else:
        notes.append("keep the fixed seeds")
    return _proposal(
        classification,
        RecoveryAction.PIN_ENVIRONMENT,
        "To make the result reproducible: " + "; ".join(notes) + ".",
        patch=patch,
        factor=0.7,
    )


_DATA_RATIONALE: dict[str, str] = {
    "empty_dataset": "The dataset split was empty; verify the dataset version and split selection.",
    "parse_error": "The data could not be parsed; check the delimiter, quoting and file format.",
    "encoding_error": "The data is not valid UTF-8; declare the correct encoding when reading it.",
    "type_conversion": "A column contains non-numeric values; clean or coerce the column explicitly.",
    "shape_mismatch": "Feature/label shapes differ; check preprocessing and column selection.",
}

_MODEL_RETRYABLE = frozenset({"rate_limited", "timeout", "unavailable", "provider_error"})


def propose_recovery(classification: Classification, context: RecoveryContext | None = None) -> RecoveryProposal:
    """Map a classification to a bounded, conservative recovery proposal (pure; never exceeds ``limits``)."""
    ctx = context or RecoveryContext()
    ftype, subtype = classification.failure_type, classification.subtype
    if ftype is FailureType.POLICY_FAILURE:
        return _proposal(
            classification,
            RecoveryAction.HUMAN_REVIEW,
            "Governance blocked this action; there is no automatic recovery. A human must review the policy "
            "decision.",
            requires_approval=True,
            factor=1.0,
        )
    if ftype is FailureType.HYPOTHESIS_FAILURE:
        return _proposal(
            classification,
            RecoveryAction.RECORD_NEGATIVE_RESULT,
            "The hypothesis was tested with adequate power and not supported; record the negative result instead of "
            "retrying.",
            factor=1.0,
        )
    if ftype is FailureType.RESOURCE_FAILURE:
        if subtype == "oom":
            return _scale_resource(
                classification,
                ctx,
                key="memory_mb",
                action=RecoveryAction.INCREASE_MEMORY,
                label="memory",
                allow_batch_fallback=True,
            )
        if subtype == "gpu_oom":
            fallback = _halve_batch(classification, ctx, "GPU memory cannot be increased automatically.")
            return fallback or _proposal(
                classification,
                RecoveryAction.NO_AUTOMATIC_FIX,
                "GPU memory was exhausted and no batch_size parameter is available to reduce (no automatic fix).",
                requires_approval=True,
                factor=0.5,
            )
        if subtype == "disk_full":
            return _scale_resource(
                classification,
                ctx,
                key="disk_mb",
                action=RecoveryAction.INCREASE_DISK,
                label="disk",
                allow_batch_fallback=False,
            )
        return _recover_timeout(classification, ctx)
    if ftype is FailureType.CODE_FAILURE:
        if subtype == "dependency":
            return _recover_dependency(classification, ctx)
        if subtype in {"syntax", "runtime"}:
            return _proposal(
                classification,
                RecoveryAction.FIX_CODE,
                f"The code failed ({subtype}); it must be corrected (no automatic patch). {classification.root_cause}",
                factor=0.6,
            )
        return _proposal(
            classification,
            RecoveryAction.MANUAL_INVESTIGATION,
            "The failure could not be classified; inspect the logs and traceback.",
            requires_approval=True,
            factor=0.5,
        )
    if ftype is FailureType.DATA_FAILURE:
        if subtype == "missing_input":
            return _proposal(
                classification,
                RecoveryAction.VERIFY_DATASET_MOUNTS,
                "Verify that every dataset version is mounted at the declared path (no automatic patch).",
                factor=0.7,
            )
        return _proposal(
            classification,
            RecoveryAction.INSPECT_DATA,
            _DATA_RATIONALE.get(subtype, "Inspect the input data."),
            factor=0.6,
        )
    if ftype is FailureType.EVALUATION_FAILURE:
        if subtype == "non_finite_metrics":
            return _proposal(
                classification,
                RecoveryAction.ADD_NUMERIC_GUARDS,
                "Add numeric guards (gradient clipping, epsilon in divisions/logs, NaN checks) and inspect the "
                "offending metrics (no automatic patch).",
                factor=0.6,
            )
        if subtype in {"metrics_missing", "metrics_empty"}:
            return _proposal(
                classification,
                RecoveryAction.WRITE_METRICS_FILE,
                "The code must write its metrics to /workspace/output/metrics.json (no automatic patch).",
                factor=0.7,
            )
        return _proposal(
            classification,
            RecoveryAction.INSPECT_EVALUATOR,
            "The evaluator failed; check its configuration and the artifacts it received.",
            factor=0.6,
        )
    if ftype is FailureType.STATISTICAL_FAILURE:
        return _recover_statistics(classification, ctx)
    if ftype is FailureType.REPRODUCIBILITY_FAILURE:
        return _recover_reproducibility(classification, ctx)
    if ftype is FailureType.MODEL_FAILURE:
        if subtype in _MODEL_RETRYABLE:
            return _proposal(
                classification,
                RecoveryAction.RETRY_LATER,
                "The model provider failed transiently; retry later with backoff (the gateway already retried).",
                factor=0.7,
            )
        if subtype in {"invalid_output", "context_overflow"}:
            return _proposal(
                classification,
                RecoveryAction.REVISE_OUTPUT_SCHEMA,
                "Tighten the prompt/output schema or reduce the context before retrying.",
                factor=0.6,
            )
        return _proposal(
            classification,
            RecoveryAction.HUMAN_REVIEW,
            "The provider refused or rejected the request; a human must review it.",
            requires_approval=True,
            factor=0.8,
        )
    if ftype is FailureType.TOOL_FAILURE:
        if subtype == "timeout":
            return _proposal(
                classification,
                RecoveryAction.RETRY_TOOL,
                "Retry the tool call once; persistent timeouts need a smaller request.",
                factor=0.6,
            )
        return _proposal(
            classification,
            RecoveryAction.INSPECT_TOOL,
            "Inspect the tool arguments and output; the tool reported an error.",
            factor=0.6,
        )
    if ftype is FailureType.EXPERIMENT_DESIGN_FAILURE:
        return _proposal(
            classification,
            RecoveryAction.REVISE_DESIGN,
            "Revise the experiment design to resolve the validation errors "
            f"({', '.join(classification.evidence.get('validation_issue_codes', [])) or classification.subtype}).",
            factor=0.8,
        )
    return _proposal(
        classification,
        RecoveryAction.REVIEW_STRATEGY,
        "The strategy fails repeatedly; review it for retirement or mutation (promotion changes need approval).",
        requires_approval=True,
        factor=0.8,
    )


# =============================================================================================
# Lessons
# =============================================================================================
class LessonDraft(BaseModel):
    failure_type: FailureType
    subtype: str
    signature: str
    statement: str
    recommendation: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    outcome: Literal["succeeded", "failed"] | None = None


def extract_lesson(
    classification: Classification,
    proposal: RecoveryProposal,
    outcome: Literal["succeeded", "failed"] | None = None,
) -> LessonDraft:
    """Turn a classification + recovery (+ its outcome when known) into a reusable lesson.

    Confidence starts from the proposal confidence, is raised halfway towards 1 when the recovery
    succeeded and halved when it failed.
    """
    base = proposal.confidence
    if outcome == "succeeded":
        confidence = base + (1.0 - base) * 0.5
        tail = f"Applying '{proposal.action.value}' resolved it."
    elif outcome == "failed":
        confidence = base * 0.5
        tail = f"Applying '{proposal.action.value}' did not resolve it; prefer a different remedy."
    else:
        confidence = base
        tail = f"Recommended remedy: '{proposal.action.value}' (not yet validated)."
    statement = f"{classification.failure_type.value}/{classification.subtype}: {classification.root_cause} {tail}"
    recommendation: dict[str, Any] = {
        "action": proposal.action.value,
        "spec_patch": proposal.spec_patch,
        "requires_approval": proposal.requires_approval,
        "rationale": proposal.rationale,
        "applies_to": {
            "failure_type": classification.failure_type.value,
            "subtype": classification.subtype,
            "signature": classification.signature,
        },
        "outcome": outcome,
        "engine_version": FAILURE_ENGINE_VERSION,
    }
    return LessonDraft(
        failure_type=classification.failure_type,
        subtype=classification.subtype,
        signature=classification.signature,
        statement=statement,
        recommendation=recommendation,
        confidence=round(min(1.0, max(0.0, confidence)), 4),
        outcome=outcome,
    )
