"""
Orion â€” Scheduler Loader
Handles all scheduler-domain data sources:
  - apple_synthetic_calendar_events.json (246 MB, 500k events) -> streaming + sampling
  - orion_hr_finance_calendar_2026.ics -> full ICS parse

Design Principles:
  - Streaming JSON parse via ijson (never loads full 246MB into RAM)
  - Time-bucketed chunks: group events by calendar week for temporal locality
  - Stratified sample: 50k of 500k events (10%) for manageable DB size
  - ICS parser fully retained from original, hardened for new data
"""

import os
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULER_DIR = os.path.join(PROJECT_ROOT, "data", "scheduler")

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
JSON_SAMPLE_SIZE = 2_000       # Sample size reduced for CPU limits
EVENTS_PER_CHUNK = 20           # Events per embedded text chunk


# â”€â”€ Dependency helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _ensure_ijson():
    """Import ijson; install it if missing."""
    try:
        import ijson
        return ijson
    except ImportError:
        print("  [SchedulerLoader] ijson not found â€” installingâ€¦")
        import subprocess, sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "ijson", "-q"]
        )
        import ijson
        return ijson


# â”€â”€ JSON Calendar (500k events â€” streaming) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _event_to_text(event: dict) -> str:
    """Format a calendar event dict as a readable text line."""
    parts = []
    if event.get("title"):
        parts.append(f"Event: {event['title']}")
    if event.get("organizer"):
        parts.append(f"Organizer: {event['organizer']}")
    if event.get("start_time"):
        parts.append(f"Start: {event['start_time']}")
    if event.get("end_time"):
        parts.append(f"End: {event['end_time']}")
    if event.get("location"):
        parts.append(f"Location: {event['location']}")
    if event.get("description"):
        desc = str(event["description"])[:200]
        parts.append(f"Description: {desc}")
    attendees = event.get("attendees", [])
    if attendees:
        names = ", ".join(str(a) for a in attendees[:5])
        parts.append(f"Attendees: {names}")
    return " | ".join(parts)


def _week_bucket(start_time_str: str) -> str:
    """Return an ISO week key (YYYY-WW) from a datetime string."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(start_time_str[:19], fmt[:len(start_time_str)])
            return dt.strftime("%Y-W%W")
        except Exception:
            continue
    return "unknown_week"


def load_json_calendar_events() -> list[dict]:
    """
    Stream the 500k-event JSON calendar file using ijson.
    Samples JSON_SAMPLE_SIZE events evenly across the timeline.
    Groups sampled events into EVENTS_PER_CHUNK-sized text chunks by week bucket.
    """
    fpath = os.path.join(SCHEDULER_DIR, "apple_synthetic_calendar_events.json")
    if not os.path.exists(fpath):
        print(f"  [SchedulerLoader] JSON calendar not found: {fpath}")
        return []

    ijson = _ensure_ijson()

    print(f"  [SchedulerLoader] Streaming JSON calendar (500k events) â€” target {JSON_SAMPLE_SIZE:,} samplesâ€¦")

    # --- Pass 1: count total events for stride calculation ---
    total_count = 0
    try:
        with open(fpath, "rb") as f:
            for _ in ijson.items(f, "item"):
                total_count += 1
    except Exception as e:
        print(f"  [SchedulerLoader] Error counting JSON events: {e}")
        total_count = 500_000  # fallback estimate

    stride = max(1, total_count // JSON_SAMPLE_SIZE)
    print(f"  [SchedulerLoader] Total events: {total_count:,} | Sampling every {stride} events")

    # --- Pass 2: collect sampled events ---
    sampled: list[dict] = []
    try:
        with open(fpath, "rb") as f:
            for idx, event in enumerate(ijson.items(f, "item")):
                if idx % stride == 0:
                    sampled.append(event)
                if len(sampled) >= JSON_SAMPLE_SIZE:
                    break
    except Exception as e:
        print(f"  [SchedulerLoader] Error reading JSON events: {e}")
        return []

    print(f"  [SchedulerLoader] Sampled {len(sampled):,} events")

    # --- Group into week buckets for temporal locality ---
    week_buckets: dict[str, list[dict]] = defaultdict(list)
    for event in sampled:
        week = _week_bucket(str(event.get("start_time", "")))
        week_buckets[week].append(event)

    # --- Build text chunks from each week bucket ---
    chunks = []
    chunk_id = 0
    for week, events in sorted(week_buckets.items()):
        for i in range(0, len(events), EVENTS_PER_CHUNK):
            batch = events[i:i + EVENTS_PER_CHUNK]
            lines = [_event_to_text(e) for e in batch]
            text = f"Calendar week {week}:\n" + "\n".join(l for l in lines if l.strip())
            if text.strip():
                chunks.append({
                    "id": f"cal_json_week_{week}_chunk_{i // EVENTS_PER_CHUNK}",
                    "text": text,
                    "metadata": {
                        "source": "apple_synthetic_calendar_events.json",
                        "type": "calendar_event",
                        "week": week,
                        "event_count": len(batch),
                        "chunk_id": chunk_id,
                    },
                })
                chunk_id += 1

    print(f"  [SchedulerLoader] JSON calendar -> {len(chunks)} time-bucketed chunks")
    return chunks


# â”€â”€ ICS Calendar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _parse_ics_file(file_path: str) -> list[dict]:
    """Parse an ICS file and return list of event dicts."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"  [SchedulerLoader] Error reading ICS: {e}")
        return []

    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", content, re.DOTALL):
        event: dict = {}

        def _field(tag: str) -> str:
            m = re.search(rf"{tag}[^:]*:(.*?)(?:\r?\n)", block)
            return m.group(1).strip() if m else ""

        summary = _field("SUMMARY")
        dtstart = _field("DTSTART")
        dtend = _field("DTEND")
        location = _field("LOCATION")
        description = _field("DESCRIPTION")

        if summary:
            event["summary"] = summary
        for field_name, raw in [("start", dtstart), ("end", dtend)]:
            if raw:
                for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
                    try:
                        dt = datetime.strptime(raw[:len(fmt.replace("%", "XX"))], fmt)
                        event[field_name] = dt.strftime("%Y-%m-%d %H:%M")
                        if field_name == "start":
                            event["date"] = dt.strftime("%Y-%m-%d")
                        break
                    except Exception:
                        continue
                else:
                    event[field_name] = raw
        if location:
            event["location"] = location
        if description:
            event["description"] = description[:200]
        events.append(event)

    return events


