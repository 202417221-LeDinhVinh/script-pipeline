"""Unit: chunker (EN/VI/ZH), firewall, clean-cut; scoring; splice; bible; budget; freshness."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from vsp.chunking.boundary import chunk_source
from vsp.topology.heuristics import word_equiv


def _src(texts, sid="s1"):
    paras = [{"id": i, "text": t} for i, t in enumerate(texts)]
    return {"series_id": sid, "paragraphs": paras, "normalized_text": "\n\n".join(texts)}


def test_100_page_chapter_yields_5k_7k_chunks():
    texts = [f"Para {i} " + "word " * 200 for i in range(150)]  # ~30k words
    chunks = chunk_source(_src(texts), "CONTINUOUS_SERIAL", "en", {"default": 1.0})
    assert len(chunks) >= 4
    for c in chunks[:-1]:
        assert 5000 - 600 <= c["word_equiv"] <= 7000 + 600, c["word_equiv"]


def test_vietnamese_and_chinese_headings_and_factors():
    assert word_equiv("xin chào các bạn hôm nay", "vi", {"vi": 0.7}) < 6
    zh = "第1章 开始了。" + "汉" * 1000
    assert word_equiv(zh, "zh", {"zh": 0.625}) < 700
    texts = ["Chương 1 mở đầu"] + ["nội dung " * 50 for _ in range(5)] + ["Chương 2 tiếp theo"] + ["nội dung " * 50 for _ in range(5)]
    chunks = chunk_source(_src(texts), "ANTHOLOGY", "vi", {"vi": 0.7, "default": 1.0})
    assert len(chunks) == 2
    assert all(c["is_hard_boundary_end"] for c in chunks)


def test_case_boundary_firewall():
    texts = ["Case 1 broken lamp"] + ["clue " * 100 for _ in range(3)] + ["Case 2 loud horn"] + ["clue " * 100 for _ in range(3)]
    chunks = chunk_source(_src(texts), "EPISODIC_SERIES", "en", {"default": 1.0})
    assert len(chunks) == 2
    assert "Case 2" in chunks[1]["raw_text_content"]
    assert "Case 1" not in chunks[1]["raw_text_content"]


def test_clean_cut_open_quote_avoidance():
    from vsp.chunking.boundary import pick_clean_cut, _balanced_quotes, _starts_with_dialogue
    paras = [{"id": i, "text": t} for i, t in enumerate([
        "plain filler words here " * 10,
        'He shouted "run now ' + "word " * 20,  # unbalanced quote: must not cut right after
        '"Yes," she cried. We fled fast down the road.',  # dialogue start: must not cut right before
        "plain filler words here " * 10,
        "plain filler words here " * 10,
    ])]
    # naive target cut (=2) sits exactly after the open quote / before dialogue
    rel, clean = pick_clean_cut(paras, 0, 2, 2, set())
    cut = rel
    assert cut != 2, "cut must avoid stranding an open quote / leading into dialogue"
    assert _balanced_quotes(paras[cut - 1]["text"]), paras[cut - 1]
    assert not _starts_with_dialogue(paras[cut]["text"] if cut < len(paras) else ""), cut
    assert clean is True
    # end-to-end smoke: chunking still yields valid chunks
    texts = ['He said "run now ' + "word " * 30] + ["plain words " * 40 for _ in range(30)] + ['She left town.']
    chunks = chunk_source(_src(texts), "CONTINUOUS_SERIAL", "en", {"default": 1.0},
                          target=200, smin=100, smax=400, window_words=60)
    assert len(chunks) >= 1
    for c in chunks:
        assert c["clean_cut"] in (True, False)


def test_scoring_formula_and_payoff_gate():
    from vsp.gates.tier2.scoring import compute_overall, classify
    ov, _ = compute_overall({"hook_payoff": 9, "pacing": 8, "logic": 7, "retention": 8}, True)
    assert ov == round((18 + 8 + 7 + 8) / 5, 2)
    ov2, adj = compute_overall({"hook_payoff": 10, "pacing": 9, "logic": 9, "retention": 9}, False)
    assert adj["hook_payoff"] == 0.0
    assert classify(8.5, True) == "PASS" and classify(6.0, True) == "REWRITE" and classify(4.0, True) == "REJECT"


def test_splice_byte_identity():
    from vsp.draft.router import splice_script
    base = {"scene_beats": [{"beat_id": i, "narration_text": f"text {i}"} for i in range(1, 6)], "call_to_action": {"type": "loop_back"}}
    rw = [{"beat_id": 2, "narration_text": "fixed two"}]
    out = splice_script(base, rw)
    assert out["scene_beats"][1]["narration_text"] == "fixed two"
    assert out["scene_beats"][0]["narration_text"] == "text 1"


def test_bible_freeze_and_voices():
    from vsp.extract.beats import bible_merge
    voices = {"pools": {"male": {"young": ["m1"], "deep": ["m2"]}, "female": {"young": ["f1"], "mature": ["f2"]}, "neutral": {"narrator": ["n1", "n2"]}}}
    st = {"series_id": "s", "character_registry": [{"original_name": "A", "us_name": "Al", "tts_voice_id": "m1", "main_cast": True}], "localized_glossary": {}}
    st2, warn = bible_merge(st, {"new_entities": [{"original_name": "B", "proposed_us_name": "Bob", "gender": "male"}]}, voices)
    assert any(c["original_name"] == "B" for c in st2["character_registry"])
    assert warn == []
    # R20: re-proposing an existing name keeps the frozen us_name + warning.
    st3, warn = bible_merge(st2, {"new_entities": [{"original_name": "A", "proposed_us_name": "Alex"}]}, voices)
    assert [c["us_name"] for c in st3["character_registry"] if c["original_name"] == "A"] == ["Al"]
    assert warn == [{"original_name": "A", "frozen_us_name": "Al", "proposed_us_name": "Alex"}]
    # R20: two different originals mapping to the same us_name → exit-2 refusal.
    import pytest
    with pytest.raises(SystemExit, match="check=us_name"):
        bible_merge(st2, {"new_entities": [{"original_name": "C", "proposed_us_name": "Bob"}]}, voices)


def test_budget_and_daily_cap(vsp_root):
    from vsp.config import load_config
    from vsp.llm.client import LLMBudget, BudgetExceeded
    cfg = load_config(vsp_root)
    b = LLMBudget(cfg, vsp_root, "s")
    b.precheck("writer", [{"content": "hi"}], 100)
    # §17.6: the locked hard-cost cap wins.
    cfg.lock.setdefault("budget", {})["hard_cost_cap_usd"] = 0.0
    b2 = LLMBudget(cfg, vsp_root, "s")
    try:
        b2.precheck("writer", [{"content": "x" * 5000}], 2000)
        assert False
    except BudgetExceeded:
        pass


def test_freshness_resume(vsp_root):
    from vsp.io import atomic_write_json, is_fresh
    p = vsp_root / "run" / "x.json"
    atomic_write_json(p, {"inputs_sha256": {"a": "1"}, "v": 1})
    import json
    assert is_fresh(p, {"a": "1"}, json.loads(p.read_text())["inputs_sha256"])
    assert not is_fresh(p, {"a": "2"}, json.loads(p.read_text())["inputs_sha256"])
