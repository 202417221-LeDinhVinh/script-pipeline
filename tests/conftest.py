"""Shared fixtures: isolated root (config/prompts/assets copied), passing-script builder."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def vsp_root(tmp_path):
    for sub in ["config", "prompts", "assets"]:
        src = REPO / sub
        dst = tmp_path / sub
        if src.exists():
            shutil.copytree(src, dst)
    for sub in ["run", "out", "state", "feedback", "data", "inbox"]:
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "llm_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _sent(words: list[str]) -> str:
    return " ".join(words) + "."


def make_passing_script(template="Template_1_In_Medias_Res", hook="Option_2_Out_Of_Context_Confession",
                        payoff=4, cta="cliffhanger_subscription", total_words=200):
    """Build a Tier-1-passing script: simple words (FK<=6.5), contractions, no banned phrases.

    Avoids every uncontracted phrase in config/lexicons/uncontracted.txt.
    """
    from vsp.draft.router import TEMPLATE_BLOCKS
    blocks = TEMPLATE_BLOCKS[template]
    # Beat 1: <=20 words, punchy hook with contraction (Q5-clean: no unknown
    # mid-sentence names, no part/episode digits).
    b1 = "I smiled as docs pulled his plug, though I'd got donor match in my purse"
    # remaining beats: simple short sentences, contractions, no 'it is/that is/there is/...'
    # Q5: only allowlisted/frozen names mid-sentence (Mom/Dad); no part N digits.
    pool = [
        "Look, cops tore ope that pack at lunch. Twas blank. Maid swapped it just in time",
        "Honestly, Mom lost my mind fast. I yelled for guards to search each room",
        "So get this, guards found nought but dust. Trust cash stayed safe with me",
        "The crazy part, Mom fell hard clutching chest. I saw my mom spike my wine",
        "Chill wind blew past morgue doors. I held my strange bag, not Dad",
        "I've kept each scrap. I can't brush me off now. Watch my next move",
        "I'm mad but I've got proof. Don't test me twice. You'll see my truth soon",
        "We've run all night. I've lost my trail. I'll hide till dawn breaks",
    ]
    beats = [b1] + pool[:4]
    # pad/trim to hit total_words incl outro
    outro = "Hit follow for more to learn whose kin lay on that cold slab"
    from vsp.draft.router import count_words
    cur = sum(count_words(b) for b in beats) + count_words(outro)
    pad = ["Folks gasped loud.", "Docs ran in fast.", "Kin wept soft low.", "Phones rang nonstop.",
           "Guards paced halls.", "Rain hit glass hard.", "Dogs barked wild.", "Crowd pressed close."]
    i = 0
    while cur < total_words and i < 200:
        beats[2] += " " + pad[i % len(pad)]
        cur = sum(count_words(b) for b in beats) + count_words(outro)
        i += 1
    scene = []
    for n, (txt, blk) in enumerate(zip(beats[:5], blocks), 1):
        scene.append({"beat_id": n, "block_type": blk, "narration_text": txt,
                      "tts_emotion_prompt": "urgent", "screen_text_overlay": "WOW",
                      "b_roll_vibe": "soap_cutting", "sfx_cue": "whoosh.mp3", "bgm_directive": "tense"})
    return {"script_metadata": {"series_id": "t", "episode_number": 0, "chunk_id": "chunk_001",
                                "pacing_template": template, "hook_option": hook,
                                "hook_payoff_beat_id": payoff, "target_market": "US",
                                "estimated_duration_sec": 65, "total_word_count": cur,
                                "provenance": {"source_url_or_path": "x", "source_sha256": "y"}},
            "scene_beats": scene,
            "call_to_action": {"type": cta, "outro_narration": outro, "loop_connector_word": "Because"}}
