"""Scraper proof tests (SPEC §15.5). Offline: httpx.MockTransport only, no network."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from vsp.scrape import series as series_mod
from vsp.scrape.http import PoliteSession, ScrapeError
from vsp.scrape import runner as R
from vsp.scrape import store as store_mod
from vsp.scrape import gates as gates_mod
from vsp.scrape.adapters import truyenfull as tf

REPO = Path(__file__).resolve().parents[2]
SYN = REPO / "tests" / "scrape" / "fixtures" / "synthetic"
SERIES = "https://truyenfull.live/truyen-thu-nghiem/"
SERIES_P2 = "https://truyenfull.live/truyen-thu-nghiem/trang-2/"


def _cfg():
    return yaml.safe_load((REPO / "config" / "scrape" / "truyenfull.yaml").read_text(encoding="utf-8"))


def _syn(name: str) -> str:
    return (SYN / name).read_text(encoding="utf-8")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _sess(handler, delay=0.0, sleeps=None):
    rec = [] if sleeps is None else sleeps
    return PoliteSession(_client(handler), delay=delay, sleep_fn=rec.append), rec


def _chapter_html(num: int, paras: int = 12, title: str = "") -> str:
    t = title or f"Chương {num}: Đoạn {num} Giả Lập"
    filler = ("Câu giả lập số %d của chương %d với đủ chữ để mỗi đoạn văn "
              "đều vượt qua ngưỡng ký tự tối thiểu cho cổng kiểm tra.%s")
    ps = "".join("<p>%s</p>" % (filler % (i, num, " Thêm chữ giả lập cho đủ dài." * 3))
                 for i in range(paras))
    return (f'<html><head><title>Truyện Thử Nghiệm Tổng Hợp - {t} | TruyenFull.vn</title></head><body>'
            f'<h1 class="chapter-title"><a title="Truyện Thử Nghiệm Tổng Hợp - {t}">{num}</a></h1>'
            f'<div id="chapter-c">{ps}</div></body></html>')


def test_robots_refusal():
    def handler(request):
        return httpx.Response(200, text=_syn("SYNTHETIC_robots_disallow.txt"))

    sess, _ = _sess(handler)
    with pytest.raises(SystemExit, match="robots"):
        R.scrape_series(Path("/nonexistent"), SERIES, _cfg(), max_chapters=1, session=sess)


def test_robots_allows_series_and_chapter_paths():
    from vsp.scrape.http import check_robots

    def handler(request):
        return httpx.Response(200, text=_syn("SYNTHETIC_robots_allow.txt"))

    sha = check_robots(_client(handler), SERIES + "chuong-1/")
    assert isinstance(sha, str) and len(sha) == 64


def test_pagination_two_list_pages_in_order():
    p1 = _syn("SYNTHETIC_series_page1.html")
    p2 = _syn("SYNTHETIC_series_page2.html")
    seen: list = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=p1 if str(request.url) == SERIES else p2)

    sess, _ = _sess(handler)
    coll = series_mod.collect_chapters(sess, SERIES, 5, _cfg())
    assert coll["title"] == "Truyện Thử Nghiệm Tổng Hợp"
    assert coll["author"] == "Tác Giả Giả Lập"
    assert [c["number"] for c in coll["chapters"]] == [1, 2, 3, 4, 5]
    assert coll["chapters"][0]["url"].endswith("/chuong-1/")
    assert seen == [SERIES, SERIES_P2]


def test_rate_limit_delay_fake_clock():
    seen: list = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    sess, sleeps = _sess(handler, delay=1.5)
    for u in ("https://x.test/a", "https://x.test/b", "https://x.test/c"):
        assert sess.get(u).status_code == 200
    assert sleeps == [1.5, 1.5] and len(seen) == 3


def test_retry_503_then_success_and_no_retry_on_404():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text="ok")

    sess, sleeps = _sess(flaky, delay=0.5)
    assert sess.get("https://x.test/r").text == "ok"
    assert calls["n"] == 3 and sleeps == [0.5, 1.0]

    n404 = {"n": 0}

    def gone(request):
        n404["n"] += 1
        return httpx.Response(404, text="gone")

    sess2, _ = _sess(gone, delay=0.5)
    with pytest.raises(ScrapeError, match="404"):
        sess2.get("https://x.test/missing")
    assert n404["n"] == 1


def test_clean_chapter_extraction():
    ext = tf.extract_chapter(_syn("SYNTHETIC_chapter_clean.html"), _cfg())
    assert len(ext["paragraphs"]) >= 10
    assert all(len(p) >= 20 for p in ext["paragraphs"])
    blob = "\n".join(ext["paragraphs"])
    assert "<div" not in blob and "&nbsp;" not in blob
    assert ext["title"].startswith("Truyện Thử Nghiệm Tổng Hợp - Chương 1")
    assert ext["lock_hit"] is False


def test_lock_and_watermark_removal_with_stripped_lines():
    cfg = _cfg()
    cfg["chapter"]["lock_phrases"] = ["Nội dung chương đang bị khóa"]
    ext = tf.extract_chapter(_syn("SYNTHETIC_chapter_lock.html"), cfg)
    assert ext["lock_hit"] is True
    assert any("bị khóa" in s for s in ext["stripped_lines"])
    assert not any("Nội dung chương đang bị khóa" in p for p in ext["paragraphs"])
    cfg["chapter"]["watermark_lines"] = ["truyen full gia lap watermark"]
    ext2 = tf.extract_chapter(_syn("SYNTHETIC_chapter_watermark.html"), cfg)
    assert any("watermark" in s.lower() or "truyenfull" in s.lower()
               for s in ext2["stripped_lines"])
    assert not any(p.strip().lower() == "truyen full gia lap watermark"
                   for p in ext2["paragraphs"])


def test_gates_short_duplicate_leftover_tag_entity():
    cfg = _cfg()
    mk = lambda n, t, ps: {"number": n, "title": t, "paragraphs": ps, "text": "\n\n".join(ps)}
    longp = ["xđoạn giả lập %d đủ dài cho kiểm tra vượt ngưỡng ký tự tối thiểu. " % i * 2 for i in range(12)]
    good = [mk(1, "A", longp), mk(2, "B", [p.replace("xđoạn", "yđoạn") for p in longp])]
    assert gates_mod.check_chapters(good, 1, 2, False, cfg) == []
    assert any("too short" in e for e in gates_mod.check_chapters([mk(1, "A", ["ngắn"])], 1, 1, True, cfg))
    dup = [mk(1, "A", good[0]["paragraphs"]), mk(2, "B", good[0]["paragraphs"])]
    assert any("duplicates chapter" in e for e in gates_mod.check_chapters(dup, 1, 2, False, cfg))
    tagged = [mk(1, "A", good[0]["paragraphs"] + ["<div>tag sót</div>"])]
    assert any("leftover HTML tag" in e for e in gates_mod.check_chapters(tagged, 1, 1, True, cfg))
    ent = [mk(1, "A", good[0]["paragraphs"] + ["dấu &nbsp; còn sót"])]
    assert any("leftover entity" in e for e in gates_mod.check_chapters(ent, 1, 1, True, cfg))
    locked = [mk(1, "A", good[0]["paragraphs"] + ["Nội dung chương đang bị khóa"])]
    assert any("leftover marker" in e for e in gates_mod.check_chapters(locked, 1, 1, True, cfg))


def test_resume_skips_fresh_chapters(tmp_path):
    from vsp.scrape import runner as R
    from vsp.scrape.http import PoliteSession
    import httpx
    cfg = dict(_cfg())
    p1 = _syn("SYNTHETIC_series_page1.html")
    seen = []

    ch1 = _chapter_html(1, paras=14)

    def handler(request):
        seen.append(str(request.url))
        if "/chuong-" in str(request.url):
            return httpx.Response(200, text=ch1)
        return httpx.Response(200, text=p1)

    def make_sess():
        return PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0.0)

    m1 = R.scrape_series(tmp_path, SERIES, cfg, max_chapters=1, session=make_sess())
    first_calls = list(seen)
    assert m1["gates_passed"] is True
    m2 = R.scrape_series(tmp_path, SERIES, cfg, max_chapters=1, session=make_sess())
    assert m2["inbox_sha256"] == m1["inbox_sha256"]
    new_calls = seen[len(first_calls):]
    assert not any("/chuong-" in u for u in new_calls), new_calls


def test_verify_passes_then_fails_on_changed_bytes(tmp_path):
    from vsp.scrape import runner as R
    from vsp.scrape.store import verify_slug, read_manifest
    import httpx
    cfg = dict(_cfg())
    mapping = {SERIES: (200, _syn("SYNTHETIC_series_page1.html")),
               "https://truyenfull.live/robots.txt": (200, _syn("SYNTHETIC_robots_allow.txt"))}
    for n in (1, 2):
        mapping[f"https://truyenfull.live/truyen-thu-nghiem/chuong-{n}/"] = (200, _chapter_html(n, paras=14))

    def handler(request):
        item = mapping.get(str(request.url))
        if item is None:
            return httpx.Response(404, text=b"nope")
        status, body = item
        return httpx.Response(status, content=body.encode("utf-8"))

    from vsp.scrape.http import PoliteSession
    sess = PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0.0)
    manifest = R.scrape_series(tmp_path, SERIES, cfg, max_chapters=2, session=sess)
    slug = manifest["slug"]
    assert verify_slug(tmp_path, slug, cfg, "truyenfull") == []
    html_p = tmp_path / "data" / "scrape" / slug / "chapters" / "chuong-001.html"
    raw = bytearray(html_p.read_bytes())
    raw[200] = raw[200] ^ 0x01
    html_p.write_bytes(bytes(raw))
    assert verify_slug(tmp_path, slug, cfg, "truyenfull") != []
    html_p.write_bytes(_chapter_html(1, paras=14).encode("utf-8"))
    assert verify_slug(tmp_path, slug, cfg, "truyenfull") == []
    inbox = tmp_path / manifest["inbox_file"]
    data = bytearray(inbox.read_bytes())
    data[50] = data[50] ^ 0x01
    inbox.write_bytes(bytes(data))
    assert verify_slug(tmp_path, slug, cfg, "truyenfull") != []


def test_output_chunks_into_chapters_with_existing_chunker(vsp_root):
    from vsp.orchestrator import do_ingest, do_chunk
    text = ("Truyện Thử Nghiệm Tổng Hợp\n\n" + "\n\n".join(
        "Chương %d: Tiêu Đề Giả %d\n\n%s" % (n, n, "câu giả lập. " * 1500) for n in (1, 2, 3)))
    f = vsp_root / "scraped.txt"
    f.write_text(text, encoding="utf-8")
    payload = do_ingest(vsp_root, str(f), topology="CONTINUOUS_SERIAL")
    chunks = do_chunk(vsp_root, payload["series_id"])
    assert len(chunks) >= 2
    from vsp.topology.heuristics import find_headings
    from vsp.io import read_json as _r
    paras = _r(vsp_root / "run" / payload["series_id"] / "00_source_raw.json")["paragraphs"]
    heads = find_headings(paras)
    assert [h["para_id"] for h in heads] == [p["id"] for p in paras if p["text"].startswith("Chương ")]


def test_scrape_cli_exit_codes(vsp_root, monkeypatch):
    from vsp.cli import build_parser
    import vsp.scrape.runner as R
    import vsp.scrape.http as H
    import httpx
    cfg = dict(_cfg())
    monkeypatch.setattr(R, "load_scrape_config", lambda root, site="truyenfull": cfg)
    html = _syn("SYNTHETIC_chapter_clean.html")

    def handler(request):
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text=_syn("SYNTHETIC_robots_allow.txt"))
        if "chuong-" in url:
            return httpx.Response(200, text=html)
        if "truyen-thu-nghiem" in url:
            return httpx.Response(200, text=_syn("SYNTHETIC_series_page1.html"))
        return httpx.Response(404, text="nope")

    real_build = H.build_client
    monkeypatch.setattr(H, "build_client", lambda transport=None: real_build(transport=httpx.MockTransport(handler)))
    ap = build_parser()
    args = ap.parse_args(["--root", str(vsp_root), "scrape", "https://truyenfull.live/truyen-thu-nghiem/", "--max-chapters", "1"])
    assert args.fn(args) == 0
    args2 = ap.parse_args(["--root", str(vsp_root), "scrape", "verify", "no-such-slug"])
    assert args2.fn(args2) == 2


def test_no_llm_imports_in_scrape_package():
    import re
    pkg = REPO / "src" / "vsp" / "scrape"
    import_pat = re.compile(r"^\s*(from|import)\b.*\bllm\b", re.IGNORECASE | re.MULTILINE)
    for path in list(pkg.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "FakeLLM" not in text, "fake-client reference in %s" % path
        assert import_pat.search(text) is None, "LLM import in %s" % path
        assert "opencode_backend" not in text
        assert "from .llm" not in text and "from vsp.llm" not in text
        assert "api_key" not in text.lower()


def _robots_loop_handler(calls, *, busted_body=None, loop_busted=False):
    """MockTransport handler: /robots.txt raises a self-redirect loop;
    /robots.txt?_vsp=... returns 200 (or loops if loop_busted)."""
    import httpx

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if url.split("?")[0].endswith("/robots.txt"):
            if "_vsp=" in url:
                if loop_busted:
                    raise httpx.TooManyRedirects("redirect loop (busted)", request=request)
                return httpx.Response(200, text=busted_body)
            raise httpx.TooManyRedirects("self-redirect loop on robots.txt", request=request)
        return httpx.Response(404, text=b"nope")

    return handler


def test_robots_redirect_loop_cache_busted(tmp_path):
    """R6: self-redirecting robots.txt is retried once with ?_vsp=; the
    scrape proceeds and the manifest records cache-busted + final URL."""
    import httpx
    from vsp.scrape import runner as R
    from vsp.scrape.http import PoliteSession
    cfg = dict(_cfg())
    allow = _syn("SYNTHETIC_robots_allow.txt")
    mapping = {SERIES: (200, _syn("SYNTHETIC_series_page1.html"))}
    for n in (1,):
        mapping[f"https://truyenfull.live/truyen-thu-nghiem/chuong-{n}/"] = (200, _chapter_html(n, paras=14))

    def handler(request):
        url = str(request.url)
        if url.split("?")[0].endswith("/robots.txt"):
            if "_vsp=" in url:
                return httpx.Response(200, text=allow)
            raise httpx.TooManyRedirects("self-redirect loop on robots.txt", request=request)
        item = mapping.get(url)
        if item is None:
            return httpx.Response(404, text=b"nope")
        status, body = item
        return httpx.Response(status, content=body.encode("utf-8"))

    sess = PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0.0)
    manifest = R.scrape_series(tmp_path, SERIES, cfg, max_chapters=1, session=sess)
    assert manifest["gates_passed"] is True
    assert manifest["robots_fetch"] == "cache-busted"
    assert "_vsp=" in manifest["robots_url"]
    assert manifest["robots_txt_sha256"] is not None
    import hashlib
    assert manifest["robots_txt_sha256"] == hashlib.sha256(allow.encode("utf-8")).hexdigest()


def test_robots_redirect_loop_both_fail_refuses_exit2(tmp_path):
    """R6: redirect loop on plain and cache-busted robots.txt -> REFUSAL
    (exit 2, check=robots), never exit 1, never allow-all."""
    import httpx
    from vsp.scrape import runner as R
    from vsp.scrape.http import PoliteSession
    cfg = dict(_cfg())

    def handler(request):
        if "robots.txt" in str(request.url):
            raise httpx.TooManyRedirects("redirect loop", request=request)
        return httpx.Response(200, text=_syn("SYNTHETIC_series_page1.html"))

    sess = PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0.0)
    with pytest.raises(SystemExit) as exc:
        R.scrape_series(tmp_path, SERIES, cfg, max_chapters=1, session=sess)
    assert "check=robots" in str(exc.value) and "REFUSAL" in str(exc.value)


def test_series_page_redirect_loop_clear_error(tmp_path):
    """R6: a looping series/chapter page raises ScrapeError with a clear
    message (exit 1 upstream), not a raw traceback."""
    import httpx
    from vsp.scrape import runner as R
    from vsp.scrape.http import PoliteSession, ScrapeError
    cfg = dict(_cfg())

    def handler(request):
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(200, text=_syn("SYNTHETIC_robots_allow.txt"))
        raise httpx.TooManyRedirects("self-redirect loop on series page", request=request)

    sess = PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0.0)
    with pytest.raises(ScrapeError, match="redirect loop"):
        R.scrape_series(tmp_path, SERIES, cfg, max_chapters=1, session=sess)


def test_r7_real_hidden_watermark_removed_and_recorded():
    """R7: real captured chapter — hidden watermark lines absent from text,
    present in stripped_lines."""
    from vsp.scrape.adapters import truyenfull as tf
    real = REPO / "tests" / "scrape" / "fixtures" / "real" / "chuong-001.html"
    ext = tf.extract_chapter(real.read_text(encoding="utf-8"), _cfg())
    assert any("truyenfull" in s.lower() for s in ext["stripped_lines"])
    blob = "\n".join(ext["paragraphs"])
    assert "truyenfullvn" not in blob.replace(" ", "")
    assert "truyenfull,truyenfull" not in blob.replace(" ", "")


def test_r7_surviving_stripped_line_fails_gate():
    """R7: a stripped line that survives into the text -> gate failure."""
    from vsp.scrape import gates as G
    cfg = _cfg()
    mk = lambda n, t, ps: {"number": n, "title": t, "paragraphs": ps,
                           "text": "\n\n".join(ps),
                           "stripped_lines": ["truyen full, truyenfull"]}
    good = [mk(1, "A", [(("xđoạn giả lập %d đủ dài cho kiểm tra vượt ngưỡng ký tự tối thiểu cho cổng này. Thêm chữ. " % i) * 3) for i in range(12)])]
    assert G.check_chapters(good, 1, 1, True, cfg) == []
    bad = [dict(good[0], text=good[0]["text"] + "\n\n" + "truyen full, truyenfull")]
    assert any("leaked into final text" in e for e in G.check_chapters(bad, 1, 1, True, cfg))


def test_r8_editor_beta_credits_dropped_mid_sentence_kept():
    """R8: whole `Editor:`/`Beta:` lines dropped+recorded; a story line with
    mid-sentence 'editor' survives."""
    from vsp.scrape.adapters import truyenfull as tf
    cfg = _cfg()
    paras = [
        "Người biên tập viên trong truyện nhắc tới editor ở giữa câu dài này.",
        "Editor: Min Adelia",
        "Beta: Min",
        "Đoạn giả lập đủ dài để vượt qua mọi ngưỡng kiểm tra còn lại.",
    ]
    html = ("<html><head><title>T</title></head><body>"
            '<div id="chapter-c">' + "".join("<p>%s</p>" % x for x in paras) + "</div></body></html>")
    ext = tf.extract_chapter(html, cfg)
    kept = " ".join(ext["paragraphs"])
    assert "editor ở giữa câu" in kept
    assert not any("Editor:" in pr or pr.startswith("Beta:") for pr in ext["paragraphs"])
    assert any("Editor: Min Adelia" in s for s in ext["stripped_lines"])
    assert any("Beta: Min" in s for s in ext["stripped_lines"])


def test_r9_locked_chapter_message_names_check_and_retry():
    """R9: too-short chapter matching a locked_at_source phrase reports
    check=locked_at_source chapter=N with --max-chapters N-1 (exit 2)."""
    from vsp.scrape import gates as G
    cfg = _cfg()
    short = "Chương này sẽ được đặt pass vào ngày mai."
    ch = {"number": 15, "title": "X", "paragraphs": [short], "text": short}
    errs = G.check_chapters([ch], 15, 15, False, cfg)
    assert any("locked_at_source" in e for e in errs), errs
    assert any("chapter=15" in e and "--max-chapters 14" in e for e in errs), errs
