#!/usr/bin/env python3
"""Moonwatch monitor: watches Everbyte pages/feeds and pushes alerts via ntfy.

Standard library only. Built to run from GitHub Actions every ~15 minutes.
Its memory lives in state.json, which the workflow commits back to the repo.

  python3 monitor.py          # normal run
  python3 monitor.py --test   # send a test notification
  python3 monitor.py --show   # print what the parser sees on the tracker page

Environment:
  NTFY_TOPIC        required for push alerts (keep it secret and hard to guess)
  PARANOID          "1" (default) = also alert on invisible/builder-only edits
  FAIL_ALERT_AFTER  consecutive failures before a "monitor problem" alert (default 3)
"""
import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

BASE = "https://everbytestudio.com"
WP = BASE + "/wp-json/wp/v2/pages"
FIELDS = "_fields=modified_gmt,content"

SOURCES = [
    {
        "id": "tracker",
        "name": "Moonvale Episode Tracker",
        "kind": "wp",
        "tracker": True,
        "url": f"{WP}/33571?{FIELDS}",
        "page": f"{BASE}/moonvale-episode-tracker/",
    },
    {
        "id": "story-hub",
        "name": "Detective Story Hub",
        "kind": "wp",
        "url": f"{WP}?slug=moonvale-detective-story&{FIELDS}",
        "page": f"{BASE}/moonvale-detective-story/",
    },
    {
        "id": "duskwood-side",
        "name": "Duskwood Side Story",
        "kind": "wp",
        "url": f"{WP}?slug=moonvale-duskwood-sidestory&{FIELDS}",
        "page": f"{BASE}/moonvale-duskwood-sidestory/",
    },
    {
        "id": "support-faq",
        "name": "Support and FAQ",
        "kind": "wp",
        "url": f"{WP}?slug=moonvale-support-faq&{FIELDS}",
        "page": f"{BASE}/moonvale-support-faq/",
    },
    {
        "id": "youtube",
        "name": "Everbyte YouTube",
        "kind": "youtube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCoJTRMVHvIiJJicMA9fTNGg",
    },
]

