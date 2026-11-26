from __future__ import annotations
import io
import locale
from pathlib import Path


def read_text_with_encoding(path) -> tuple[str, str]:
    """Read UTF-8, falling back to the locale for files older SpecLine wrote on Windows."""
    data = Path(path).read_bytes()
    try:
        return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8").read(), "utf-8"
    except UnicodeDecodeError:
        pass
    for encoding in (locale.getpreferredencoding(False), "cp1252", "latin-1"):
        try:
            return io.TextIOWrapper(io.BytesIO(data), encoding=encoding).read(), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise AssertionError("latin-1 decodes every byte")  # pragma: no cover


def read_text(path) -> str:
    return read_text_with_encoding(path)[0]

def templates_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "templates"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[1] / "templates"  # pre-0.6 source checkouts; remove next release

def project_dirs(root: Path) -> dict:
    return {"specs": root/"specs", "plans": root/"plans", "context": root/"context",
            "adr": root/"adr", "packets": root/"packets", "slices": root/"slices",
            "skeleton": root/"skeleton", "handoff": root/"handoff", "receipts": root/"receipts"}
