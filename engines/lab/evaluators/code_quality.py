"""CodeQualityEvaluator — pure static analysis (``ast``) of the experiment's Python code.

Sources: ``*.py`` files and Jupyter notebooks (``*.ipynb``, code cells only) in the artifact bundle, plus the
spec's inline ``code.files`` (``include_spec_files``). The code is parsed, never imported or executed.

Findings (``code`` / default severity):

* ``PARSE_ERROR`` (error) — the file does not parse;
* ``FORBIDDEN_IMPORT`` (error) — ``subprocess``, ``socket``, ``ctypes``, ``multiprocessing.managers``, ``pty``,
  ``cffi`` (configurable), including ``from multiprocessing import managers``;
* ``DANGEROUS_CALL`` (error) — ``eval``, ``exec``, ``__import__``, ``os.system``, ``os.popen``, ``os.exec*``,
  ``os.spawn*``, ``os.fork*`` (aliases such as ``import os as o; o.system(...)`` are resolved); ``compile`` is a
  warning;
* ``SHELL_ESCAPE`` (error) — notebook ``!cmd`` lines and ``%%bash``/``%%sh``/``%%script``/``%system``/``%sx``;
* ``UNSAFE_DESERIALIZATION`` (warning) — ``pickle``/``marshal``/``dill``/``cloudpickle``/``joblib`` loads,
  ``shelve.open``, ``yaml.load`` without a safe loader, ``numpy.load(allow_pickle=True)``, ``torch.load``
  without ``weights_only=True``;
* ``DYNAMIC_IMPORT`` (warning) — ``importlib.import_module``;
* ``WRITE_OUTSIDE_OUTPUT`` (warning) — a write (``open(..., 'w')``, ``to_csv``, ``savefig``, ``np.save``,
  ``torch.save``, ``write_text``…) to a literal path outside ``/workspace/output`` (and ``/tmp``), or to a
  relative path (only ``/workspace/output`` is collected);
* ``SUSPICIOUS_PATH`` (warning; error for the Docker socket) — literals pointing at ``/etc``, ``/root``,
  ``/proc``, ``~/.ssh``, cloud credential files…;
* ``FILE_TOO_LARGE`` (warning) — per-file or total size above the configured limits;
* ``NO_SEED_HANDLING`` (warning; error with ``require_seed_handling``) — no ``random.seed``,
  ``numpy.random.seed``/``default_rng(seed)``, ``torch.manual_seed``, ``tf.random.set_seed``,
  ``jax.random.PRNGKey``, ``set_seed``/``seed_everything``, ``random_state=...`` or read of the seed env var.

``passed`` = no error findings (``None`` when there is no code). Static analysis cannot prove the absence of
runtime behaviour, so ``confidence = 0.9 × parsed files / files``.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from engines.lab.evaluators.base import (
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
)

DEFAULT_FORBIDDEN_MODULES = ("subprocess", "socket", "ctypes", "_ctypes", "multiprocessing.managers", "pty", "cffi")
_DANGEROUS_BUILTINS = {"eval", "exec", "__import__"}
_DANGEROUS_OS_PREFIXES = ("exec", "spawn", "fork")
_DANGEROUS_OS = {"system", "popen", "posix_spawn", "posix_spawnp", "forkpty", "killpg"}
_DESERIALIZERS = {
    "pickle.load",
    "pickle.loads",
    "pickle.Unpickler",
    "marshal.load",
    "marshal.loads",
    "dill.load",
    "dill.loads",
    "cloudpickle.load",
    "cloudpickle.loads",
    "joblib.load",
    "shelve.open",
    "pandas.read_pickle",
}
_SEED_CALLS = {
    "random.seed",
    "numpy.random.seed",
    "numpy.random.default_rng",
    "numpy.random.RandomState",
    "numpy.random.Generator",
    "torch.manual_seed",
    "torch.cuda.manual_seed",
    "torch.cuda.manual_seed_all",
    "tensorflow.random.set_seed",
    "tf.random.set_seed",
    "jax.random.PRNGKey",
    "jax.random.key",
    "transformers.set_seed",
    "lightning.seed_everything",
    "pytorch_lightning.seed_everything",
    "lightning.pytorch.seed_everything",
}
_SEED_BARE_NAMES = {"set_seed", "seed_everything", "manual_seed"}
_WRITE_METHODS = {
    "to_csv",
    "to_json",
    "to_parquet",
    "to_pickle",
    "to_excel",
    "to_feather",
    "to_hdf",
    "savefig",
    "write_text",
    "write_bytes",
    "save",
    "savez",
    "savez_compressed",
    "savetxt",
    "imwrite",
    "mkdir",
    "makedirs",
    "copyfile",
    "copy2",
    "copytree",
    "move",
    "touch",
}
# Functions whose *second* positional argument is the destination path.
_SECOND_ARG_DESTINATION = {"copyfile", "copy2", "copytree", "move"}
_QUALIFIED_DESTINATION = {
    "os.rename": 1,
    "os.replace": 1,
    "shutil.copy": 1,
    "shutil.copyfile": 1,
    "shutil.copy2": 1,
    "shutil.copytree": 1,
    "shutil.move": 1,
    "torch.save": 1,
    "joblib.dump": 1,
}
_SENSITIVE_PREFIXES = (
    "/etc/",
    "/root",
    "/home/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/var/run/",
    "/run/",
    "/boot/",
    "~/.ssh",
    "~/.aws",
    "~/.config/gcloud",
    "/.ssh/",
    "/.aws/",
    "/var/lib/docker",
)
_DOCKER_SOCKET = "docker.sock"
_SHELL_MAGICS = ("%%bash", "%%sh", "%%script", "%%system", "%system", "%sx", "%%sx")

Severity = Literal["error", "warning"]


class CodeQualityConfig(EvaluatorConfig):
    include_spec_files: bool = True
    forbidden_modules: list[str] = Field(default_factory=list, max_length=100)
    allowed_modules: list[str] = Field(default_factory=list, max_length=100)
    require_seed_handling: bool = False
    seed_env_var: str | None = None
    max_file_bytes: int = Field(default=256 * 1024, ge=1)
    max_total_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    allowed_write_prefixes: list[str] = Field(default_factory=lambda: ["/workspace/output", "/tmp"])  # noqa: S108


@dataclass
class Finding:
    file: str
    line: int
    code: str
    severity: Severity
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class _FileReport:
    findings: list[Finding] = field(default_factory=list)
    parsed: bool = True
    seeded: bool = False
    lines: int = 0


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _literal_path(node: ast.AST) -> str | None:
    """Best-effort literal path: constants, f-string prefixes, ``a + b`` of constants, ``os.path.join``/
    ``Path(...)`` of constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left = _literal_path(node.left)
        return left if left is not None else None
    if isinstance(node, ast.Call) and node.args:
        name = _dotted(node.func) or ""
        if name.split(".")[-1] in ("join", "Path", "PurePath", "PosixPath"):
            return _literal_path(node.args[0])
    return None