STATE_FILE = os.environ.get(
    "STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
PARANOID = os.environ.get("PARANOID", "1") != "0"
FAIL_ALERT_AFTER = int(os.environ.get("FAIL_ALERT_AFTER", "3"))
UA = "moonwatch-monitor/2.0 (personal use; polite polling)"


# ---------------------------------------------------------------- networking
def http_get(url, tries=2, timeout=20):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json, application/xml, */*"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # retry once, then give up
            last = e
            if attempt + 1 < tries:
                time.sleep(2)
    raise last


def fetch_wp(url):
    data = json.loads(http_get(url).decode("utf-8"))
    if isinstance(data, list):  # ?slug= queries return a list
        if not data:
            raise RuntimeError("page not found (empty result)")
        data = data[0]
    return data["modified_gmt"], data["content"]["rendered"]


def notify(title, body, priority=3, tags="moon", click=None):
    print(f"[NOTIFY p{priority}] {title}\n{body}\n")
    if not NTFY_TOPIC:
        return
    headers = {
        "Title": title.encode("ascii", "replace").decode(),  # headers must be ASCII
        "Priority": str(priority),
        "Tags": tags,
        "User-Agent": UA,
    }
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body[:3500].encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # never crash the run because a push failed
        print("ntfy failed:", e)


# ------------------------------------------------------------------- parsing
def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def to_text(raw):
    """Divi shortcodes + HTML -> clean paragraphs (one per line)."""
    t = html.unescape(raw)
    t = re.sub(r"\[/?et_pb_[^\]]*\]", " ", t)  # builder shortcodes
    t = re.sub(r"(?i)<br\s*/?>|</(p|h[1-6]|li|div|ul|ol)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def parse_tracker(raw):
    """Episode line, progress bars and feature titles from the tracker page."""
    text = html.unescape(raw)
    bars = {}
    for pct, title in re.findall(
        r"\[et_pb_counter\s+percent=\W*(\d+)[^\]]*\]([^\[]+?)\[/et_pb_counter\]", text
    ):
        bars[title.strip()] = int(pct)
    m = re.search(r"<strong>\s*(Episode[^<]+?)\s*</strong>", text)
    features = [
        f.strip()
        for f in re.findall(r"(?:UPCOMING FEATURE|INSIGHT)</strong>.*?<h2>(.*?)</h2>", text, flags=re.S)
    ]
    return {"episode": m.group(1) if m else None, "bars": bars, "features": features}


def diff_parsed(old, new):
    out = []
    if old.get("episode") != new.get("episode"):
        out.append(f"Episode line: {old.get('episode')} → {new.get('episode')}")
    for k in sorted(set(old.get("bars", {})) | set(new.get("bars", {}))):
        a, b = old.get("bars", {}).get(k), new.get("bars", {}).get(k)
        if a != b:
            out.append(f"{k}: {a}% → {b}%" if a is not None and b is not None else f"{k}: {a} → {b}")
    added = [f for f in new.get("features", []) if f not in old.get("features", [])]
    removed = [f for f in old.get("features", []) if f not in new.get("features", [])]
    if added:
        out.append("New feature section: " + ", ".join(added))
    if removed:
        out.append("Removed feature section: " + ", ".join(removed))
    return out


# ------------------------------------------------------------- text diffing
def clip(s, n=110):
    return s if len(s) <= n else s[: n - 1] + "…"


def word_diff(a, b):
    aw, bw = a.split(), b.split()
    sm = difflib.SequenceMatcher(None, aw, bw, autojunk=False)
    parts = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old, new = " ".join(aw[i1:i2]), " ".join(bw[j1:j2])
        if old and new:
            parts.append(f'"{clip(old, 40)}" → "{clip(new, 40)}"')
        elif new:
            parts.append(f'+ "{clip(new, 40)}"')
        else:
            parts.append(f'- "{clip(old, 40)}"')
        if len(parts) >= 3:
            break
    return "; ".join(parts) or "(whitespace change)"


def describe_text_diff(old, new, max_items=6):
    a = [u for u in old.split("\n") if u.strip()]
    b = [u for u in new.split("\n") if u.strip()]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            out += ["+ " + clip(u) for u in b[j1:j2]]
        elif tag == "delete":
            out += ["- " + clip(u) for u in a[i1:i2]]
        else:
            out.append("~ " + word_diff(" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    extra = len(out) - max_items
    out = out[:max_items]
    if extra > 0:
        out.append(f"… and {extra} more")
    return out


# ------------------------------------------------------------ source checks
def process_wp(src, s):
    modified, raw = fetch_wp(src["url"])
    text = to_text(raw)
    snap = {"modified": modified, "raw_hash": sha(raw), "text_hash": sha(text), "text": text}
    parsed = None
    if src.get("tracker"):
        parsed = parse_tracker(raw)
        snap["parsed"] = parsed
    old = s.get("snap")
    s["snap"] = snap

    if old is None:
        print(f"[{src['id']}] baseline saved")
        return
    if old["raw_hash"] == snap["raw_hash"]:
        print(f"[{src['id']}] no change")
        return

    lines, priority = [], 3
    if parsed is not None:
        changes = diff_parsed(old.get("parsed") or {}, parsed)
        if changes:
            lines += changes
            if not parsed["episode"] or len(parsed["bars"]) != 3:
                lines.append("Heads-up: page structure changed, the parser may need an update.")
    if old["text_hash"] != snap["text_hash"]:
        td = describe_text_diff(old["text"], text)
        if td:
            lines += (["Text changes:"] if lines else []) + td
    if lines:
        priority = 4 if src.get("tracker") else 3
        title = f"{src['name']} updated"
    else:
        if not PARANOID:
            print(f"[{src['id']}] invisible change ignored (PARANOID off)")
            return
        priority = 2
        title = f"{src['name']} edited (nothing visible)"
        lines.append(f"Raw content changed (builder, layout or hidden). Modified: {modified} UTC")
    notify(title, "\n".join(lines), priority, "moon", src.get("page"))


def process_youtube(src, s):
    root = ET.fromstring(http_get(src["url"]))
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    vids = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", default="", namespaces=ns)
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else f"https://www.youtube.com/watch?v={vid}"
        if vid:
            vids.append({"id": vid, "title": e.findtext("a:title", default="", namespaces=ns), "link": link})
    if not vids:
        raise RuntimeError("no entries in feed")
    ids = [v["id"] for v in vids]
    seen = s.get("videos")
    if seen is None:
        print(f"[{src['id']}] baseline saved")
    else:
        for v in [v for v in vids if v["id"] not in seen][:3]:
            notify("New Everbyte video", v["title"], 4, "movie_camera", v["link"])
    s["videos"] = (ids + [i for i in (seen or []) if i not in ids])[:60]


def run_source(src, state):
    s = state["sources"].setdefault(src["id"], {})
    try:
        (process_wp if src["kind"] == "wp" else process_youtube)(src, s)
    except Exception as e:
        s["fails"] = s.get("fails", 0) + 1
        print(f"[{src['id']}] FAILED ({s['fails']}): {type(e).__name__}: {e}")
        if s["fails"] == FAIL_ALERT_AFTER:
            notify(
                f"Monitor problem: {src['name']}",
                f"{FAIL_ALERT_AFTER} checks in a row failed ({type(e).__name__}: {e}).",
                3,
                "warning",
            )
        return
    if s.get("fails", 0) >= FAIL_ALERT_AFTER:
        notify(f"Recovered: {src['name']}", "Checks are working again.", 3, "white_check_mark")
    s["fails"] = 0


# -------------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sources": {}}
    except json.JSONDecodeError:
        print("state file unreadable; starting from a fresh baseline")
        return {"sources": {}}


def save_state(state):
    new = json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            if f.read() == new:
                return
    except FileNotFoundError:
        pass
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(new)


def main():
    if "--test" in sys.argv:
        notify("Moonwatch test", "ntfy is working. Alerts will look like this.", 3)
        return
    if "--show" in sys.argv:
        _, raw = fetch_wp(SOURCES[0]["url"])
        print(json.dumps(parse_tracker(raw), indent=2, ensure_ascii=False))
        return

    state = load_state()
    state.setdefault("sources", {})
    for i, src in enumerate(SOURCES):
        if i:
            time.sleep(1)  # be polite between requests
        run_source(src, state)

    # monthly heartbeat so the repo never looks inactive (scheduled workflows can be paused)
    hb = state.get("heartbeat")
    if not hb or (date.today() - date.fromisoformat(hb)).days >= 30:
        state["heartbeat"] = date.today().isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
