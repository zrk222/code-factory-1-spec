"""specline audit — the post-coding drift detector.

strict_lint stops ambiguity BEFORE the coder runs. This catches drift AFTER:
it compares what shipped against what the contract authorized, and fails when
the coder invented parameters, referenced undefined entities, or widened scope.

Deterministic: it parses, it does not judge intent.
"""
from __future__ import annotations
import re
import ast
import difflib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from .strict_lint import Finding

_UPPER_PARAM = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_KNOWN_PARAM = re.compile(
    r"^(?:timeout|retries|max_\w+|min_\w+|limit|threshold|ttl|batch_size|"
    r"page_size|expiry|expires_in)$", re.I)
_NONPY_ASSIGN = re.compile(
    r"(?<![\w$])(?:const\s+|let\s+|var\s+)?([A-Za-z_$][\w$]*)\s*(?:=|:)\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?![\w.])")
_TOML_ASSIGN = re.compile(
    r"^\s*(?:[A-Za-z0-9_-]+\.)*([A-Za-z_][\w-]*)\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*(?:#.*)?)$")
_MAX_BASELINE_BYTES = 1_000_000


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    @property
    def blocks(self): return [f for f in self.findings if f.severity == "BLOCK"]
    @property
    def warns(self): return [f for f in self.findings if f.severity == "WARN"]
    @property
    def ok(self) -> bool: return not self.blocks
    def add(self, code, severity, message, line=0):
        self.findings.append(Finding(code, severity, message, line))

    def attribution(self, changed_files):
        from .attribution import Attribution, FailureClass, UnitResult
        units = []
        for file_path in changed_files:
            path = Path(file_path)
            path_text = str(path)
            file_findings = [f for f in self.blocks if path_text in f.message]
            spans = []
            if path.exists() and path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text())
                    spans = [
                        (node.name, node.lineno, getattr(node, "end_lineno", node.lineno))
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                except (SyntaxError, OSError, UnicodeDecodeError):
                    spans = []
            if not spans:
                spans = [("<module>", 1, 10**9)]
            for function_name, start, end in sorted(spans, key=lambda item: (item[1], item[0])):
                related = [
                    finding for finding in file_findings
                    if finding.line == 0 or start <= finding.line <= end
                ]
                failure_class = None
                if related:
                    code = related[0].code
                    failure_class = (
                        FailureClass.INVENTED_PARAM if code == "A_INVENTED_PARAM"
                        else FailureClass.STUB_UNFILLED if code == "A_STUB_LEFT"
                        else FailureClass.SCOPE_ESCAPE
                    )
                units.append(UnitResult(
                    unit=f"function:{path_text}:{function_name}",
                    stage="drift_audit",
                    passed=not related,
                    evidence="no drift found" if not related else related[0].message,
                    failure_class=failure_class,
                ))
        return Attribution("drift_audit", len(units), sum(u.passed for u in units), units)


def _spec_authorized_numbers(spec_text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"(?<![\w.])(-?\d+(?:\.\d+)?)\b", spec_text)}


def _in_slice(fp: Path, slice_prefix: str | None) -> bool:
    """Segment-aware slice membership: an absolute path .../refund/logic.py is
    inside slice 'refund'. This is what made the naive str.startswith unreliable."""
    if slice_prefix is None:
        return True
    sp = slice_prefix.rstrip("/")
    parts = fp.parts
    if sp in parts:
        return True
    for i in range(len(parts)):
        tail = "/".join(parts[i:])
        if tail == sp or tail.startswith(sp + "/"):
            return True
    return False


def _parameter_name(name: str) -> bool:
    return bool(_UPPER_PARAM.fullmatch(name) or _KNOWN_PARAM.fullmatch(name))


def _numeric_literals(node):
    """Return numeric constants while deliberately excluding string and index values."""
    if isinstance(node, ast.Subscript):
        return []
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)):
        value = -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
        return [(str(value), node.lineno)]
    values = []
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        values.append((str(node.value), node.lineno))
    for child in ast.iter_child_nodes(node):
        values.extend(_numeric_literals(child))
    return values


def _assignment_parameter_candidates(node):
    candidates = []
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        targets, value = [node.target], node.value
    else:
        return candidates
    if value is None:
        return candidates
    for target in targets:
        names = [target.id] if isinstance(target, ast.Name) else []
        if isinstance(target, ast.Attribute):
            names = [target.attr]
        if isinstance(target, (ast.Tuple, ast.List)):
            names = [elt.id for elt in target.elts if isinstance(elt, ast.Name)]
        for name in names:
            if _parameter_name(name):
                candidates.extend((line, name, number) for number, line in _numeric_literals(value))
    return candidates


def _function_default_candidates(node):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = zip(positional[-len(node.args.defaults):], node.args.defaults) if node.args.defaults else []
    keyword_defaults = ((arg, default) for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
                        if default is not None)
    candidates = []
    for arg, default in [*defaults, *keyword_defaults]:
        if _parameter_name(arg.arg):
            candidates.extend((line, arg.arg, number) for number, line in _numeric_literals(default))
    return candidates


