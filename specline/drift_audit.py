"""specline audit — the post-coding drift detector.

strict_lint stops ambiguity BEFORE the coder runs. This catches drift AFTER:
it compares what shipped against what the contract authorized, and fails when
the coder invented parameters, referenced undefined entities, or widened scope.

Deterministic: it parses, it does not judge intent.
"""
from __future__ import annotations
import re
import ast
from collections import Counter
import os
import subprocess
import tokenize
import io
import time
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
_MAX_BASELINE_FILES = 256
_MAX_BASELINE_TOTAL_BYTES = 16_000_000
_GIT_TIMEOUT_SECONDS = 5
_MAX_BASELINE_SECONDS = 30
_MAX_BASELINE_PATH_BYTES = 4096


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


def _spec_authorized_numbers(spec_text: str) -> dict[str, set[str]]:
    """Read explicit ``name = number`` facts from an Authorized parameters section."""
    authorized = {}
    in_section = False
    for line in spec_text.splitlines():
        if line.startswith("#"):
            in_section = bool(re.match(r"^#{1,6}\s+Authorized parameters\s*$", line, re.I))
            continue
        if not in_section:
            continue
        match = re.match(
            r"^\s*(?:[-*]\s*)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s*=\s*`?(-?\d+(?:\.\d+)?)`?\s*$",
            line,
        )
        if match:
            authorized.setdefault(match.group(1), set()).add(match.group(2))
    return authorized


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
        if isinstance(target, (ast.Tuple, ast.List)):
            candidates.extend(_destructured_parameter_candidates(target, value))
            continue
        name = target.id if isinstance(target, ast.Name) else (
            target.attr if isinstance(target, ast.Attribute) else None
        )
        if name and _parameter_name(name):
            candidates.extend((line, name, number) for number, line in _numeric_literals(value))
    return candidates


def _destructured_parameter_candidates(target, value):
    parameters = _parameter_targets(target)
    if not parameters:
        return []
    if not isinstance(value, (ast.Tuple, ast.List)) or len(target.elts) != len(value.elts):
        return [(line, name, None) for name, line in parameters]
    candidates = []
    for child_target, child_value in zip(target.elts, value.elts):
        if isinstance(child_target, (ast.Tuple, ast.List)):
            candidates.extend(_destructured_parameter_candidates(child_target, child_value))
        else:
            name = child_target.id if isinstance(child_target, ast.Name) else (
                child_target.attr if isinstance(child_target, ast.Attribute) else None
            )
            if name and _parameter_name(name):
                numbers = _numeric_literals(child_value)
                candidates.extend((line, name, number) for number, line in numbers)
                if not numbers:
                    candidates.append((child_target.lineno, name, None))
    return candidates


