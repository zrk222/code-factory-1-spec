"""specline audit — the post-coding drift detector.

strict_lint stops ambiguity BEFORE the coder runs. This catches drift AFTER:
it compares what shipped against what the contract authorized, and fails when
the coder invented parameters, referenced undefined entities, or widened scope.

Deterministic: it parses, it does not judge intent.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from .strict_lint import _declared_terms, _requirement_lines, Finding

_MAGIC_NUMBER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
_PARAM_ASSIGN = re.compile(
    r"\b([A-Z][A-Z0-9_]{2,})\s*=\s*([^\n#]+)"
    r"|\b(timeout|retries|max_\w+|min_\w+|limit|threshold|ttl|batch_size|"
    r"page_size|expiry|expires_in)\s*[:=]\s*([^\n#,)]+)", re.I)


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


def _spec_authorized_numbers(spec_text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", spec_text)}


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


def audit_code_against_spec(spec_path, changed_files, slice_prefix=None, packet_files=None):
    rep = AuditReport()
    spec_text = Path(spec_path).read_text()
    ok_numbers = _spec_authorized_numbers(spec_text)
    authorized = {str(f) for f in (packet_files or [])}

    for fp in changed_files:
        fp = Path(fp)
        rel = str(fp)
        # Only the FILENAME determines test-ness. The full path can contain
        # 'test' incidentally (e.g. pytest temp dirs), which would wrongly
        # suppress scope/param findings.
        is_test = fp.name.lower().startswith("test") or fp.name.lower().endswith("_test.py") \
            or "tests" in fp.parts

        if slice_prefix and not _in_slice(fp, slice_prefix) and not is_test:
            rep.add("A_SCOPE_ESCAPE", "BLOCK",
                    f"{rel} is outside the authorized slice {slice_prefix!r}. The coder widened "
                    f"scope beyond its task.")
        if authorized and rel not in authorized and not is_test:
            rep.add("A_UNAUTHORIZED_FILE", "BLOCK",
                    f"{rel} was not in the packet's file list — packet was wrong or coder invented "
                    f"a file.")

        if not fp.exists() or fp.is_dir():
            continue
        try:
            code = fp.read_text()
        except (UnicodeDecodeError, OSError):
            continue

        for i, ln in enumerate(code.splitlines(), 1):
            stripped = ln.strip()
            if stripped.startswith(("#", "//", "*", "/*")):
                continue
            for m in _PARAM_ASSIGN.finditer(ln):
                val = (m.group(2) or m.group(4) or "").strip()
                for num in _MAGIC_NUMBER.findall(val):
                    if num not in ok_numbers:
                        name = (m.group(1) or m.group(3) or "param").strip()
                        rep.add("A_INVENTED_PARAM", "BLOCK",
                                f"{rel}:{i} sets {name}={val} but the spec never authorizes the "
                                f"value {num}. Invented parameter — add to spec or remove.", i)
                        break
            if re.search(r"\b(TODO|FIXME|XXX|NotImplemented|raise NotImplementedError)\b", ln):
                rep.add("A_STUB_LEFT", "BLOCK",
                        f"{rel}:{i} leaves a stub/TODO — constitution forbids shipping incomplete "
                        f"work.", i)
    return rep


def audit_report_lines(rep: AuditReport) -> list[str]:
    return [str(f) for f in rep.findings]
