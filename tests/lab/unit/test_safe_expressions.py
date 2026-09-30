"""Safe expression evaluator: accepted grammar, and rejection of everything that could escape it."""

from __future__ import annotations

import pytest

from engines.lab.evaluators.expressions import (
    MAX_EXPRESSION_LENGTH,
    ExpressionError,
    compile_expression,
    evaluate_expression,
)

METRICS = {"accuracy": 0.93, "latency_ms": 150.0, "f1": 0.88, "baseline_accuracy": 0.9, "converged": True}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("accuracy >= 0.9 and latency_ms < 200", True),
        ("accuracy >= 0.95 or f1 > 0.85", True),
        ("not (accuracy < 0.9)", True),
        ("0.9 < accuracy <= 1", True),
        ("accuracy - baseline_accuracy >= 0.02", True),
        ("abs(accuracy - baseline_accuracy) < 0.05", True),
        ("max(accuracy, f1) == accuracy", True),
        ("min(accuracy, f1, 0.5) == 0.5", True),
        ("round(latency_ms / 3, 1) == 50.0", True),
        ("sqrt(16) + log(1) == 4", True),
        ("log(8, 2) == 3", True),
        ("2 ** 10 == 1024", True),
        ("latency_ms % 7 == 3", True),
        ("-accuracy < +0", True),
        ("converged and accuracy > 0.9", True),
        ("accuracy != f1", True),
    ],
)
def test_allowed_expressions(expression, expected):
    assert evaluate_expression(expression, METRICS) is expected


def test_numeric_results_and_names():
    assert evaluate_expression("accuracy * 100", METRICS) == pytest.approx(93.0)
    compiled = compile_expression("accuracy >= 0.9 and sqrt(latency_ms) < 20")
    assert compiled.names == frozenset({"accuracy", "latency_ms"})
    assert compiled.evaluate({"accuracy": 0.95, "latency_ms": 100.0}) is True


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "__import__('os').system('id')",
        "accuracy.real",
        "().__class__.__bases__[0].__subclasses__()",
        "metrics['accuracy']",
        "(lambda: 1)()",
        "[x for x in range(3)]",
        "{x for x in (1, 2)}",
        "open('/etc/passwd')",
        "eval('1')",
        "'abc' == 'abc'",
        "f'{accuracy}' == '0.93'",
        "b'x'",
        "None",
        "1j",
        "accuracy if True else 0",
        "(y := 3)",
        "max(*[1, 2])",
        "round(accuracy, ndigits=2)",
        "_private > 0",
        "__builtins__",
        "accuracy in (1, 2)",
        "accuracy is 1",
        "~1",
        "1 << 3",
        "1 // 2",
        "abs",
        "globals()",
    ],
)
def test_rejected_constructs(expression):
    with pytest.raises(ExpressionError):
        compile_expression(expression)


def test_exponent_and_size_bounds():
    with pytest.raises(ExpressionError, match="exponent"):
        evaluate_expression("10 ** 1000", {})
    with pytest.raises(ExpressionError, match="exponent"):
        evaluate_expression("2 ** (10 * 10)", {})
    with pytest.raises(ExpressionError):
        evaluate_expression("10.0 ** 60 * 10.0 ** 60 * 10.0 ** 60 * 10.0 ** 60 * 10.0 ** 60 * 10.0 ** 60", {})
    with pytest.raises(ExpressionError, match="out of range"):
        compile_expression("99999999999999999999 > 1")  # huge literals are rejected at compile time
    with pytest.raises(ExpressionError, match="non-finite"):
        evaluate_expression("x * x > 0", {"x": 1e200})  # overflow to infinity is rejected at run time
    with pytest.raises(ExpressionError):
        compile_expression("a" * (MAX_EXPRESSION_LENGTH + 1))
    with pytest.raises(ExpressionError):
        compile_expression(" + ".join(["x"] * 150))  # too many nodes
    with pytest.raises(ExpressionError):
        compile_expression("(" * 300 + "1" + ")" * 300)  # nesting


def test_runtime_errors_are_expression_errors():
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate_expression("accuracy / 0", METRICS)
    with pytest.raises(ExpressionError, match="unknown variable"):
        evaluate_expression("precision > 0.5", METRICS)
    with pytest.raises(ExpressionError):
        evaluate_expression("sqrt(-1) > 0", METRICS)
    with pytest.raises(ExpressionError):
        evaluate_expression("log(0) > 0", METRICS)
    with pytest.raises(ExpressionError, match="finite"):
        evaluate_expression("x > 0", {"x": float("nan")})
    with pytest.raises(ExpressionError):
        evaluate_expression("(-8) ** 0.5 > 0", {})
    with pytest.raises(ExpressionError):
        evaluate_expression("", {})
