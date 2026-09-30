"""A tiny, safe expression language for custom evaluator rules (no ``eval``/``exec``).

Supported: numbers, booleans, names resolved from a namespace (dotted access into dicts such as
``candidate.accuracy``), unary +/-/not, + - * / // % **, comparisons (chained), and/or, parentheses and the
functions ``abs``, ``min``, ``max``, ``round``, ``len``, ``sqrt``, ``log``. Anything else is rejected at parse
time. Expression size and exponent magnitude are bounded to prevent resource exhaustion.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping
from typing import Any

MAX_EXPRESSION_LENGTH = 500
MAX_NODES = 200
MAX_EXPONENT = 100

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "sqrt": math.sqrt,
    "log": math.log,
}


class ExpressionError(ValueError):
    """Invalid or unsafe expression."""


def parse(expression: str) -> ast.Expression:
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError("Expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Invalid expression: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExpressionError("Expression is too complex")
    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Expression
            | ast.BoolOp
            | ast.BinOp
            | ast.UnaryOp
            | ast.Compare
            | ast.Call
            | ast.Name
            | ast.Attribute
            | ast.Constant
            | ast.Load
            | ast.And
            | ast.Or
            | ast.Not
            | ast.USub
            | ast.UAdd
            | ast.operator
            | ast.cmpop,
        ):
            raise ExpressionError(f"Disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS):
            raise ExpressionError("Only abs/min/max/round/len/sqrt/log calls are allowed")
        if isinstance(node, ast.Call) and node.keywords:
            raise ExpressionError("Keyword arguments are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionError("Private attributes are not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ExpressionError("Private names are not allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, int | float | bool | type(None)):
            raise ExpressionError("Only numeric/boolean constants are allowed")
    return tree


def _resolve_attribute(node: ast.Attribute, namespace: Mapping[str, Any]) -> Any:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        raise ExpressionError("Attribute access must start from a name")
    parts.append(current.id)
    value: Any = namespace
    for part in reversed(parts):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            raise ExpressionError(f"Unknown name '{'.'.join(reversed(parts))}'")
    return value


def _eval(node: ast.AST, ns: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, ns)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        if node.id not in ns:
            raise ExpressionError(f"Unknown name '{node.id}'")
        return ns[node.id]
    if isinstance(node, ast.Attribute):
        return _resolve_attribute(node, ns)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ns)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, ns), _eval(node.right, ns)
        if isinstance(node.op, ast.Pow) and isinstance(right, int | float) and abs(right) > MAX_EXPONENT:
            raise ExpressionError("Exponent too large")
        try:
            return _BINOPS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise ExpressionError("Division by zero") from exc
    if isinstance(node, ast.BoolOp):
        values = (_eval(v, ns) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ns)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, ns)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = [_eval(a, ns) for a in node.args]
        return _FUNCTIONS[node.func.id](*args)
    raise ExpressionError(f"Unsupported expression node {type(node).__name__}")


def evaluate(expression: str, namespace: Mapping[str, Any]) -> Any:
    return _eval(parse(expression), namespace)
