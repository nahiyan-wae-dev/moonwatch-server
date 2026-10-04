#!/usr/bin/env python3
"""Production entry point for moonwatch-server.

monitor.py is retained as a shared compatibility/parser module. This file is the
only production monitor entry point and is the file invoked by GitHub Actions.
"""
import json
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date

import fcm_sender
import monitor as legacy

FULL_FETCH_EVERY = 4
MAX_BODY = 1500


def stable_event_id(source_id, modified, fingerprint):
    return legacy.sha(f"{source_id}\n{modified}\n{fingerprint}")


def metadata_url(url):
    parsed = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != "_fields"]
    query.append(("_fields", "modified_gmt"))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def fetch_modified(src):
    data = json.loads(legacy.http_get(metadata_url(src["url"])).decode("utf-8"))
    if isinstance(data, list):
        if not data:
            raise RuntimeError("page not found (empty result)")
        data = data[0]
    return data["modified_gmt"]


def fetch_full(src):
    return legacy.fetch_wp(src["url"])


def build_event(source_id, title, body, priority, url=None, modified=""):
    body = body[:MAX_BODY]
    return {
        "event_id": stable_event_id(source_id, modified, legacy.sha(body)),
        "source_id": source_id,
        "title": title,
        "body": body,
        "priority": "high" if priority >= 4 else "normal",
        "url": url or "",
        "ts": str(int(time.time() * 1000)),
    }


def send_event(source_id, title, body, priority, tags="moon", url=None, modified=""):
    event = build_event(source_id, title, body, priority, url, modified)
    body = event["body"]
    print(f"[EVENT {event['event_id']}] {title}\n{body}\n")

    fcm_ok = fcm_sender.send(event)
    if fcm_ok:
        print("FCM delivered; ntfy fallback not sent")
    else:
        # FCM is optional. When absent or unavailable, ntfy is the fallback.
        legacy.notify(title, body, priority, tags, url)
    return event


def process_wp(src, state_entry, force_full):
    old = state_entry.get("snap")
    modified = fetch_modified(src)
    if old and old.get("modified") == modified and not force_full:
        print(f"[{src['id']}] metadata unchanged; full fetch skipped")
        return

    modified, raw = fetch_full(src)
    text = legacy.to_text(raw)
    snap = {"modified": modified, "raw_hash": legacy.sha(raw), "text_hash": legacy.sha(text), "text": text}
    parsed = legacy.parse_tracker(raw) if src.get("tracker") else None
    if parsed is not None:
        snap["parsed"] = parsed
    state_entry["snap"] = snap

    if old is None:
        print(f"[{src['id']}] baseline saved")
        return
    if old["raw_hash"] == snap["raw_hash"]:
        print(f"[{src['id']}] no change")
        return

    lines = []
    if parsed is not None:
        changes = legacy.diff_parsed(old.get("parsed") or {}, parsed)
        lines.extend(changes)
        if changes and (not parsed.get("episode") or len(parsed.get("bars", {})) != 3):
            lines.append("Heads-up: page structure changed, the parser may need an update.")
    if old["text_hash"] != snap["text_hash"]:
        diff = legacy.describe_text_diff(old["text"], text)
        if diff:
            lines.extend((["Text changes:"] if lines else []) + diff)

    if lines:
        send_event(src["id"], f"{src['name']} updated", "\n".join(lines), 4 if src.get("tracker") else 3, "moon", src.get("page"), modified)
    elif legacy.PARANOID:
        send_event(src["id"], f"{src['name']} edited (nothing visible)", f"Raw content changed. Modified: {modified} UTC", 2, "moon", src.get("page"), modified)


def process_youtube(src, state_entry):
    root = ET.fromstring(legacy.http_get(src["url"]))
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    videos = []
    for entry in root.findall("a:entry", ns):
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
        if not video_id:
            continue
        link_element = entry.find("a:link", ns)
        link = link_element.get("href") if link_element is not None else f"https://www.youtube.com/watch?v={video_id}"
        videos.append({
            "id": video_id,
            "title": entry.findtext("a:title", default="", namespaces=ns),
            "link": link,
        })
    if not videos:
        raise RuntimeError("no entries in feed")

    current_ids = [video["id"] for video in videos]
    seen = state_entry.get("videos")
    if seen is None:
        print(f"[{src['id']}] baseline saved")
    else:
        for video in [v for v in videos if v["id"] not in seen][:3]:
            send_event(
                src["id"],
                "New Everbyte video",
                video["title"],
                4,
                "movie_camera",
                video["link"],
                video["id"],
            )
    state_entry["videos"] = (current_ids + [i for i in (seen or []) if i not in current_ids])[:60]


def run_source(src, state, force_full):
    entry = state["sources"].setdefault(src["id"], {})
    try:
        if src["kind"] == "wp":
            process_wp(src, entry, force_full)
        else:
            process_youtube(src, entry)
    except Exception as exc:
        entry["fails"] = entry.get("fails", 0) + 1
        print(f"[{src['id']}] FAILED ({entry['fails']}): {type(exc).__name__}: {exc}")
        if entry["fails"] == legacy.FAIL_ALERT_AFTER:
            send_event(src["id"], f"Monitor problem: {src['name']}", f"{legacy.FAIL_ALERT_AFTER} checks in a row failed ({type(exc).__name__}: {exc}).", 3, "warning", None, str(int(time.time())))
        return
    if entry.get("fails", 0) >= legacy.FAIL_ALERT_AFTER:
        send_event(src["id"], f"Recovered: {src['name']}", "Checks are working again.", 3, "white_check_mark", None, str(int(time.time())))
    entry["fails"] = 0


def main():
    if "--test" in sys.argv:
        event = build_event("test", "Moonwatch test", "FCM and ntfy transport test.", 3, None, "test")
        print("Testing FCM transport...")
        fcm_ok = fcm_sender.send(event)
        print(f"FCM test result: {'OK' if fcm_ok else 'NOT CONFIGURED/FAILED'}")
        print("Testing ntfy transport explicitly...")
        legacy.notify(event["title"], event["body"], 3, "bell", None)
        return 0
    if "--show" in sys.argv:
        _, raw = fetch_full(legacy.SOURCES[0])
        print(json.dumps(legacy.parse_tracker(raw), indent=2, ensure_ascii=False))
        return 0

    state = legacy.load_state()
    state.setdefault("sources", {})
    run_count = int(state.get("run_count", 0)) + 1
    state["run_count"] = run_count
    force_full = run_count % FULL_FETCH_EVERY == 0
    print(f"monitor run {run_count}; forced full fetch={force_full}")

    for index, src in enumerate(legacy.SOURCES):
        if index:
            time.sleep(1)
        run_source(src, state, force_full)

    heartbeat = state.get("heartbeat")
    if not heartbeat or (date.today() - date.fromisoformat(heartbeat)).days >= 30:
        state["heartbeat"] = date.today().isoformat()
    legacy.save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
