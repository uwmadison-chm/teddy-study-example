#!/usr/bin/env python3
"""Puts Teddy's runs from a `pig organize` tree in order for analysis.

    python3 prepare.py ORGANIZED PREPARED

For each run under ORGANIZED (anything with a manifest.json), it writes, at the
same path under PREPARED:

- events.csv: one row per event, with the run's manifest fields and link
  parameters on every row, and nested fields flattened into dotted columns
- one playable file per recording, its parts joined and remuxed with ffmpeg

and PREPARED/events.csv, every run's rows in one file.

It never changes ORGANIZED. Running it again rewrites the CSVs and joins only
the recordings that aren't in PREPARED yet (all of them, with --force).

This is a starting point. Copy it into your study and change what you need;
the parts most likely to change are near the top. See Teddy's docs/events.md
for what each event and field means.
"""

import argparse
import csv
import json
import mimetypes
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The event schema major version this script understands. A run whose
# session_started says another major version is skipped; see docs/events.md.
SCHEMA_MAJOR = "1"

# Columns that come first, in this order, whether or not a run has them. Every
# other field follows in the order it was first seen.
RUN_COLUMNS = ["task_code", "run_number", "run_id", "disposition"]
EVENT_COLUMNS = [
    "event_id",
    "type",
    "level",
    "module",
    "recording",
    "item",
    "recording_ms",
    "media_file",
    "media_finished",
    "media_duration_s",
    "_client.wall_time",
    "_client.performance_now",
]

# What a link parameter's column is called when an event field already has
# its name.
PARAMETER_PREFIX = "param."

# File extensions for the content types browsers record in. Anything else goes
# to Python's mimetypes, then .bin.
EXTENSIONS = {
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/mp4": ".mp4",
    "audio/webm": ".weba",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
}


def media_name(event_id, data):
    """A recording's file name, without the extension: rec03-sentence-02 from
    its recording number, kind, and item. The questions module records every
    question at once, so its recordings have no item in the name."""
    if data.get("type") != "recording" or data.get("recording") is None:
        return f"media-{event_id}"
    parts = [f"rec{data['recording']:02d}", data.get("kind")]
    if data.get("item") is not None:
        parts.append(data["item"])
    return "-".join(safe(str(p)) for p in parts if p is not None)


def safe(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)


def extension(content_type):
    base = (content_type or "").split(";")[0].strip().lower()
    return EXTENSIONS.get(base) or mimetypes.guess_extension(base) or ".bin"


class Problems:
    """Everything that went wrong, reported as it happens and counted at the end.
    A note is something worth knowing that nobody needs to fix, like a recording
    cut short by a closed tab; notes don't count."""

    def __init__(self):
        self.count = 0

    def __call__(self, where, message):
        self.count += 1
        print(f"{where}: {message}", file=sys.stderr)

    def note(self, where, message):
        print(f"{where}: {message}", file=sys.stderr)


# ---------------------------------------------------------------- reading runs


def read_run(run_dir, problem):
    """A run's manifest and its events, each event's data flattened, in event ID
    order with repeats dropped. None if the run can't be used."""
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest.get("type") != "pig_run_manifest":
        problem(run_dir, "manifest.json isn't a pig run manifest; skipped")
        return None

    events = {}
    with open(run_dir / "events.jsonl", encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            stored = json.loads(line)
            event_id = str(stored["event_id"])
            if event_id in events:
                # A retry can store the same event twice. They should match.
                if events[event_id] != stored["data"]:
                    problem(run_dir, f"event {event_id} is stored twice with different data (line {number}); kept the first")
                continue
            events[event_id] = stored["data"]

    ordered = sorted(events.items(), key=lambda e: event_order(e[0]))

    for _, data in ordered:
        if data.get("type") == "session_started":
            schema = str(data.get("schema", ""))
            if schema.split(".")[0] != SCHEMA_MAJOR:
                problem(run_dir, f"event schema {schema}, but this script reads {SCHEMA_MAJOR}.x; skipped")
                return None
            break
    else:
        if ordered:
            problem(run_dir, "no session_started event; carrying on without checking the schema")

    rows = []
    for event_id, data in ordered:
        row = {"event_id": event_id}
        try:
            flatten(data, "", row)
        except ValueError as e:
            problem(run_dir, f"event {event_id}: {e}")
        rows.append((event_id, data, row))
    return manifest, rows


def event_order(event_id):
    # pig's client numbers events 0, 1, 2, ... in the order they were made.
    return (0, int(event_id), "") if event_id.isdigit() else (1, 0, event_id)


def flatten(value, prefix, row):
    """Nested objects become dotted columns: {"study": {"hash": ...}} is
    study.hash. Lists stay whole, as JSON, since their length varies."""
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, dict) and item:
            flatten(item, name + ".", row)
            continue
        if name in row:
            raise ValueError(f"two fields flatten to {name}; kept the first")
        row[name] = cell(item)


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (bool, list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


# ---------------------------------------------------------------- recordings


def add_timing(rows):
    """recording_ms: how far into its recording's video each event happened.
    Time 0 is the recording event's _client.performance_now; see "Recordings
    and timing" in docs/events.md."""
    starts = {}
    for _, data, row in rows:
        if data.get("type") == "recording" and data.get("recording") is not None:
            starts[data["recording"]] = data.get("_client", {}).get("performance_now")
    for _, data, row in rows:
        start = starts.get(data.get("recording"))
        now = data.get("_client", {}).get("performance_now")
        if start is not None and now is not None:
            row["recording_ms"] = round(now - start, 3)


def join_media(run_dir, out_dir, out_root, manifest, rows, force, problem):
    """Joins each media item's parts into one file, remuxed so it plays and
    seeks, and notes the file on the item's row."""
    by_id = {event_id: (data, row) for event_id, data, row in rows}
    used = set()
    for item in manifest.get("media", []):
        event_id = str(item["event_id"])
        where = f"{run_dir} media {event_id}"
        if event_id not in by_id:
            problem(where, "no event for this media item; skipped")
            continue
        data, row = by_id[event_id]
        row["media_finished"] = cell(item["finished"])

        parts = sorted((run_dir / "media" / event_id).glob("*.part"), key=lambda p: int(p.stem))
        if not parts:
            problem(where, "no parts; skipped")
            continue
        numbers = [int(p.stem) for p in parts]
        if numbers != list(range(1, len(parts) + 1)) or (item["finished"] and len(parts) != item.get("parts")):
            problem(where, f"has parts {numbers}, but the manifest lists {item.get('parts')}; joined what's there")

        name = media_name(event_id, data)
        if name in used:
            name = f"{name}-{event_id}"
        used.add(name)
        target = out_dir / (name + extension(data.get("content_type")))
        row["media_file"] = target.relative_to(out_root).as_posix()

        if force or not target.exists():
            if not item["finished"]:
                problem.note(where, "the task never finished this item, so it may end early; joined what arrived")
            remux(parts, target, where, problem, item["finished"])
        if target.exists():
            row["media_duration_s"] = duration(target)


def remux(parts, target, where, problem, finished):
    """Joins the parts in order, then copies the streams into a fresh file with
    ffmpeg. A recording sent in parts has no duration or seek index until it's
    remuxed. If ffmpeg can't read the joined file, it's kept, named .joined,
    for a person to look at."""
    joined = target.with_name(target.stem + ".joined" + target.suffix)
    with open(joined, "wb") as out:
        for part in parts:
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out)
    scratch = target.with_name(target.stem + ".partial" + target.suffix)
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", joined, "-c", "copy", scratch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        scratch.unlink(missing_ok=True)
        problem(where, f"ffmpeg couldn't remux it; the joined parts are in {joined.name}\n{result.stderr.strip()}")
        return
    if result.stderr.strip():
        # An unfinished recording is expected to end mid-frame.
        report = problem if finished else problem.note
        report(where, f"ffmpeg reported problems remuxing it\n{result.stderr.strip()}")
    scratch.replace(target)
    joined.unlink()


def duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return ""


# ---------------------------------------------------------------- writing CSVs


def parameter_names(manifest):
    """The run's link parameters, run key first, then the rest the task
    declares, then any extras the link carried."""
    parameters = manifest.get("parameters", {})
    extra = manifest.get("extra_parameters", {}) or {}
    names = list(manifest.get("run_key", []))
    names += [n for n in parameters if n not in names]
    names += [n for n in extra if n not in names]
    return names, {**extra, **parameters}


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("organized", type=Path, help="a pig organize tree, or any directory in one")
    parser.add_argument("prepared", type=Path, help="where to write the CSVs and recordings")
    parser.add_argument("--force", action="store_true", help="join every recording again, even ones already in PREPARED")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} isn't installed, or isn't on the PATH. It's needed to make the recordings playable.")
    organized = args.organized.resolve()
    prepared = args.prepared.resolve()
    if prepared == organized or organized in prepared.parents or prepared in organized.parents:
        sys.exit("PREPARED has to be outside ORGANIZED, and the other way round.")

    problem = Problems()
    runs = []
    for manifest_path in sorted(organized.rglob("manifest.json")):
        run_dir = manifest_path.parent
        read = read_run(run_dir, problem)
        if read is None:
            continue
        manifest, rows = read
        out_dir = prepared / run_dir.relative_to(organized)
        out_dir.mkdir(parents=True, exist_ok=True)
        add_timing(rows)
        join_media(run_dir, out_dir, prepared, manifest, rows, args.force, problem)
        runs.append((out_dir, manifest, rows))

    # Event columns from every run, so every CSV has the same columns.
    event_columns = list(EVENT_COLUMNS)
    for _, _, rows in runs:
        for _, _, row in rows:
            event_columns += [c for c in row if c not in event_columns]
    run_columns = list(RUN_COLUMNS)
    for _, manifest, _ in runs:
        names, _ = parameter_names(manifest)
        run_columns += [n for n in names if n not in run_columns]

    # A parameter named like an event field (a link with ?module=...) gets a
    # prefix. Deciding this across every run keeps the columns the same in all.
    renamed = {n: PARAMETER_PREFIX + n for n in run_columns[len(RUN_COLUMNS):] if n in event_columns or n in RUN_COLUMNS}
    for old, new in renamed.items():
        print(f"The link parameter {old} is also an event field, so its column is {new}.", file=sys.stderr)
    run_columns = RUN_COLUMNS + [renamed.get(n, n) for n in run_columns[len(RUN_COLUMNS):]]
    columns = run_columns + event_columns

    everything = []
    for out_dir, manifest, rows in runs:
        names, values = parameter_names(manifest)
        run = {c: manifest.get(c) for c in RUN_COLUMNS}
        run.update({renamed.get(n, n): values.get(n) for n in names})
        here = out_dir.relative_to(prepared).as_posix() + "/"
        combined, own = [], []
        for _, _, row in rows:
            combined.append({**run, **row})
            local = dict(row)
            if local.get("media_file", "").startswith(here):
                local["media_file"] = local["media_file"][len(here):]
            own.append({**run, **local})
        write_csv(out_dir / "events.csv", columns, own)
        everything += combined
    write_csv(prepared / "events.csv", columns, everything)

    print(f"Prepared {len(runs)} run(s) into {prepared}.")
    if problem.count:
        sys.exit(f"{problem.count} problem(s); see above.")


if __name__ == "__main__":
    main()