def _parameter_targets(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        return [item for child in target.elts for item in _parameter_targets(child)]
    name = target.id if isinstance(target, ast.Name) else (
        target.attr if isinstance(target, ast.Attribute) else None
    )
    return [(name, target.lineno)] if name and _parameter_name(name) else []


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
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        scope = _lexical_scope(node, parents)
        candidates.extend((*candidate, scope) for candidate in _assignment_parameter_candidates(node))
        if isinstance(node, ast.keyword) and node.arg and _parameter_name(node.arg):
            candidates.extend((line, node.arg, number, scope) for number, line in _numeric_literals(node.value))
        default_scope = (*scope, node.name) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
        candidates.extend((*candidate, default_scope) for candidate in _function_default_candidates(node))
    return candidates


def _lexical_scope(node, parents):
    scopes = []
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes.append(f"{type(parent).__name__}:{parent.name}")
        parent = parents.get(parent)
    return tuple(reversed(scopes))


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
    before = Counter(baseline.splitlines())
    seen = Counter()
    changed = set()
    for line_number, text in enumerate(current.splitlines(), 1):
        seen[text] += 1
        if seen[text] > before[text]:
            changed.add(line_number)
    return changed


def _git_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("baseline Git inventory exceeded its aggregate time limit")
    return min(_GIT_TIMEOUT_SECONDS, remaining)


def _baseline_blob_identity(root: Path, commit: str, rel: str, deadline: float):
    path_bytes = rel.encode("utf-8", errors="strict")
    if not path_bytes or len(path_bytes) > _MAX_BASELINE_PATH_BYTES:
        raise ValueError("baseline path exceeds the bounded Git path limit")
    try:
        listing = subprocess.run(
            ["git", "--literal-pathspecs", "ls-tree", "-l", "-z", commit, "--", rel], cwd=root,
            capture_output=True, check=False, shell=False, timeout=_git_timeout(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("baseline Git inventory exceeded its time limit") from exc
    if listing.returncode:
        raise ValueError("could not inspect baseline commit path")
    if not listing.stdout:
        return None, None
    records = listing.stdout.rstrip(b"\0").split(b"\0")
    if len(records) != 1 or b"\t" not in records[0]:
        raise ValueError("baseline path lookup returned multiple or malformed records")
    header, listed_path = records[0].split(b"\t", 1)
    if listed_path != path_bytes:
        raise ValueError("baseline path lookup did not return the requested literal path")
    record = header.split()
    if len(record) != 4 or record[0] not in {b"100644", b"100755"} or record[1] != b"blob":
        raise ValueError("baseline path is not a regular Git blob")
    try:
        size = int(record[3])
    except ValueError as exc:
        raise ValueError("baseline blob size is unavailable") from exc
    if size > _MAX_BASELINE_BYTES:
        raise ValueError("baseline file exceeds the size limit")
    return record[2].decode("ascii"), size


def _read_baseline_contents(root: Path, oid: str, size: int, deadline: float) -> bytes:
    try:
        process = subprocess.run(
            ["git", "cat-file", "blob", oid], cwd=root,
            capture_output=True, check=False, shell=False, timeout=_git_timeout(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("baseline Git read exceeded its time limit") from exc
    if process.returncode:
        raise ValueError("baseline file is unreadable")
    data = process.stdout
    if len(data) != size:
        raise ValueError("baseline blob size changed or is inconsistent")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("baseline file is not valid UTF-8") from exc
    return data


def _read_baseline_blob(root: Path, commit: str, rel: str, deadline: float | None = None) -> bytes | None:
    if deadline is None:
        deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    oid, size = _baseline_blob_identity(root, commit, rel, deadline)
    if oid is None:
        return None
    return _read_baseline_contents(root, oid, size, deadline)


def _resolve_baseline_path(root_path: Path, item) -> tuple[Path, str]:
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
    return resolved, rel


def load_git_baselines(root, files, base_commit: str) -> dict[str, bytes | None]:
    """Load bounded, workspace-relative file baselines from an explicit full commit."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base_commit or ""):
        raise ValueError("base commit must be a full 40-character hexadecimal object ID")
    root_path = Path(root).resolve()
    if len(files) > _MAX_BASELINE_FILES:
        raise ValueError(f"baseline file count exceeds the {_MAX_BASELINE_FILES} file limit")
    result = {}
    total_bytes = 0
    deadline = time.monotonic() + _MAX_BASELINE_SECONDS
    for item in files:
        if time.monotonic() >= deadline:
            raise ValueError("baseline Git reads exceeded the aggregate time limit")
        resolved, rel = _resolve_baseline_path(root_path, item)
        data = _read_baseline_blob(root_path, base_commit, rel, deadline)
        total_bytes += len(data or b"")
        if total_bytes > _MAX_BASELINE_TOTAL_BYTES:
            raise ValueError("baseline total bytes exceed the aggregate size limit")
        result[str(resolved)] = data
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


def _baseline_entry_key(rel, baseline_files, source_path):
    prior_key = rel if rel in baseline_files else None
    candidates = [source_path, Path(rel)] if source_path is not None else [Path(rel)]
    if prior_key is None:
        for candidate in candidates:
            try:
                canonical = str(Path(candidate).resolve())
            except (OSError, RuntimeError):
                continue
            if canonical in baseline_files:
                prior_key = canonical
                break
    if prior_key is None:
        raise ValueError(f"baseline is missing an explicit entry for {rel}")
    return prior_key


def _decode_baseline_text(rel, prior):
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


def _baseline_text(rel, baseline_files, source_path=None):
    if baseline_files is None:
        return None
    prior_key = _baseline_entry_key(rel, baseline_files, source_path)
    return _decode_baseline_text(rel, baseline_files[prior_key])


def _audit_stubs(code, rel, report):
    if Path(rel).suffix.lower() not in {".py", ".pyi", ".pyw"}:
        _audit_nonpython_stubs(code, rel, report)
        return
    try:
        tree = ast.parse(code, filename=rel)
    except SyntaxError:
        return
    _audit_python_todo_comments(code, rel, report)
    parents = _ast_parent_map(tree)
    for node in ast.walk(tree):
        if not _is_notimplemented_raise(node):
            continue
        function = _enclosing_function(node, parents)
        if function is None or _is_abstract_contract(function, parents):
            continue
        report.add("A_STUB_LEFT", "BLOCK",
                   f"{rel}:{node.lineno} implementation {function.name} raises NotImplementedError.",
                   node.lineno)


def _audit_nonpython_stubs(code, rel, report):
    for line_no, text in enumerate(_mask_non_code(code, hash_comments=True).splitlines(), 1):
        if re.search(r"\b(?:TODO|FIXME|XXX|NotImplementedError)\b", text):
            report.add("A_STUB_LEFT", "BLOCK", f"{rel}:{line_no} contains an executable stub marker.", line_no)


def _audit_python_todo_comments(code, rel, report):
    for token in tokenize.generate_tokens(io.StringIO(code).readline):
        if token.type == tokenize.COMMENT:
            marker = re.match(r"#\s*(TODO|FIXME|XXX)\b", token.string, re.I)
            if marker:
                line = token.start[0]
                report.add("A_STUB_LEFT", "BLOCK", f"{rel}:{line} leaves a TODO/FIXME marker.", line)


def _ast_parent_map(tree):
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_notimplemented_raise(node):
    if not isinstance(node, ast.Raise):
        return False
    fn = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(fn, ast.Name) and fn.id == "NotImplementedError"


def _enclosing_function(node, parents):
    parent = parents.get(node)
    while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parent = parents.get(parent)
    return parent


def _is_abstract_contract(function, parents):
    module = parents.get(function)
    while module is not None and not isinstance(module, ast.Module):
        module = parents.get(module)
    if module is None:
        return False
    imports, shadowed = _contract_import_bindings(module)
    if _has_abstractmethod_decorator(function, imports, shadowed):
        return True
    class_node = _enclosing_class(function, parents)
    if class_node is None:
        return False
    return any(_is_protocol_base(base, imports["protocols"], shadowed) for base in class_node.bases)


def _contract_import_bindings(module):
    abstract_names = set()
    abstract_modules = set()
    protocol_names = set()
    shadowed = {
        node.id
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    } | {
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for statement in module.body:
        if (isinstance(statement, ast.ImportFrom) and statement.level == 0
                and statement.module == "abc"):
            abstract_names.update(alias.asname or alias.name for alias in statement.names
                                  if alias.name == "abstractmethod")
        elif (isinstance(statement, ast.ImportFrom) and statement.level == 0
              and statement.module in {"typing", "typing_extensions"}):
            protocol_names.update(alias.asname or alias.name for alias in statement.names
                                  if alias.name == "Protocol")
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name == "abc":
                    abstract_modules.add(bound)
                elif alias.name in {"typing", "typing_extensions"}:
                    protocol_names.add(f"{bound}.Protocol")
    return {
        "abstract_names": abstract_names,
        "abstract_modules": abstract_modules,
        "protocols": protocol_names,
    }, shadowed


def _has_abstractmethod_decorator(function, imports, shadowed):
    return any(
        isinstance(decorator, ast.Name)
        and decorator.id in imports["abstract_names"] - shadowed
        or isinstance(decorator, ast.Attribute)
        and decorator.attr == "abstractmethod"
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id in imports["abstract_modules"] - shadowed
        for decorator in function.decorator_list
    )


def _enclosing_class(function, parents):
    parent = parents.get(function)
    while parent is not None and not isinstance(parent, ast.ClassDef):
        parent = parents.get(parent)
    return parent


def _is_protocol_base(base, protocol_names, shadowed):
    if isinstance(base, ast.Name):
        return base.id in protocol_names - shadowed
    return (
        isinstance(base, ast.Attribute)
        and isinstance(base.value, ast.Name)
        and f"{base.value.id}.{base.attr}" in protocol_names
        and base.value.id not in shadowed
    )


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
    if baseline is not None:
        try:
            baseline_candidates = Counter(
                (name, number, scope)
                for _line, name, number, scope in _parameter_candidates(ast.parse(baseline, filename=rel))
            )
        except SyntaxError as exc:
            raise ValueError(f"baseline Python file cannot be parsed: {rel}") from exc
        candidates = []
        for candidate in _parameter_candidates(tree):
            identity = (candidate[1], candidate[2], candidate[3])
            if baseline_candidates[identity]:
                baseline_candidates[identity] -= 1
            else:
                candidates.append(candidate)
        changed_lines = {candidate[0] for candidate in candidates}
    else:
        candidates = _parameter_candidates(tree)
    _report_parameter_candidates(
        candidates, changed_lines, rel, authorized_numbers, report
    )


def _report_parameter_candidates(candidates, changed_lines, rel, authorized_numbers, report):
    for candidate in candidates:
        line, name, number = candidate[:3]
        if line in changed_lines and (number is None or number not in authorized_numbers.get(name, set())):
            report.add("A_INVENTED_PARAM", "BLOCK",
                       f"{rel}:{line} sets {name} to {number} but the spec has no matching "
                       "parameter declaration, or the destructuring value is unknown.", line)


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
        baseline = _baseline_text(rel, baseline_files, source_path=fp)
        _audit_stubs(code, rel, rep)
        _audit_parameters(code, baseline, baseline_files is not None, rel, ok_numbers, rep)
    return rep


def audit_report_lines(rep: AuditReport) -> list[str]:
    return [str(f) for f in rep.findings]
