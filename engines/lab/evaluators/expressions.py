"""Safe arithmetic/boolean expression evaluator for custom evaluators.

Expressions such as ``accuracy >= 0.9 and latency_ms < 200`` are parsed with :mod:`ast` and evaluated by an
explicit tree walker — ``eval``/``compile`` are never used. Only this grammar is accepted:

* boolean operators ``and``/``or``, ``not``;
* comparisons ``== != < <= > >=`` (chains like ``0.5 < x <= 1`` allowed; ``in``/``is`` rejected);
* arithmetic ``+ - * / % **`` and unary ``+``/``-``;
* numeric and boolean literals (strings, bytes, ``None``, complex numbers are rejected);
* variable names (metric lookups) — never names starting with ``_`` (so no dunders);
* calls to the whitelist ``abs, min, max, round, sqrt, log`` with positional arguments only.

Everything else — attribute access, subscripts, lambdas, comprehensions, f-strings, conditional expressions,
keyword/star arguments, walrus — is rejected at compile time. Resource bounds: source length, AST node count,
nesting depth, exponent magnitude, and finite results. All numbers are evaluated as floats (no big integers).
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

MAX_EXPRESSION_LENGTH = 1000
MAX_NODES = 200
MAX_DEPTH = 32
MAX_ABS_EXPONENT = 64.0
MAX_ABS_LITERAL = 1e15

Value = float | bool


class ExpressionError(ValueError):
    """The expression is not allowed, malformed, or cannot be evaluated with the given variables."""


def _sqrt(x: float) -> float:
    if x < 0:
        raise ExpressionError("sqrt of a negative number")
    return math.sqrt(x)


def _log(x: float, base: float | None = None) -> float:
    if x <= 0 or (base is not None and (base <= 0 or base == 1)):
        raise ExpressionError("log domain error")
    return math.log(x) if base is None else math.log(x, base)


def _round(x: float, ndigits: float | None = None) -> float:
    if ndigits is None:
        return float(round(x))
    if not float(ndigits).is_integer() or abs(ndigits) > 15:
        raise ExpressionError("round() ndigits must be an integer in [-15, 15]")
    return float(round(x, int(ndigits)))


def _min(*args: float) -> float:
    if not args:
        raise ExpressionError("min() needs at least one argument")
    return min(args)


def _max(*args: float) -> float:
    if not args:
        raise ExpressionError("max() needs at least one argument")
    return max(args)


def _abs(x: float) -> float:
    return abs(x)


_FUNCTIONS: dict[str, tuple[Callable[..., float], int, int]] = {
    # name: (implementation, min args, max args)
    "abs": (_abs, 1, 1),
    "min": (_min, 1, 32),
    "max": (_max, 1, 32),
    "round": (_round, 1, 2),
    "sqrt": (_sqrt, 1, 1),
    "log": (_log, 1, 2),
}
ALLOWED_FUNCTIONS = frozenset(_FUNCTIONS)

_BOOL_OPS = (ast.And, ast.Or)
_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
_UNARY_OPS = (ast.Not, ast.USub, ast.UAdd)


@dataclass(frozen=True)
class CompiledExpression:
    """A validated expression. ``names`` are the variables it reads (excluding function names)."""

    source: str
    tree: ast.Expression
    names: frozenset[str]

    def evaluate(self, variables: Mapping[str, float | int | bool]) -> Value:
        return _Evaluator(variables).visit(self.tree.body)


def _validate(node: ast.AST, depth: int, names: set[str]) -> None:
    if depth > MAX_DEPTH:
        raise ExpressionError(f"expression nesting exceeds {MAX_DEPTH} levels")
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _BOOL_OPS):
            raise ExpressionError("unsupported boolean operator")
        for operand in node.values:
            _validate(operand, depth + 1, names)
    elif isinstance(node, ast.Compare):
        if not all(isinstance(op, _CMP_OPS) for op in node.ops):
            raise ExpressionError("only == != < <= > >= comparisons are allowed")
        _validate(node.left, depth + 1, names)
        for comparator in node.comparators:
            _validate(comparator, depth + 1, names)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BIN_OPS):
            raise ExpressionError(f"operator {type(node.op).__name__} is not allowed")
        _validate(node.left, depth + 1, names)
        _validate(node.right, depth + 1, names)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARY_OPS):
            raise ExpressionError(f"unary operator {type(node.op).__name__} is not allowed")
        _validate(node.operand, depth + 1, names)
    elif isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            raise ExpressionError("assignment is not allowed")
        if node.id.startswith("_"):
            raise ExpressionError(f"name {node.id!r} is not allowed (leading underscore)")
        if node.id in ALLOWED_FUNCTIONS:
            raise ExpressionError(f"function {node.id!r} must be called")
        names.add(node.id)
    elif isinstance(node, ast.Constant):
        literal = node.value
        if isinstance(literal, bool):
            return
        if not isinstance(literal, (int, float)):
            raise ExpressionError(f"literal of type {type(literal).__name__} is not allowed")
        if not math.isfinite(float(literal)) or abs(literal) > MAX_ABS_LITERAL:
            raise ExpressionError("numeric literal out of range")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            raise ExpressionError("only calls to abs, min, max, round, sqrt and log are allowed")
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        _, min_args, max_args = _FUNCTIONS[node.func.id]
        if not (min_args <= len(node.args) <= max_args):
            raise ExpressionError(f"{node.func.id}() takes {min_args}..{max_args} arguments")
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise ExpressionError("star arguments are not allowed")
            _validate(arg, depth + 1, names)
    else:
        raise ExpressionError(f"{type(node).__name__} is not allowed in expressions")


def compile_expression(source: str) -> CompiledExpression:
    """Parse and validate ``source``; raises :class:`ExpressionError` for anything outside the grammar."""
    if not isinstance(source, str) or not source.strip():
        raise ExpressionError("expression must be a non-empty string")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(f"expression exceeds {MAX_EXPRESSION_LENGTH} characters")
    if "\x00" in source:
        raise ExpressionError("expression contains a NUL byte")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ExpressionError(f"invalid expression: {exc.__class__.__name__}") from exc
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_NODES:
        raise ExpressionError(f"expression has {node_count} nodes (max {MAX_NODES})")
    names: set[str] = set()
    _validate(tree.body, 0, names)
    return CompiledExpression(source=source, tree=tree, names=frozenset(names))


def evaluate_expression(source: str | CompiledExpression, variables: Mapping[str, float | int | bool]) -> Value:
    """Compile (if needed) and evaluate an expression against ``variables``."""
    compiled = source if isinstance(source, CompiledExpression) else compile_expression(source)
    return compiled.evaluate(variables)


class _Evaluator:
    def __init__(self, variables: Mapping[str, float | int | bool]) -> None:
        self._vars = variables

    def _number(self, value: Value) -> float:
        return float(value)

    def _check(self, value: float) -> float:
        if not math.isfinite(value):
            raise ExpressionError("expression produced a non-finite number")
        return value

    def visit(self, node: ast.AST) -> Value:
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Value = True
                for operand in node.values:
                    result = self.visit(operand)
                    if not result:
                        return result
                return result
            result = False
            for operand in node.values:
                result = self.visit(operand)
                if result:
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = self._number(self.visit(node.left))
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._number(self.visit(comparator))
                if not _compare(op, left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            inner = self.visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not inner
            number = self._number(inner)
            return -number if isinstance(node.op, ast.USub) else number
        if isinstance(node, ast.Name):
            if node.id not in self._vars:
                raise ExpressionError(f"unknown variable {node.id!r}")
            variable = self._vars[node.id]
            if isinstance(variable, bool):
                return variable
            if not isinstance(variable, (int, float)) or not math.isfinite(float(variable)):
                raise ExpressionError(f"variable {node.id!r} is not a finite number")
            return float(variable)
        if isinstance(node, ast.Constant):
            literal = node.value
            if isinstance(literal, bool):
                return literal
            assert isinstance(literal, (int, float))  # ensured by compile-time validation
            return float(literal)
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            fn = _FUNCTIONS[node.func.id][0]
            args = [self._number(self.visit(arg)) for arg in node.args]
            try:
                return self._check(float(fn(*args)))
            except (OverflowError, ValueError, ZeroDivisionError) as exc:
                if isinstance(exc, ExpressionError):
                    raise
                raise ExpressionError(f"{node.func.id}() failed: {exc}") from exc
        raise ExpressionError(f"{type(node).__name__} is not allowed")  # unreachable after validation

    def _binop(self, node: ast.BinOp) -> float:
        left = self._number(self.visit(node.left))
        right = self._number(self.visit(node.right))
        op = node.op
        try:
            if isinstance(op, ast.Add):
                return self._check(left + right)
            if isinstance(op, ast.Sub):
                return self._check(left - right)
            if isinstance(op, ast.Mult):
                return self._check(left * right)
            if isinstance(op, ast.Div):
                if right == 0:
                    raise ExpressionError("division by zero")
                return self._check(left / right)
            if isinstance(op, ast.Mod):
                if right == 0:
                    raise ExpressionError("modulo by zero")
                return self._check(left % right)
            if abs(right) > MAX_ABS_EXPONENT:
                raise ExpressionError(f"exponent magnitude exceeds {MAX_ABS_EXPONENT:g}")
            if left < 0 and not right.is_integer():
                raise ExpressionError("fractional power of a negative number")
            if left == 0 and right < 0:
                raise ExpressionError("zero raised to a negative power")
            return self._check(math.pow(left, right))
        except OverflowError as exc:
            raise ExpressionError("numeric overflow") from exc


def _compare(op: ast.cmpop, left: float, right: float) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    return left >= right