class _Analyzer(ast.NodeVisitor):
    def __init__(self, file: str, cfg: CodeQualityConfig, forbidden: tuple[str, ...], seed_env_var: str) -> None:
        self.file = file
        self.cfg = cfg
        self.forbidden = forbidden
        self.seed_env_var = seed_env_var
        self.aliases: dict[str, str] = {}  # local name -> fully qualified module/object
        self.report = _FileReport()

    def _add(self, node: ast.AST, code: str, severity: Severity, message: str) -> None:
        self.report.findings.append(Finding(self.file, getattr(node, "lineno", 0), code, severity, message))

    def _is_forbidden(self, module: str) -> bool:
        return any(module == f or module.startswith(f + ".") for f in self.forbidden)

    def _qualify(self, dotted: str | None) -> str | None:
        if dotted is None:
            return None
        head, _, rest = dotted.partition(".")
        base = self.aliases.get(head, head)
        return f"{base}.{rest}" if rest else base

    # Imports -----------------------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._is_forbidden(alias.name):
                self._add(node, "FORBIDDEN_IMPORT", "error", f"import of forbidden module {alias.name!r}")
            local = alias.asname or alias.name.split(".")[0]
            self.aliases[local] = alias.name if alias.asname else alias.name.split(".")[0]
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0 and self._is_forbidden(module):
            self._add(node, "FORBIDDEN_IMPORT", "error", f"import from forbidden module {module!r}")
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            if node.level == 0 and self._is_forbidden(qualified) and not self._is_forbidden(module):
                self._add(node, "FORBIDDEN_IMPORT", "error", f"import of forbidden module {qualified!r}")
            if module == "os" and (alias.name in _DANGEROUS_OS or alias.name.startswith(_DANGEROUS_OS_PREFIXES)):
                self._add(node, "DANGEROUS_CALL", "error", f"import of process-spawning function os.{alias.name}")
            self.aliases[alias.asname or alias.name] = qualified
        self.generic_visit(node)

    # Calls -------------------------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        raw = _dotted(node.func)
        name = self._qualify(raw)
        if name is not None:
            self._check_call(node, raw or "", name)
        elif isinstance(node.func, ast.Attribute):
            self._check_write(node, node.func.attr)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call, raw: str, name: str) -> None:
        last = name.rsplit(".", 1)[-1]
        if (raw in _DANGEROUS_BUILTINS and raw not in self.aliases) or name in {
            f"builtins.{b}" for b in _DANGEROUS_BUILTINS
        }:
            self._add(node, "DANGEROUS_CALL", "error", f"call to {last}()")
        elif raw == "compile" and "compile" not in self.aliases:
            self._add(node, "DANGEROUS_CALL", "warning", "call to compile() (dynamic code)")
        if raw == "getattr" and len(node.args) > 1:
            looked_up = node.args[1]
            if (
                isinstance(looked_up, ast.Constant)
                and isinstance(looked_up.value, str)
                and _is_dangerous_name(looked_up.value)
            ):
                self._add(node, "DANGEROUS_CALL", "error", f"dynamic lookup of {looked_up.value!r} via getattr()")
        if name.startswith("os.") and name.count(".") == 1:
            os_attr = name.split(".", 1)[1]
            if os_attr in _DANGEROUS_OS or os_attr.startswith(_DANGEROUS_OS_PREFIXES):
                self._add(node, "DANGEROUS_CALL", "error", f"call to {name}() (process execution)")
        if name in ("importlib.import_module", "importlib.__import__"):
            self._add(node, "DYNAMIC_IMPORT", "warning", f"dynamic import via {name}()")
        self._check_deserialization(node, name)
        seeds_rng = name in _SEED_CALLS or last in _SEED_BARE_NAMES
        # default_rng() without an argument draws OS entropy: that is *not* seeding.
        if seeds_rng and (last != "default_rng" or node.args or node.keywords):
            self.report.seeded = True
        if any(kw.arg == "random_state" and not _is_none(kw.value) for kw in node.keywords):
            self.report.seeded = True
        if last == "open" and name in ("open", "io.open", "builtins.open", "codecs.open"):
            self._check_open(node)
        elif name in _QUALIFIED_DESTINATION:
            position = _QUALIFIED_DESTINATION[name]
            if len(node.args) > position:
                self._check_write_path(node, _literal_path(node.args[position]))
        else:
            self._check_write(node, last)

    def _check_deserialization(self, node: ast.Call, name: str) -> None:
        if name in _DESERIALIZERS:
            self._add(node, "UNSAFE_DESERIALIZATION", "warning", f"{name}() can execute code from untrusted data")
        elif name == "yaml.load":
            loader = next((kw.value for kw in node.keywords if kw.arg == "Loader"), None)
            if loader is None and len(node.args) > 1:
                loader = node.args[1]
            loader_name = _dotted(loader) if loader is not None else None
            if loader_name is None or "Safe" not in loader_name:
                self._add(node, "UNSAFE_DESERIALIZATION", "warning", "yaml.load without SafeLoader")
        elif name in ("numpy.load", "np.load"):
            if any(kw.arg == "allow_pickle" and _is_true(kw.value) for kw in node.keywords):
                self._add(node, "UNSAFE_DESERIALIZATION", "warning", "numpy.load(allow_pickle=True)")
        elif name == "torch.load" and not any(kw.arg == "weights_only" and _is_true(kw.value) for kw in node.keywords):
            self._add(node, "UNSAFE_DESERIALIZATION", "warning", "torch.load without weights_only=True")

    def _check_open(self, node: ast.Call) -> None:
        mode_node = (
            node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
        )
        mode = mode_node.value if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) else "r"
        if any(ch in mode for ch in "wax+") and node.args:
            self._check_write_path(node, _literal_path(node.args[0]))

    def _check_write(self, node: ast.Call, method: str) -> None:
        if method not in _WRITE_METHODS:
            return
        target: ast.AST | None = None
        if method in _SECOND_ARG_DESTINATION:
            target = node.args[1] if len(node.args) > 1 else None
        elif method in ("write_text", "write_bytes"):
            # Path("/x").write_text(data): the path is the receiver, never the (data) argument.
            target = node.func.value if isinstance(node.func, ast.Attribute) else None
        elif (
            method in ("touch", "mkdir")
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            target = node.func.value  # Path("/x").mkdir()
        elif node.args:
            target = node.args[0]
        if target is not None:
            self._check_write_path(node, _literal_path(target))

    def _check_write_path(self, node: ast.AST, path: str | None) -> None:
        if path is None or not path.strip():
            return
        if ".." in path.replace("\\", "/").split("/"):
            self._add(node, "WRITE_OUTSIDE_OUTPUT", "warning", f"write path {path!r} contains '..'")
            return
        if path.startswith("/"):
            if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.cfg.allowed_write_prefixes):
                self._add(node, "WRITE_OUTSIDE_OUTPUT", "warning", f"write to {path!r} outside /workspace/output")
        elif not path.startswith(("{", "%")):
            self._add(
                node,
                "WRITE_OUTSIDE_OUTPUT",
                "warning",
                f"write to relative path {path!r}; only files under /workspace/output are collected",
            )

    # Literals ----------------------------------------------------------------------------------
    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, str) and len(value) < 4096:
            if value == self.seed_env_var:
                self.report.seeded = True
            if _DOCKER_SOCKET in value:
                self._add(node, "SUSPICIOUS_PATH", "error", "reference to the Docker socket")
            elif value.startswith(_SENSITIVE_PREFIXES):
                self._add(node, "SUSPICIOUS_PATH", "warning", f"reference to sensitive path {value[:80]!r}")


