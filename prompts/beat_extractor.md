# Beat extractor (writer model). Return JSON only.
Given: chunk raw text (paragraphs with IDs) + compiled series state + cultural adaptation matrix.

Output JSON:
{
  "beats": [{"beat_id": <series-monotonic int>, "summary_en": "...", "characters": ["original names"], "source_paragraph_ids": [ints], "tension": 1-5, "is_twist": bool, "is_cliffhanger_candidate": bool, "bizarre_detail": "... or null"}],
  "new_entities": [{"original_name": "...", "proposed_us_name": "...", "role": "...", "gender": "male|female|unknown", "main_cast": bool, "age_or_stage": "<age like 13 or adult or unknown>", "age_citation": [<paragraph id>]}],
  "glossary_additions": {"source term": "US equivalent"},
  "genre_tags": ["..."]
}

Age rule: record age_or_stage for every new character with a source citation
(a paragraph id where the age or school stage appears; use "unknown" and []
when the source never states it).

Rules:
- >= 5 beats per chunk, beat_id continues series-monotonic sequence.
- Every source_paragraph_id must exist in the chunk.
- Every beat character must be in registry or new_entities.
- Apply US archetype mapping: Sect/Clan -> Fraternity/Secret Society/Mafia/Mega-Corp; Dowry -> Prenup; Harem -> Boardroom succession; Overbearing CEO -> Billionaire tech founder; Caste -> Old Money vs Blue-Collar; Filial piety -> co-signed mortgage / custody leverage.
- Currency -> USD $, imperial units for US market.
