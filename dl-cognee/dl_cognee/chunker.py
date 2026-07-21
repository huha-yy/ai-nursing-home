"""Text chunker for knowledge ingest (spec §6.3).

v1: blank-line split, section-aware, hard cap with sentence-boundary splitting.
"""

from __future__ import annotations

import re

_BLANK_LINE = re.compile(r"\n\s*\n+")
_SECTION_HEADING = re.compile(r"^# ", re.MULTILINE)
_SENTENCE_BOUNDARY = re.compile(r"[.?!]\s+|\n")


def chunk_text(
    content: str,
    content_type: str = "text/markdown",
    *,
    target_chars: int = 1500,
    max_chars: int = 4000,
) -> list[str]:
    """Split content into chunks suitable for embedding.

    Returns a list of chunk strings. chunk_idx is the 0-based position.
    """
    # Step 1: split on blank lines.
    raw_splits = [s.strip() for s in _BLANK_LINE.split(content) if s.strip()]
    if not raw_splits:
        return []

    # Step 2: re-join until reaching target_chars or hitting a section boundary.
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for split in raw_splits:
        is_section = bool(_SECTION_HEADING.match(split))
        if is_section or (current_len + len(split) > target_chars and current):
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(split)
        current_len += len(split)

    if current:
        chunks.append("\n\n".join(current))

    # Step 3: hard cap — split over-long chunks on sentence boundaries,
    # then fall back to fixed-width splitting for unbreakable text.
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(chunk) if s.strip()]
            sub = ""
            for sent in sentences:
                if len(sub) + len(sent) > max_chars and sub:
                    result.append(sub.strip())
                    sub = sent
                else:
                    sub = (sub + " " + sent).strip() if sub else sent
            if sub and len(sub) <= max_chars:
                result.append(sub.strip())
            elif sub:
                # Fallback: fixed-width split for boundary-free text.
                for i in range(0, len(sub), max_chars):
                    result.append(sub[i : i + max_chars].strip())

    return result
