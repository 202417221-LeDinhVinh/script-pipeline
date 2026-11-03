"""Round 15 M1+M2: metruyenchu adapter + pagination (SPEC §18.2).

No network (httpx.MockTransport). No story text typed: synthetic fixtures use
obviously fake filler with SYNTHETIC markers; real fixtures are the 2 pages
captured by the scraper itself with PROVENANCE.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from vsp.scrape.adapters import metruyenchu as mtc
from vsp.scrape import series as series_mod

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "tests" / "scrape" / "fixtures" / "real"


def _cfg():
    import yaml
    return yaml.safe_load((REPO / "config" / "scrape" / "metruyenchu.yaml").read_text(encoding="utf-8"))


def _syn_series(pages: dict) -> str:
    # synthetic series page with href pagination (M2 proof)
    items = "".join(
        '<li><a href="/dao-quy-di-tien/chuong-%d-Tok%d">Chuong %d: Fake Title %d</a></li>' % (n, n, n, n)
        for n in pages["nums"])
    nxt = '<a class="next-page" href="%s">Next</a>' % pages["next"] if pages.get("next") else ""
    return "<!-- SYNTHETIC metruyenchu list --><html><head><title>Dao Quy Di Tien</title></head><body>" \
        "<h1>Dao Quy Di Tien</h1><div id='chapter-list'><ul>%s</ul></div>%s</body></html>" % (items, nxt)


def test_m1_adapter_selected_by_host():
    from vsp.scrape import runner
    assert runner.select_adapter("https://metruyenchu.com.vn/dao-quy-di-tien", {}, False) == "metruyenchu"


def test_m1_real_chapter_strips_ps_footer():
    cfg = _cfg()
    html = (REAL / "mtc_chuong-001.html").read_bytes().decode("utf-8", errors="replace")
    ext = mtc.extract_chapter(html, cfg)
    assert len(ext["paragraphs"]) >= 10
    assert sum(len(p) for p in ext["paragraphs"]) >= 1500
    text = "\n".join(ext["paragraphs"])
    assert "_____" not in text
    assert "PS:" not in text
    # stripped footer recorded once
    assert any("_____" in s or "PS:" in s for s in ext["stripped_lines"])
    # nav removed, title present
    assert "Tải Ebook" not in text
    assert ext["title"] != ""


def test_m1_real_series_title_and_chapter_1_link():
    cfg = _cfg()
    html = (REAL / "mtc_series.html").read_bytes().decode("utf-8", errors="replace")
    page = series_mod.parse_series_page(html, "https://metruyenchu.com.vn/dao-quy-di-tien", cfg)
    assert page["title"] != ""
    nums = [c["number"] for c in page["chapters"]]
    assert 1 in nums
    urls = [c["url"] for c in page["chapters"] if c["number"] == 1]
    assert urls and "Odu5R7udF0MJ" in urls[0]


def test_m1_real_provenance_sha():
    import json, hashlib
    for fname in ["mtc_series.html", "mtc_chuong-001.html"]:
        prov = json.loads((REAL / (fname + ".PROVENANCE.json")).read_text(encoding="utf-8"))
        raw = (REAL / fname).read_bytes()
        assert prov["sha256"] == hashlib.sha256(raw).hexdigest()


def test_m2_pagination_stops_once_n_known():
    import httpx
    from vsp.scrape import series as sm
    from vsp.scrape.http import PoliteSession
    p1 = _syn_series({"nums": list(range(1, 6)), "next": "/s?page=2"})
    p2 = _syn_series({"nums": list(range(6, 11))})
    seen_urls = []

    def handler(request):
        url = str(request.url)
        seen_urls.append(url)
        if "page=2" in url:
            return httpx.Response(200, text=p2)
        return httpx.Response(200, text=p1)

    cfg = _cfg()
    sess = PoliteSession(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)
    coll = sm.collect_chapters(sess, "https://metruyenchu.com.vn/dao-quy-di-tien", 7, cfg)
    assert [c["number"] for c in coll["chapters"]] == [1, 2, 3, 4, 5, 6, 7]
    # stopped after 2 list pages (did not fetch page 3)
    assert len(seen_urls) == 2, seen_urls


def test_m2_output_chunks_into_chapters():
    # metruyenchu inbox format uses Chapter N headings the chunker detects
    from vsp.topology.heuristics import find_headings
    paras = [{"id": 0, "text": "Dao Quy Di Tien"}, {"id": 1, "text": "Chapter 1: Fake Start"},
             {"id": 2, "text": "Doan thu nghiem so 1 voi noi dung gia."},
             {"id": 3, "text": "Chapter 2: Fake Next"},
             {"id": 4, "text": "Doan thu nghiem so 2 voi noi dung gia."}]
    heads = find_headings(paras)
    kinds = [h.get("kind") for h in heads]
    assert len(heads) >= 2
