"""Built-in evaluators. Each is deterministic for a given (spec, artifacts, context, seed)."""

from __future__ import annotations

import ast
import csv
import io
import math
from collections.abc import Mapping
from typing import Any, ClassVar

from engines.common.stats import wilson_interval
from engines.lab.evaluation import statistics as st
from engines.lab.evaluation.base import EvaluationContext, EvaluationResult, Evaluator, EvaluatorRegistry
from engines.lab.evaluation.expressions import ExpressionError, evaluate
from engines.lab.experiments.spec import ExperimentSpec, MetricSpec, SuccessCriterion


def _mean(values: list[float] | None) -> float | None:
    if not values:
        return None
    clean = [v for v in values if v is not None and not math.isnan(v)]
    return sum(clean) / len(clean) if clean else None


def _better(direction: str, delta: float) -> float:
    """Orient a difference so that positive always means 'better'."""
    return delta if direction == "maximize" else -delta


def _check_absolute(criterion: SuccessCriterion, value: float) -> bool:
    t = criterion.threshold
    return {
        "gt": value > t,
        "gte": value >= t,
        "lt": value < t,
        "lte": value <= t,
    }[criterion.comparator]


def _read_pairs(data: bytes) -> tuple[list[str], list[str]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace")))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    true_col = next((c for c in ("y_true", "label", "target", "truth") if c in fields), None)
    pred_col = next((c for c in ("y_pred", "prediction", "pred", "predicted") if c in fields), None)
    if true_col is None or pred_col is None:
        raise ValueError("predictions file needs y_true/y_pred (or label/prediction) columns")
    mapping = {f.strip().lower(): f for f in reader.fieldnames or []}
    truths: list[str] = []
    preds: list[str] = []
    for row in reader:
        truths.append(str(row[mapping[true_col]]).strip())
        preds.append(str(row[mapping[pred_col]]).strip())
    return truths, preds


def _criteria_for(spec: ExperimentSpec, metrics: Mapping[str, float]) -> tuple[bool | None, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    for crit in spec.success_criteria:
        if crit.comparator not in ("gt", "gte", "lt", "lte") or crit.metric not in metrics:
            continue
        ok = _check_absolute(crit, metrics[crit.metric])
        checks.append(
            {
                "metric": crit.metric,
                "comparator": crit.comparator,
                "threshold": crit.threshold,
                "value": metrics[crit.metric],
                "passed": ok,
            }
        )
    if not checks:
        return None, checks
    return all(c["passed"] for c in checks), checks


class MetricEvaluator(Evaluator):
    key = "metric"
    version = "1.0.0"
    description = "Absolute success criteria (gt/gte/lt/lte) against the candidate's mean metric values."

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        means = {k: m for k, v in context.candidate.items() if (m := _mean(v)) is not None}
        passed, checks = _criteria_for(experiment, means)
        warnings: list[str] = []
        missing = [
            c.metric
            for c in experiment.success_criteria
            if c.comparator in ("gt", "gte", "lt", "lte") and c.metric not in means
        ]
        if missing:
            warnings.append(f"metrics not produced: {', '.join(sorted(set(missing)))}")
            passed = False if checks else None
        n = min((len(v) for v in context.candidate.values()), default=0)
        confidence = 0.0 if passed is None else min(0.95, 0.5 + 0.1 * n)
        if context.self_reported:
            warnings.append("metrics were self-reported by the experiment code")
            confidence *= 0.5
        return self.result(
            metrics=means,
            passed=passed,
            confidence=round(confidence, 3),
            warnings=warnings,
            evidence=[{"type": "criteria_checks", "checks": checks}],
        )


class BenchmarkEvaluator(Evaluator):
    key = "benchmark"
    version = "1.0.0"
    description = "Baseline vs candidate comparison across seeds with bootstrap CIs and non-regression checks."
    default_config: ClassVar[dict[str, Any]] = {"regression_tolerance": 0.02, "confidence": 0.95}

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        cfg = self.config(context)
        comparisons: dict[str, dict[str, Any]] = {}
        metrics: dict[str, float] = {}
        warnings: list[str] = []
        for spec_metric in experiment.metrics:
            name = spec_metric.name
            base, cand = context.baseline.get(name), context.candidate.get(name)
            if not base or not cand:
                warnings.append(f"'{name}': missing baseline or candidate samples")
                continue
            bm, cm = _mean(base), _mean(cand)
            if bm is None or cm is None:
                continue
            lo, hi = st.bootstrap_diff_ci(cand, base, confidence=float(cfg["confidence"]), seed=context.seed)
            rel = st.relative_change(cm, bm)
            oriented = _better(spec_metric.direction, cm - bm)
            comparisons[name] = {
                "baseline_mean": bm,
                "candidate_mean": cm,
                "delta": cm - bm,
                "relative_change": rel,
                "improvement": oriented,
                "ci_low": lo,
                "ci_high": hi,
                "n_baseline": len(base),
                "n_candidate": len(cand),
                "direction": spec_metric.direction,
            }
            metrics[f"{name}.delta"] = cm - bm
            if rel is not None:
                metrics[f"{name}.relative_change"] = rel
        checks: list[dict[str, Any]] = []
        for crit in experiment.success_criteria:
            comp = comparisons.get(crit.metric)
            if crit.comparator not in ("improves_over_baseline_by", "not_worse_than_baseline_by"):
                continue
            if comp is None:
                checks.append(
                    {"metric": crit.metric, "comparator": crit.comparator, "passed": False, "reason": "missing samples"}
                )
                continue
            if crit.relative:
                rel = comp["relative_change"]
                improvement = None if rel is None else (rel if comp["direction"] == "maximize" else -rel)
            else:
                improvement = comp["improvement"]
            if improvement is None:
                ok = False
            elif crit.comparator == "improves_over_baseline_by":
                ok = improvement >= crit.threshold and improvement > 0
            else:
                ok = improvement >= -abs(crit.threshold)
            checks.append(
                {
                    "metric": crit.metric,
                    "comparator": crit.comparator,
                    "threshold": crit.threshold,
                    "relative": crit.relative,
                    "improvement": improvement,
                    "passed": ok,
                }
            )
        # Non-regression of metrics without explicit criteria.
        criteria_metrics = {c.metric for c in experiment.success_criteria}
        tol = float(cfg["regression_tolerance"])
        for name, comp in comparisons.items():
            if name in criteria_metrics:
                continue
            rel = comp["relative_change"]
            oriented_rel = None if rel is None else (rel if comp["direction"] == "maximize" else -rel)
            if oriented_rel is not None and oriented_rel < -tol:
                warnings.append(f"'{name}' regressed by {abs(oriented_rel):.1%} (tolerance {tol:.1%})")
        passed: bool | None = all(c["passed"] for c in checks) if checks else None
        n = min((c["n_candidate"] for c in comparisons.values()), default=0)
        ci_excludes_zero = (
            all((c["ci_low"] > 0 or c["ci_high"] < 0) for c in comparisons.values() if not math.isnan(c["ci_low"]))
            if comparisons
            else False
        )
        confidence = 0.0 if passed is None else min(0.95, 0.35 + 0.08 * n + (0.2 if ci_excludes_zero else 0.0))
        if context.self_reported:
            confidence *= 0.5
            warnings.append("metrics were self-reported by the experiment code")
        return self.result(
            metrics=metrics,
            passed=passed,
            confidence=round(confidence, 3),
            warnings=warnings,
            evidence=[
                {"type": "comparisons", "comparisons": comparisons},
                {"type": "criteria_checks", "checks": checks},
            ],
            details={"comparisons": comparisons},
        )


class StatisticalEvaluator(Evaluator):
    key = "statistical"
    version = "1.0.0"
    kind = "statistical"
    description = "Hypothesis test per the statistical plan with multiple-comparison correction and effect sizes."

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        plan = experiment.statistical_plan
        tested: list[MetricSpec] = []
        criteria_metrics = {c.metric for c in experiment.success_criteria}
        for m in experiment.metrics:
            if m.primary or m.name in criteria_metrics:
                tested.append(m)
        if not tested and experiment.primary_metric:
            tested = [experiment.primary_metric]
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for m in tested:
            base, cand = context.baseline.get(m.name) or [], context.candidate.get(m.name) or []
            if len(base) < plan.min_seeds or len(cand) < plan.min_seeds:
                warnings.append(f"'{m.name}': {len(cand)}/{len(base)} samples < min_seeds {plan.min_seeds}")
                rows.append({"metric": m.name, "insufficient": True, "n_candidate": len(cand), "n_baseline": len(base)})
                continue
            res = st.run_test(plan.test, cand, base, seed=context.seed)
            d = st.hedges_g(cand, base)
            oriented_d = d if m.direction == "maximize" else -d
            rows.append(
                {
                    "metric": m.name,
                    "test": res.test,
                    "statistic": res.statistic,
                    "p_value": res.p_value,
                    "df": res.df,
                    "effect_size_g": d,
                    "oriented_effect": oriented_d,
                    "n_candidate": res.n_a,
                    "n_baseline": res.n_b,
                    "note": res.note,
                }
            )
        testable = [r for r in rows if not r.get("insufficient")]
        adjusted = st.correct([r["p_value"] for r in testable], plan.correction)
        for row, p_adj in zip(testable, adjusted, strict=True):
            row["p_adjusted"] = p_adj
            significant = p_adj < plan.alpha
            better = row["oriented_effect"] > 0 if not math.isnan(row["oriented_effect"]) else False
            large_enough = plan.min_effect_size is None or abs(row["oriented_effect"]) >= plan.min_effect_size
            row["significant"] = significant
            row["supports_improvement"] = bool(significant and better and large_enough)
        if not testable:
            passed: bool | None = None
            confidence = 0.0
        elif any(r.get("insufficient") for r in rows):
            passed = None
            confidence = 0.3
            warnings.append("inconclusive: not every tested metric has enough samples")
        else:
            passed = all(r["supports_improvement"] for r in testable)
            worst_p = max(r["p_adjusted"] for r in testable)
            confidence = max(0.05, min(0.99, 1.0 - worst_p)) if passed else max(0.05, min(0.9, worst_p))
        metrics = {f"{r['metric']}.p_adjusted": r["p_adjusted"] for r in testable}
        metrics.update(
            {
                f"{r['metric']}.effect_size_g": r["effect_size_g"]
                for r in testable
                if not math.isnan(r["effect_size_g"]) and not math.isinf(r["effect_size_g"])
            }
        )
        if context.self_reported:
            confidence *= 0.5
            warnings.append("metrics were self-reported by the experiment code")
        return self.result(
            metrics=metrics,
            passed=passed,
            confidence=round(confidence, 3),
            warnings=warnings,
            evidence=[{"type": "statistical_tests", "alpha": plan.alpha, "correction": plan.correction, "rows": rows}],
            details={"rows": rows},
        )


class ClassificationEvaluator(Evaluator):
    key = "classification"
    version = "1.0.0"
    description = "Accuracy, macro precision/recall/F1 and confusion matrix from a predictions CSV artifact."
    default_config: ClassVar[dict[str, Any]] = {"artifact": "predictions.csv"}

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        name = self.config(context)["artifact"]
        data = artifacts.get(name)
        if data is None:
            return self.result(passed=False, confidence=0.9, warnings=[f"artifact '{name}' not found"])
        try:
            truths, preds = _read_pairs(data)
        except ValueError as exc:
            return self.result(passed=False, confidence=0.9, warnings=[str(exc)])
        n = len(truths)
        if n == 0:
            return self.result(passed=False, confidence=0.9, warnings=["no predictions"])
        labels = sorted(set(truths) | set(preds))
        confusion = {t: dict.fromkeys(labels, 0) for t in labels}
        for t, p in zip(truths, preds, strict=True):
            confusion[t][p] += 1
        precisions, recalls, f1s = [], [], []
        for label in labels:
            tp = confusion[label][label]
            fp = sum(confusion[t][label] for t in labels if t != label)
            fn = sum(confusion[label][p] for p in labels if p != label)
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            precisions.append(prec)
            recalls.append(rec)
            f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
        metrics = {
            "accuracy": sum(1 for t, p in zip(truths, preds, strict=True) if t == p) / n,
            "precision_macro": sum(precisions) / len(labels),
            "recall_macro": sum(recalls) / len(labels),
            "f1_macro": sum(f1s) / len(labels),
            "n": float(n),
        }
        passed, checks = _criteria_for(experiment, metrics)
        lo, hi = wilson_interval(round(metrics["accuracy"] * n), n)
        return self.result(
            metrics=metrics,
            passed=passed,
            confidence=round(min(0.99, 0.5 + math.log10(n + 1) / 8), 3),
            evidence=[
                {"type": "confusion_matrix", "labels": labels, "matrix": confusion},
                {"type": "criteria_checks", "checks": checks},
            ],
            details={"accuracy_ci": [lo, hi]},
        )


class RegressionEvaluator(Evaluator):
    key = "regression"
    version = "1.0.0"
    description = "MSE, RMSE, MAE and R² from a predictions CSV artifact (numeric targets)."
    default_config: ClassVar[dict[str, Any]] = {"artifact": "predictions.csv"}

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        name = self.config(context)["artifact"]
        data = artifacts.get(name)
        if data is None:
            return self.result(passed=False, confidence=0.9, warnings=[f"artifact '{name}' not found"])
        try:
            truths_s, preds_s = _read_pairs(data)
            truths = [float(v) for v in truths_s]
            preds = [float(v) for v in preds_s]
        except ValueError as exc:
            return self.result(passed=False, confidence=0.9, warnings=[f"invalid predictions: {exc}"])
        n = len(truths)
        if n == 0:
            return self.result(passed=False, confidence=0.9, warnings=["no predictions"])
        errors = [p - t for t, p in zip(truths, preds, strict=True)]
        mse = sum(e * e for e in errors) / n
        mean_t = sum(truths) / n
        ss_tot = sum((t - mean_t) ** 2 for t in truths)
        metrics = {
            "mse": mse,
            "rmse": math.sqrt(mse),
            "mae": sum(abs(e) for e in errors) / n,
            "r2": 1 - (mse * n) / ss_tot if ss_tot else 0.0,
            "n": float(n),
        }
        passed, checks = _criteria_for(experiment, metrics)
        return self.result(
            metrics=metrics,
            passed=passed,
            confidence=round(min(0.99, 0.5 + math.log10(n + 1) / 8), 3),
            evidence=[{"type": "criteria_checks", "checks": checks}],
        )


class ReproductionEvaluator(Evaluator):
    key = "reproduction"
    version = "1.0.0"
    description = "Does an independent re-execution reproduce the original metrics within tolerance?"

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        if context.reproduction is None:
            return self.result(passed=None, confidence=0.0, warnings=["no reproduction samples supplied"])
        tol = experiment.reproducibility.relative_tolerance
        names = [m.name for m in experiment.metrics if m.primary] or [m.name for m in experiment.metrics]
        rows: list[dict[str, Any]] = []
        for name in names:
            orig, repro = _mean(context.candidate.get(name)), _mean(context.reproduction.get(name))
            if orig is None or repro is None:
                rows.append({"metric": name, "reproduced": False, "reason": "missing samples"})
                continue
            denom = abs(orig) if orig != 0 else 1.0
            rel_diff = abs(repro - orig) / denom
            rows.append(
                {
                    "metric": name,
                    "original": orig,
                    "reproduced_value": repro,
                    "relative_difference": rel_diff,
                    "tolerance": tol,
                    "reproduced": rel_diff <= tol,
                }
            )
        passed = all(r["reproduced"] for r in rows) if rows else None
        worst = max((r.get("relative_difference", 1.0) for r in rows), default=1.0)
        confidence = (
            0.0
            if passed is None
            else (min(0.99, 0.7 + 0.3 * (1 - min(worst / max(tol, 1e-9), 1.0))) if passed else 0.85)
        )
        return self.result(
            metrics={
                f"{r['metric']}.relative_difference": r["relative_difference"]
                for r in rows
                if "relative_difference" in r
            },
            passed=passed,
            confidence=round(confidence, 3),
            evidence=[{"type": "reproduction", "rows": rows}],
        )


_DEFAULT_FORBIDDEN_IMPORTS = [
    "subprocess",
    "socket",
    "ctypes",
    "requests",
    "urllib.request",
    "http.client",
    "ftplib",
    "telnetlib",
    "paramiko",
    "httpx",
    "aiohttp",
]
_DEFAULT_FORBIDDEN_CALLS = [
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.fork",
    "pickle.loads",
    "marshal.loads",
]
_SEED_MARKERS = (
    "random.seed",
    "np.random.seed",
    "numpy.random.seed",
    "default_rng",
    "torch.manual_seed",
    "tf.random.set_seed",
    "set_seed",
)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


class CodeQualityEvaluator(Evaluator):
    key = "code_quality"
    version = "1.0.0"
    kind = "static_analysis"
    description = "Static (AST) checks on generated code: syntax, forbidden imports/calls, seeding, output paths."
    default_config: ClassVar[dict[str, Any]] = {
        "forbidden_imports": _DEFAULT_FORBIDDEN_IMPORTS,
        "forbidden_calls": _DEFAULT_FORBIDDEN_CALLS,
        "require_seed": True,
        "max_file_bytes": 200_000,
    }

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        cfg = self.config(context)
        forbidden_imports = set(cfg["forbidden_imports"])
        forbidden_calls = set(cfg["forbidden_calls"])
        violations: list[dict[str, Any]] = []
        warnings: list[str] = []
        files = {k: v for k, v in artifacts.items() if k.endswith(".py")}
        if not files:
            return self.result(passed=None, confidence=0.0, warnings=["no Python source files to analyse"])
        seeded = False
        total_lines = 0
        functions = 0
        longest = 0
        for path, raw in sorted(files.items()):
            if len(raw) > int(cfg["max_file_bytes"]):
                violations.append({"file": path, "rule": "file_too_large", "severity": "error"})
                continue
            source = raw.decode("utf-8", errors="replace")
            total_lines += source.count("\n") + 1
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError as exc:
                violations.append(
                    {"file": path, "rule": "syntax_error", "line": exc.lineno, "message": exc.msg, "severity": "error"}
                )
                continue
            if any(marker in source for marker in _SEED_MARKERS):
                seeded = True
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in forbidden_imports or alias.name.split(".")[0] in forbidden_imports:
                            violations.append(
                                {
                                    "file": path,
                                    "rule": "forbidden_import",
                                    "name": alias.name,
                                    "line": node.lineno,
                                    "severity": "error",
                                }
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                    if mod in forbidden_imports or mod.split(".")[0] in forbidden_imports:
                        violations.append(
                            {
                                "file": path,
                                "rule": "forbidden_import",
                                "name": mod,
                                "line": node.lineno,
                                "severity": "error",
                            }
                        )
                elif isinstance(node, ast.Call):
                    name = _dotted(node.func)
                    if name in forbidden_calls:
                        violations.append(
                            {
                                "file": path,
                                "rule": "forbidden_call",
                                "name": name,
                                "line": node.lineno,
                                "severity": "error",
                            }
                        )
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    functions += 1
                    end = getattr(node, "end_lineno", node.lineno) or node.lineno
                    longest = max(longest, end - node.lineno + 1)
        if cfg["require_seed"] and not seeded:
            violations.append({"rule": "missing_seed", "severity": "warning", "message": "no RNG seeding detected"})
            warnings.append("no RNG seeding detected — results may not be reproducible")
        if longest > 150:
            warnings.append(f"longest function spans {longest} lines")
        errors = [v for v in violations if v["severity"] == "error"]
        return self.result(
            metrics={
                "files": float(len(files)),
                "lines": float(total_lines),
                "functions": float(functions),
                "violations": float(len(errors)),
            },
            passed=not errors,
            confidence=0.9,
            warnings=warnings,
            evidence=[{"type": "static_analysis", "violations": violations}],
        )


class ResourceEvaluator(Evaluator):
    key = "resource"
    version = "1.0.0"
    description = "Runtime, memory and exit status measured by the execution backend against the resource request."

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        r = context.resources
        req = experiment.resources
        warnings: list[str] = []
        failures: list[str] = []
        runtime = float(r.get("runtime_seconds") or 0.0)
        peak = r.get("peak_memory_mb")
        if r.get("timed_out"):
            failures.append("timed out")
        if r.get("oom_killed"):
            failures.append("killed for exceeding memory")
        exit_code = r.get("exit_code")
        if exit_code not in (0, None):
            failures.append(f"exit code {exit_code}")
        if runtime > 0.8 * req.timeout_seconds:
            warnings.append(f"runtime {runtime:.1f}s used {runtime / req.timeout_seconds:.0%} of the timeout")
        metrics: dict[str, float] = {"runtime_seconds": runtime, "timeout_utilisation": runtime / req.timeout_seconds}
        if peak is not None:
            metrics["peak_memory_mb"] = float(peak)
            metrics["memory_utilisation"] = float(peak) / req.memory_mb
            if float(peak) > 0.9 * req.memory_mb:
                warnings.append("peak memory above 90% of the limit")
        if r.get("output_bytes") is not None:
            metrics["output_bytes"] = float(r["output_bytes"])
        return self.result(
            metrics=metrics,
            passed=not failures,
            confidence=0.95,
            warnings=warnings + failures,
            evidence=[{"type": "resource_usage", "measured": r, "requested": req.model_dump()}],
        )


class CustomEvaluator(Evaluator):
    key = "custom"
    version = "1.0.0"
    kind = "rule"
    description = "Declarative rules in a safe expression language over candidate/baseline metric means."

    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult:
        rules = self.config(context).get("rules") or []
        if not rules:
            return self.result(passed=None, confidence=0.0, warnings=["no custom rules configured"])
        cand = {k: m for k, v in context.candidate.items() if (m := _mean(v)) is not None}
        base = {k: m for k, v in context.baseline.items() if (m := _mean(v)) is not None}
        namespace = {
            "candidate": cand,
            "baseline": base,
            "delta": {k: cand[k] - base[k] for k in cand if k in base},
            "resources": {k: v for k, v in context.resources.items() if isinstance(v, int | float)},
            "n": min((len(v) for v in context.candidate.values()), default=0),
        }
        outcomes: list[dict[str, Any]] = []
        for rule in rules:
            name = str(rule.get("name", "rule"))
            severity = rule.get("severity", "error")
            try:
                value = bool(evaluate(str(rule["expr"]), namespace))
                outcomes.append({"name": name, "expr": rule["expr"], "passed": value, "severity": severity})
            except (ExpressionError, KeyError, TypeError, ValueError) as exc:
                outcomes.append(
                    {"name": name, "expr": rule.get("expr"), "passed": False, "severity": severity, "error": str(exc)}
                )
        errors = [o for o in outcomes if o["severity"] == "error"]
        passed = all(o["passed"] for o in errors) if errors else None
        return self.result(
            metrics={f"rule.{o['name']}": 1.0 if o["passed"] else 0.0 for o in outcomes},
            passed=passed,
            confidence=0.8 if passed is not None else 0.0,
            warnings=[f"rule '{o['name']}' failed" for o in outcomes if not o["passed"]],
            evidence=[{"type": "custom_rules", "outcomes": outcomes}],
        )


DEFAULT_REGISTRY = EvaluatorRegistry()
for _cls in (
    MetricEvaluator,
    BenchmarkEvaluator,
    StatisticalEvaluator,
    ClassificationEvaluator,
    RegressionEvaluator,
    ReproductionEvaluator,
    CodeQualityEvaluator,
    ResourceEvaluator,
    CustomEvaluator,
):
    DEFAULT_REGISTRY.register(_cls)

# Evaluators applied to every candidate run unless the experiment overrides the set.
STANDARD_SUITE: tuple[str, ...] = ("resource", "metric", "benchmark", "statistical")