def load_ics_calendar() -> list[dict]:
    """
    Load orion_hr_finance_calendar_2026.ics.
    Full ingestion (it's only 50KB).
    """
    fpath = os.path.join(SCHEDULER_DIR, "orion_hr_finance_calendar_2026.ics")
    if not os.path.exists(fpath):
        print(f"  [SchedulerLoader] ICS file not found: {fpath}")
        return []

    events = _parse_ics_file(fpath)
    if not events:
        return []

    BATCH = 10
    chunks = []
    for i in range(0, len(events), BATCH):
        batch = events[i:i + BATCH]
        lines = []
        for e in batch:
            line = f"Event: {e.get('summary', 'Untitled')}"
            if "start" in e:
                line += f" | Start: {e['start']}"
            if "end" in e:
                line += f" | End: {e['end']}"
            if "location" in e:
                line += f" | Location: {e['location']}"
            if "description" in e:
                line += f" | {e['description']}"
            lines.append(line)

        dates = list(set(e.get("date", "") for e in batch if "date" in e))
        text = "\n".join(l for l in lines if l.strip())
        if text:
            chunks.append({
                "id": f"ics_batch_{i // BATCH}",
                "text": text,
                "metadata": {
                    "source": "orion_hr_finance_calendar_2026.ics",
                    "type": "calendar_ics",
                    "batch": i // BATCH,
                    "dates": ",".join(sorted(dates)),
                },
            })

    print(f"  [SchedulerLoader] ICS calendar -> {len(events)} events, {len(chunks)} chunks")
    return chunks


# â”€â”€ Public Entry Point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_scheduler_data() -> list[dict]:
    """
    Master loader â€” aggregates all scheduler data sources.
    Returns a unified list of {id, text, metadata} dicts.
    """
    print("\n[SchedulerLoader] Starting scheduler data loadâ€¦")
    all_chunks: list[dict] = []

    all_chunks.extend(load_json_calendar_events())
    all_chunks.extend(load_ics_calendar())

    print(f"[SchedulerLoader] Total scheduler chunks: {len(all_chunks)}")
    for c in all_chunks:
        md = c.setdefault("metadata", {})
        md["entity"] = "SYNTH_CAL"
        md["modality"] = "calendar"
    return all_chunks

