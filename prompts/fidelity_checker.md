# Fidelity checker (judge family). Return JSON only.
Inputs: atomic claims (sentences from narration) plus the selected beats'
verbatim source paragraphs.

For each claim, output supported, contradicted or not_in_source with the
source paragraph ids that prove it. Mark connective or emotional sentences
that carry no factual load as stylistic true (at most 3 per episode).

Output JSON:
{"claims": [{"claim": "<exact narration sentence>", "verdict": "supported|contradicted|not_in_source", "source_paragraph_ids": [ints], "stylistic": bool}]}
