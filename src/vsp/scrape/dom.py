"""Tiny DOM + selector subset for the scraper (stdlib only, no new deps).

Supported selectors: comma groups; descendant combinators; simple parts of the
form `tag`, `.class`, `#id`, `tag.class`, `tag#id`, `tag.class1.class2`, plus an
optional `[attr="value"]` suffix. Good enough for site adapters; anything fancier
belongs in adapter code, not here.
"""
from __future__ import annotations
from html.parser import HTMLParser


VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "col", "base",
        "embed", "track", "area", "param"}


class Node:
    __slots__ = ("tag", "attrs", "children", "text", "parent", "ordered")

    def detach(self) -> None:
        """Remove this node from its parent (both children list and the
        ordered segment list, so later token walks skip detached subtrees)."""
        parent = self.parent
        if parent is None:
            return
        try:
            parent.children.remove(self)
        except ValueError:
            pass
        parent.ordered = [(k, v) for k, v in parent.ordered
                          if not (k == "n" and v is self)]

    def __init__(self, tag: str, attrs: dict | None = None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.text = ""
        self.parent = parent
        # ("t", str) text chunks and ("n", Node) children in document order.
        # .text stays as the concatenated aggregate for backward compatibility.
        self.ordered: list = []

    def classes(self) -> list[str]:
        return str(self.attrs.get("class", "")).split()

    def __repr__(self) -> str:  # pragma: no cover
        return f"Node({self.tag!r} {self.attrs!r} kids={len(self.children)})"


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = Node("document")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        self.cur.ordered.append(("n", node))
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        self.cur.ordered.append(("n", node))

    def handle_endtag(self, tag):
        tag = tag.lower()
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if data:
            self.cur.text += data
            self.cur.ordered.append(("t", data))

    def handle_comment(self, data):
        return


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    return builder.root


def _parse_simple(part: str) -> dict:
    """Parse one compound selector part into {tag, id, classes, attr}."""
    attr = None
    if "[" in part and part.endswith("]"):
        part, cond = part.split("[", 1)
        cond = cond[:-1].strip()
        if "*=" in cond:
            k, v = cond.split("*=", 1)
            attr = (k.strip(), v.strip().strip("\"'"), "contains")
        elif "=" in cond:
            k, v = cond.split("=", 1)
            attr = (k.strip(), v.strip().strip("\"'"), "eq")
        else:
            attr = (cond, None, "exists")
    tag, rest, ident, classes = "", part, "", []
    i = 0
    while i < len(rest) and rest[i] not in ("#", "."):
        i += 1
    tag = rest[:i]
    rest = rest[i:]
    while rest:
        if rest[0] == "#":
            j = 1
            while j < len(rest) and rest[j] not in ("#", "."):
                j += 1
            ident = rest[1:j]
            rest = rest[j:]
        elif rest[0] == ".":
            j = 1
            while j < len(rest) and rest[j] not in ("#", "."):
                j += 1
            classes.append(rest[1:j])
            rest = rest[j:]
        else:
            break
    return {"tag": tag or None, "id": ident or None, "classes": classes, "attr": attr}


def _matches(node: Node, simple: dict) -> bool:
    if simple["tag"] and node.tag != simple["tag"]:
        return False
    if simple["id"] and node.attrs.get("id") != simple["id"]:
        return False
    have = node.classes()
    for c in simple["classes"]:
        if c not in have:
            return False
    if simple["attr"]:
        if len(simple["attr"]) == 3:
            k, v, op = simple["attr"]
        else:
            k, v, op = simple["attr"][0], simple["attr"][1], "eq"
        if k not in node.attrs:
            return False
        if op == "exists":
            pass
        elif op == "contains":
            if v not in str(node.attrs.get(k)):
                return False
        elif v is not None and str(node.attrs.get(k)) != v:
            return False
    return True


def _descendants(node: Node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def select(root: Node, selector: str) -> list[Node]:
    """Return nodes matching a selector group, in document order, deduplicated."""
    out: list[Node] = []
    seen: set[int] = set()
    for group in selector.split(","):
        parts = [_parse_simple(p.strip()) for p in group.strip().split() if p.strip()]
        if not parts:
            continue
        cands = [root]
        for simple in parts:
            nxt: list[Node] = []
            for cand in cands:
                for d in _descendants(cand):
                    if _matches(d, simple):
                        nxt.append(d)
            cands = nxt
        for n in cands:
            if id(n) not in seen:
                seen.add(id(n))
                out.append(n)
    return out


def select_one(root: Node, selector: str) -> Node | None:
    found = select(root, selector)
    return found[0] if found else None


def inner_text(node: Node) -> str:
    """Concatenated text of a subtree in document order (entities left raw;
    caller unescapes)."""
    parts = []
    for kind, val in node.ordered:
        if kind == "t":
            parts.append(val)
        else:
            if val.tag == "br":
                parts.append("\n")
            else:
                parts.append(inner_text(val))
                if val.tag in ("p", "div", "h1", "h2", "h3", "h4", "li", "tr",
                               "article", "section", "blockquote"):
                    parts.append("\n")
    return "".join(parts)