def _parameter_candidates(tree):
    candidates = []
    for node in ast.walk(tree):
        candidates.extend(_assignment_parameter_candidates(node))
        if isinstance(node, ast.keyword) and node.arg and _parameter_name(node.arg):
            candidates.extend((line, node.arg, number) for number, line in _numeric_literals(node.value))
        candidates.extend(_function_default_candidates(node))
    return candidates


def _mask_quote(chars, index, quote, escaped):
    char = chars[index]
    if char != "\n":
        chars[index] = " "
    if escaped:
        return quote, False, index + 1
    if char == "\\":
        return quote, True, index + 1
    return (None if char == quote else quote), False, index + 1


def _mask_comment(chars, index, state):
    char = chars[index]
    following = chars[index + 1] if index + 1 < len(chars) else ""
    if state == "line":
        if char == "\n":
            return None, False, index + 1
        chars[index] = " "
        return state, False, index + 1
    if char == "*" and following == "/":
        chars[index] = chars[index + 1] = " "
        return None, False, index + 2
    if char != "\n":
        chars[index] = " "
    return state, False, index + 1


def _mask_start(chars, index, hash_comments):
    char = chars[index]
    following = chars[index + 1] if index + 1 < len(chars) else ""
    if char in {"'", '"', "`"}:
        chars[index] = " "
        return char, False, index + 1
    if char == "/" and following in {"/", "*"}:
        chars[index] = chars[index + 1] = " "
        return ("line" if following == "/" else "block"), False, index + 2
    if hash_comments and char == "#":
        chars[index] = " "
        return "line", False, index + 1
    return None, False, index + 1


def _mask_step(chars, index, state, escaped, hash_comments):
    if state in {"'", '"', "`"}:
        return _mask_quote(chars, index, state, escaped)
    if state in {"line", "block"}:
        return _mask_comment(chars, index, state)
    return _mask_start(chars, index, hash_comments)


def _mask_non_code(text, hash_comments=True):
    """Mask quoted strings and comments before scanning non-Python assignments."""
    chars = list(text)
    state = None
    escaped = False
    index = 0
    while index < len(chars):
        state, escaped, index = _mask_step(chars, index, state, escaped, hash_comments)
    return "".join(chars)


def _nonpython_candidates(code, rel, strict_source):
    if Path(rel).suffix.lower() == ".toml":
        try:
            tomllib.loads(code)
        except tomllib.TOMLDecodeError as exc:
            if strict_source:
                raise ValueError(f"current TOML file cannot be parsed: {rel}") from exc
            return []
        candidates = []
        for line, text in enumerate(code.splitlines(), 1):
            match = _TOML_ASSIGN.match(text)
            if match and _parameter_name(match.group(1)):
                candidates.append((line, match.group(1), match.group(2)))
        return candidates
    candidates = []
    is_js = Path(rel).suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}
    for line, text in enumerate(_mask_non_code(code, hash_comments=not is_js).splitlines(), 1):
        for match in _NONPY_ASSIGN.finditer(text):
            name = match.group(1)
            if _parameter_name(name):
                candidates.append((line, name, match.group(2)))
    return candidates


def _changed_line_numbers(current: str, baseline: str | None) -> set[int]:
    if baseline is None:
        return set(range(1, len(current.splitlines()) + 1))
    before = baseline.splitlines()
    after = current.splitlines()
    changed = set()
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag in {"replace", "insert"}:
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def _read_baseline_blob(root: Path, commit: str, rel: str) -> bytes | None:
    listing = subprocess.run(
        ["git", "ls-tree", "-z", commit, "--", rel], cwd=root,
        capture_output=True, check=False, shell=False,
    )
    if listing.returncode:
        raise ValueError("could not inspect baseline commit path")
    if not listing.stdout:
        return None
    record = listing.stdout.split(b"\t", 1)[0]
    if not record.startswith((b"100644 blob ", b"100755 blob ")):
        raise ValueError("baseline path is not a regular Git blob")
    process = subprocess.Popen(
        ["git", "show", f"{commit}:{rel}"], cwd=root, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, shell=False,
    )
    assert process.stdout is not None
    data = process.stdout.read(_MAX_BASELINE_BYTES + 1)
    if len(data) > _MAX_BASELINE_BYTES:
        process.kill()
        process.wait()
        raise ValueError("baseline file exceeds the size limit")
    return_code = process.wait()
    if return_code:
        raise ValueError("baseline file is unreadable")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("baseline file is not valid UTF-8") from exc
    return data


