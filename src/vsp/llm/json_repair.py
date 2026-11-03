"""JSON parse + repair (one retry): strip fences, extract object."""
from __future__ import annotations
import json
import re


def extract_json(text: str) -> dict:
    t = text.strip()
    # strip code fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        # find first { ... last }
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1 and j > i:
            t = t[i:j + 1]
    return json.loads(t)
