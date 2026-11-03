"""LLM-backed topology classification (1 judge call max)."""
from __future__ import annotations
import json


def build_classifier_input(source: dict, headings: list[dict], word_eq: int) -> str:
    paras = source["paragraphs"]
    head_txt = "\n".join(h["text"] for h in headings[:30]) or "(no headings)"
    first = " ".join(p["text"] for p in paras[:8])[:6000]
    n = len(paras)
    samples = []
    for idx in [n // 3, n // 2, 2 * n // 3]:
        if 0 <= idx < n:
            samples.append(paras[idx]["text"][:2000])
    return (f"word_equiv={word_eq}\nHEADINGS:\n{head_txt}\n\nFIRST:\n{first}\n\n"
            + "\n\n".join(f"SAMPLE{i+1}:\n{s}" for i, s in enumerate(samples)))


def parse_classifier_output(text: str) -> dict:
    data = json.loads(text)
    topo = data.get("topology", "CONTINUOUS_SERIAL")
    if topo not in ("SINGLE_SHOT", "ANTHOLOGY", "EPISODIC_SERIES", "CONTINUOUS_SERIAL"):
        raise ValueError(f"Unknown topology: {topo}")
    return {
        "topology": topo,
        "confidence": float(data.get("confidence", 0.7)),
        "source_language": data.get("source_language", "en"),
        "genre": data.get("genre", ""),
        "genre_tags": data.get("genre_tags", []) or [],
        "notes": data.get("notes", ""),
    }
