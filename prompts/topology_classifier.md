# Topology classifier (judge model). Return JSON only.
Classify the source into ONE topology: SINGLE_SHOT | ANTHOLOGY | EPISODIC_SERIES | CONTINUOUS_SERIAL.

Input gives: heading list, approx word_equiv, first ~1500 words, 3 sampled passages.

Return JSON:
{"topology": "...", "confidence": 0.0-1.0, "source_language": "en|vi|zh|ja|...", "genre": "...", "genre_tags": ["..."], "notes": "..."}

Hints:
- Reddit AITA / ProRevenge / creepypasta < 3500 words -> SINGLE_SHOT
- independent stories with headings -> ANTHOLOGY
- detective cases / procedural with Case headings -> EPISODIC_SERIES
- long romance/revenge serial chapters -> CONTINUOUS_SERIAL
- genre_tags drive template routing: revenge/betrayal/action -> T1; mystery/thriller/paranormal/horror -> T2; family/workplace/aita/drama -> T3.