def _is_dangerous_name(name: str) -> bool:
    return name in _DANGEROUS_BUILTINS or name in _DANGEROUS_OS or name.startswith(_DANGEROUS_OS_PREFIXES)


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def notebook_code(source: str, file: str) -> tuple[list[tuple[str, str]], list[Finding]]:
    """Extract code cells from a notebook → ``([(cell_name, code)], findings)`` (shell escapes flagged)."""
    findings: list[Finding] = []
    try:
        notebook = json.loads(source)
    except json.JSONDecodeError as exc:
        return [], [Finding(file, 0, "PARSE_ERROR", "error", f"invalid notebook JSON: {exc.msg}")]
    cells = notebook.get("cells", []) if isinstance(notebook, dict) else []
    out: list[tuple[str, str]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        raw = cell.get("source", "")
        text = "".join(raw) if isinstance(raw, list) else str(raw)
        name = f"{file}#cell{index}"
        kept: list[str] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("!") or stripped.startswith(_SHELL_MAGICS):
                findings.append(Finding(name, line_no, "SHELL_ESCAPE", "error", f"shell escape {stripped[:60]!r}"))
                kept.append("")
            elif stripped.startswith("%"):
                kept.append("")  # other IPython magics are ignored
            else:
                kept.append(line)
        out.append((name, "\n".join(kept)))
    return out, findings


def analyze_source(
    source: str, file: str, cfg: CodeQualityConfig | None = None, *, seed_env_var: str = "AEGIS_SEED"
) -> _FileReport:
    """Analyse one Python source string (exposed for unit tests and the coding agent's self-check)."""
    cfg = cfg or CodeQualityConfig()
    forbidden = tuple(sorted((set(DEFAULT_FORBIDDEN_MODULES) | set(cfg.forbidden_modules)) - set(cfg.allowed_modules)))
    analyzer = _Analyzer(file, cfg, forbidden, seed_env_var)
    analyzer.report.lines = source.count("\n") + 1 if source else 0
    try:
        tree = ast.parse(source, filename=file)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        line = getattr(exc, "lineno", 0) or 0
        analyzer.report.parsed = False
        analyzer.report.findings.append(
            Finding(file, line, "PARSE_ERROR", "error", f"{exc.__class__.__name__}: {exc}"[:300])
        )
        return analyzer.report
    analyzer.visit(tree)
    return analyzer.report


class CodeQualityEvaluator(BaseEvaluator):
    key = "code_quality"
    version = "1.0.0"
    kind = "code_quality"
    name = "Static code analysis"
    description = (
        "Parses experiment code with ast and flags parse errors, forbidden imports, process execution, unsafe "
        "deserialization, writes outside /workspace/output and missing seed handling."
    )
    config_schema = CodeQualityConfig
    independent = True

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: CodeQualityConfig = config
        sources: dict[str, str] = {}
        evidence: list[dict[str, Any]] = []
        findings: list[Finding] = []
        for name in sorted(artifacts.files):
            if name.endswith((".py", ".ipynb")):
                try:
                    sources[name] = artifacts.text(name)
                except UnicodeDecodeError:
                    findings.append(Finding(name, 0, "PARSE_ERROR", "error", "file is not valid UTF-8"))
                evidence.append({"check": "input", **artifacts.describe(name)})
        if cfg.include_spec_files and experiment.spec is not None:
            for path, content in sorted(experiment.spec.code.files.items()):
                if path.endswith((".py", ".ipynb")) and path not in sources:
                    sources[path] = content
                    evidence.append(
                        {"check": "input", "file": path, "source": "spec.code.files", "size": len(content.encode())}
                    )
        if not sources and not findings:
            return self._result(passed=None, confidence=0.0, warnings=["no Python code to analyse"])
        seed_env_var = cfg.seed_env_var or (
            experiment.spec.reproducibility.seed_env_var if experiment.spec else "AEGIS_SEED"
        )
        total_bytes = 0
        parsed = 0
        units = 0
        seeded = False
        lines = 0
        for name, source in sources.items():
            size = len(source.encode("utf-8"))
            total_bytes += size
            if size > cfg.max_file_bytes:
                findings.append(Finding(name, 0, "FILE_TOO_LARGE", "warning", f"{size} bytes > {cfg.max_file_bytes}"))
            chunks: list[tuple[str, str]]
            if name.endswith(".ipynb"):
                chunks, nb_findings = notebook_code(source, name)
                findings.extend(nb_findings)
                if any(f.code == "PARSE_ERROR" for f in nb_findings):
                    units += 1
                    continue
            else:
                chunks = [(name, source)]
            for chunk_name, code in chunks:
                units += 1
                report = analyze_source(code, chunk_name, cfg, seed_env_var=seed_env_var)
                findings.extend(report.findings)
                parsed += 1 if report.parsed else 0
                seeded = seeded or report.seeded
                lines += report.lines
        if total_bytes > cfg.max_total_bytes:
            findings.append(
                Finding("*", 0, "FILE_TOO_LARGE", "warning", f"total {total_bytes} bytes > {cfg.max_total_bytes}")
            )
        if not seeded:
            severity: Severity = "error" if cfg.require_seed_handling else "warning"
            findings.append(
                Finding(
                    "*",
                    0,
                    "NO_SEED_HANDLING",
                    severity,
                    f"no seeding found (random/numpy/torch seed or {seed_env_var})",
                )
            )
        errors = [f for f in findings if f.severity == "error"]
        warnings_ = [f for f in findings if f.severity == "warning"]
        by_code: dict[str, int] = {}
        for f in findings:
            by_code[f.code] = by_code.get(f.code, 0) + 1
        return self._result(
            passed=not errors,
            confidence=0.9 * (parsed / units if units else 0.0),
            metrics={
                "files": float(len(sources)),
                "lines": float(lines),
                "error_count": float(len(errors)),
                "warning_count": float(len(warnings_)),
                "parse_errors": float(by_code.get("PARSE_ERROR", 0)),
                "seed_handling": 1.0 if seeded else 0.0,
                "findings_by_code": dict(sorted(by_code.items())),
            },
            warnings=[f"{f.file}:{f.line}: {f.code}: {f.message}" for f in warnings_],
            evidence=evidence + [{"check": "finding", **f.as_dict()} for f in findings],
            details={"findings": [f.as_dict() for f in findings]},
        )
