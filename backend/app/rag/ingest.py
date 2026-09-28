"""Turn policy markdown files into citable chunks: one chunk = one `##` section.

Section-level chunking is deliberate. The documents are short and each section
is a self-contained rule, so a section is exactly the unit we must cite
("document + section"). Splitting further would break tables; merging would
make citations vague.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Sections of older documents that a newer document explicitly replaces.
# Source: 03_returns_policy_update_2026.md, preamble: "This update replaces the
# return window and pickup charge rules in all earlier return policies."
SUPERSEDED = {
    ("02_returns_policy_2024.md", "1. Return Window"):
        "The 7-day window is REPLACED by 03_returns_policy_update_2026.md § 1. New Return Window. "
        "The condition rule (unused, original tags and packaging) still applies.",
    ("02_returns_policy_2024.md", "4. Exchanges"):
        "The '7-day window' here refers to the old return window, which was replaced by "
        "03_returns_policy_update_2026.md § 1. Exchange eligibility (size issues, clothing/footwear, "
        "stock) is otherwise unchanged.",
    ("02_returns_policy_2024.md", "5. Return Pickup Charges"):
        "REPLACED by 03_returns_policy_update_2026.md § 3. Return Pickup Charges (pickup is free for "
        "all eligible returns; the ₹50 fee no longer applies).",
}


@dataclass
class Chunk:
    chunk_id: str
    doc: str
    doc_title: str
    effective: str
    section: str
    text: str
    superseded_note: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.doc} § {self.section}"

    def render(self) -> str:
        """How the chunk is shown to the LLM."""
        head = f"[SOURCE: {self.citation}] ({self.doc_title}, effective {self.effective})"
        warn = f"\n⚠ SUPERSEDED IN PART: {self.superseded_note}" if self.superseded_note else ""
        return f"{head}{warn}\n{self.text.strip()}"


def parse_markdown(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", raw, re.M)
    title = title_match.group(1).strip() if title_match else path.stem
    eff_match = re.search(r"Effective:\s*(.+)", raw)
    effective = eff_match.group(1).strip() if eff_match else "unknown"

    parts = re.split(r"^##\s+", raw, flags=re.M)
    preamble, sections = parts[0], parts[1:]
    chunks: list[Chunk] = []

    # Keep a preamble chunk when it carries rules (e.g. the 2026 update's "replaces" statement).
    pre_body = "\n".join(
        line for line in preamble.splitlines()
        if not line.startswith("# ") and not line.lower().startswith("effective")
    ).strip()
    if pre_body:
        chunks.append(Chunk(f"{path.stem}::0", path.name, title, effective, "Preamble", pre_body))

    for i, sec in enumerate(sections, start=1):
        header, _, body = sec.partition("\n")
        header = header.strip()
        chunks.append(Chunk(
            chunk_id=f"{path.stem}::{i}",
            doc=path.name,
            doc_title=title,
            effective=effective,
            section=header,
            text=f"{header}\n{body.strip()}",
            superseded_note=SUPERSEDED.get((path.name, header)),
        ))
    return chunks


def load_chunks(policy_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(policy_dir).glob("*.md")):
        chunks.extend(parse_markdown(path))
    return chunks