def load_git_baselines(root, files, base_commit: str) -> dict[str, bytes | None]:
    """Load bounded, workspace-relative file baselines from an explicit full commit."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base_commit or ""):
        raise ValueError("base commit must be a full 40-character hexadecimal object ID")
    root_path = Path(root).resolve()
    result = {}
    for item in files:
        path = Path(item)
        if not path.is_absolute():
            path = root_path / path
        lexical = Path(os.path.abspath(path))
        try:
            lexical_rel = lexical.relative_to(root_path)
        except ValueError as exc:
            raise ValueError("baseline file must be inside the workspace root") from exc
        cursor = root_path
        for part in lexical_rel.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("baseline file path cannot contain symlinks")
        resolved = lexical.resolve(strict=True)
        try:
            rel = resolved.relative_to(root_path).as_posix()
        except ValueError as exc:
            raise ValueError("baseline file must be inside the workspace root") from exc
        if not resolved.is_file():
            raise ValueError("baseline file must be a regular, non-symlink file")
        result[str(path)] = _read_baseline_blob(root_path, base_commit, rel)
    return result


def _audit_file_authority(fp, rel, slice_prefix, authorized, report):
    is_test = fp.name.lower().startswith("test") or fp.name.lower().endswith("_test.py") \
        or "tests" in fp.parts
    if slice_prefix and not _in_slice(fp, slice_prefix) and not is_test:
        report.add("A_SCOPE_ESCAPE", "BLOCK",
                   f"{rel} is outside the authorized slice {slice_prefix!r}. The coder widened "
                   "scope beyond its task.")
    if authorized and rel not in authorized and not is_test:
        report.add("A_UNAUTHORIZED_FILE", "BLOCK",
                   f"{rel} was not in the packet's file list — packet was wrong or coder invented "
                   "a file.")


def _read_audit_source(fp, rel, bounded):
    if not fp.exists() or fp.is_dir():
        if bounded:
            raise ValueError(f"current baseline-audited file is missing or not regular: {rel}")
        return None
    try:
        if bounded:
            with fp.open("rb") as source:
                raw = source.read(_MAX_BASELINE_BYTES + 1)
            if len(raw) > _MAX_BASELINE_BYTES:
                raise ValueError(f"current file exceeds the {_MAX_BASELINE_BYTES} byte limit: {rel}")
        else:
            raw = fp.read_bytes()
        return raw.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, OSError) as exc:
        if bounded:
            raise ValueError(f"current file is unreadable or not valid UTF-8: {rel}") from exc
        return None


def _baseline_text(rel, baseline_files):
    if baseline_files is None:
        return None
    if rel not in baseline_files:
        raise ValueError(f"baseline is missing an explicit entry for {rel}")
    prior = baseline_files[rel]
    if prior is None:
        return None
    if isinstance(prior, str):
        baseline = prior
        raw = prior.encode("utf-8")
    elif isinstance(prior, bytes):
        raw = prior
        try:
            baseline = prior.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"baseline is not valid UTF-8: {rel}") from exc
    else:
        raise ValueError(f"baseline entry must be UTF-8 text, bytes, or None: {rel}")
    if len(raw) > _MAX_BASELINE_BYTES:
        raise ValueError(f"baseline file exceeds the {_MAX_BASELINE_BYTES} byte limit: {rel}")
    return baseline


def _audit_stubs(code, rel, report):
    for line, text in enumerate(code.splitlines(), 1):
        if re.search(r"\b(TODO|FIXME|XXX|NotImplemented|raise NotImplementedError)\b", text):
            report.add("A_STUB_LEFT", "BLOCK",
                       f"{rel}:{line} leaves a stub/TODO — constitution forbids incomplete work.", line)


def _audit_parameters(code, baseline, strict_source, rel, authorized_numbers, report):
    if Path(rel).suffix.lower() not in {".py", ".pyi", ".pyw"}:
        candidates = _nonpython_candidates(code, rel, strict_source)
        changed_lines = _changed_line_numbers(code, baseline)
        _report_parameter_candidates(candidates, changed_lines, rel, authorized_numbers, report)
        return
    try:
        tree = ast.parse(code, filename=rel)
    except SyntaxError as exc:
        if strict_source:
            raise ValueError(f"current Python file cannot be parsed: {rel}") from exc
        return
    changed_lines = _changed_line_numbers(code, baseline)
    _report_parameter_candidates(
        _parameter_candidates(tree), changed_lines, rel, authorized_numbers, report
    )


def _report_parameter_candidates(candidates, changed_lines, rel, authorized_numbers, report):
    for line, name, number in candidates:
        if line in changed_lines and number not in authorized_numbers:
            report.add("A_INVENTED_PARAM", "BLOCK",
                       f"{rel}:{line} sets {name} to {number} but the spec never authorizes the "
                       f"value {number}. Invented parameter — add to spec or remove.", line)


def audit_code_against_spec(
    spec_path, changed_files, slice_prefix=None, packet_files=None, *, baseline_files=None
):
    rep = AuditReport()
    spec_text = Path(spec_path).read_text()
    ok_numbers = _spec_authorized_numbers(spec_text)
    authorized = {str(f) for f in (packet_files or [])}

    for fp in changed_files:
        fp = Path(fp)
        rel = str(fp)
        _audit_file_authority(fp, rel, slice_prefix, authorized, rep)
        code = _read_audit_source(fp, rel, baseline_files is not None)
        if code is None:
            continue
        baseline = _baseline_text(rel, baseline_files)
        _audit_stubs(code, rel, rep)
        _audit_parameters(code, baseline, baseline_files is not None, rel, ok_numbers, rep)
    return rep


def audit_report_lines(rep: AuditReport) -> list[str]:
    return [str(f) for f in rep.findings]
