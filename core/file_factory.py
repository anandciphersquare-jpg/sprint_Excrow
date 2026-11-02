"""Generates small, real sample files for document-upload testing.

Pure-Python fallbacks keep the runtime dependency-free. Files are created
once under fixtures/documents/ and reused. Includes an unsupported type
(.txt) for negative upload checks.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List

DOCS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "documents"

_MIN_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
    b"/Resources<<>>/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 18 Tf 20 100 Td (Sample doc) Tj ET\n"
    b"endstream endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    def chunk(typ: bytes, data: bytes) -> bytes:
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolor
    row = b"\x00" + (b"\x33\x66\x99" * width)                      # filter + RGB pixels
    raw = row * height
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _write_jpg(path: Path) -> None:
    """Prefer a real JPEG via Pillow; fall back to a bundled minimal JPEG."""
    try:
        from PIL import Image  # type: ignore
        Image.new("RGB", (8, 8), (51, 102, 153)).save(path, "JPEG")
        return
    except Exception:
        pass
    # Minimal valid baseline JPEG (1x1) if Pillow is unavailable.
    path.write_bytes(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb00430008060607060508"
        "070707090909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720"
        "222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b08000100"
        "0101011100ffc400140001000000000000000000000000000000000009ffc40014"
        "1000000000000000000000000000000000000000ffda0008010100003f00d2cf20"
        "ffd9"
    ))


def ensure_sample_documents() -> List[str]:
    """Return [pdf, png, jpg] paths, generating any that are missing."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    pdf, png, jpg = DOCS_DIR / "sample.pdf", DOCS_DIR / "sample.png", DOCS_DIR / "sample.jpg"
    if not pdf.exists():
        pdf.write_bytes(_MIN_PDF)
    if not png.exists():
        png.write_bytes(_png_bytes())
    if not jpg.exists():
        _write_jpg(jpg)
    return [str(pdf), str(png), str(jpg)]


def unsupported_sample() -> str:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    txt = DOCS_DIR / "unsupported.txt"
    if not txt.exists():
        txt.write_text("This file type should be rejected by the uploader.\n")
    return str(txt)


def oversize_sample(mb: int = 12) -> str:
    """Create a file larger than a typical upload cap for max-size testing."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    big = DOCS_DIR / f"oversize_{mb}mb.pdf"
    if not big.exists():
        big.write_bytes(_MIN_PDF + b"\x00" * (mb * 1024 * 1024))
    return str(big)
