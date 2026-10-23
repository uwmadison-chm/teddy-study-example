#!/usr/bin/env python3
"""Puts Teddy's runs from a `pig organize` tree in order for analysis.

    python3 prepare.py ORGANIZED PREPARED

For each run under ORGANIZED (anything with a manifest.json), it writes, at the
same path under PREPARED:

- events.csv: one row per event, with the run's manifest fields and link
  parameters on every row, and nested fields flattened into dotted columns
- one playable file per recording, its parts joined and remuxed with ffmpeg

and PREPARED/events.csv, every run's rows in one file.

It never changes ORGANIZED. A run is prepared once: running it again prepares
only the runs that don't have an events.csv in PREPARED yet (all of them, with
--force), then, if there were any, rebuilds PREPARED/events.csv from every
run's events.csv.

This is a starting point. Copy it into your study and change what you need;
the parts most likely to change are near the top. See Teddy's docs/events.md
for what each event and field means.
"""

import argparse
import csv
import json
import mimetypes
import os
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
# its name (a link with ?module=...).
PARAMETER_PREFIX = "param."

# Every top-level field Teddy's events can have, from docs/events.md, and the
# columns this script adds. A parameter with one of these names gets the
# prefix. It's a fixed list, rather than the fields seen so far, so that a
# parameter's column doesn't change name as more runs are prepared. Add to it
# when Teddy's event schema gains a field.
EVENT_FIELDS = {
    "event_id", "recording_ms", "media_file", "media_finished", "media_duration_s",
    "type", "level", "module", "recording", "_client",
    "schema", "teddy", "study", "debug", "modules", "link_problems", "next_url",
    "next_url_rejected", "expiration_time", "name", "message", "stack", "result",
    "width", "height", "frame_rate", "constraint", "video_inputs", "audio_inputs",
    "track", "items", "item", "best_score", "centered", "light_ok", "tips_shown",
    "detector_errors", "text", "responses", "choices", "number", "of", "file",
    "value", "label", "kind", "limit_seconds", "content_type", "reason", "line",
    "lines", "animation",
}

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


# The kind of recording each module makes for its items. A recording of another
# kind in that module gets its kind in the name too (reels_01_rating).
MODULE_KINDS = {"sentences": "sentence", "videologs": "videolog", "questions": "questions", "reels": "reel"}


def media_name(event_id, data):
    """A recording's file name, without the extension: the module and item, as
    in sentences_07. The questions module records every question at once, so
    its recording is just questions; calibration's are calibration_face_check
    and calibration_eyes_closed."""
    if data.get("type") != "recording" or data.get("module") is None:
        return f"media_{event_id}"
    module, kind = data["module"], data.get("kind")
    own = MODULE_KINDS.get(module)
    parts = [module]
    if data.get("item") is not None:
        parts.append(data["item"])
    if kind is not None and kind != own:
        parts.append(kind.removeprefix(f"{own}_") if own else kind)
    return "_".join(safe(str(p)) for p in parts)


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


def join_media(run_dir, out_dir, manifest, rows, force, problem):
    """Joins each media item's parts into one file, remuxed so it plays and
    seeks, and notes the file on the item's row."""
    by_id = {event_id: (data, row) for event_id, data, row in rows}
    used = set()
    for item in sorted(manifest.get("media", []), key=lambda m: event_order(str(m["event_id"]))):
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

        # An item recorded again in the same run (after the camera help screen,
        # say) gets _take2, _take3, in the order they were recorded.
        base = name = media_name(event_id, data)
        take = 1
        while name in used:
            take += 1
            name = f"{base}_take{take}"
        used.add(name)
        target = out_dir / (name + extension(data.get("content_type")))
        row["media_file"] = target.name

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


# ---------------------------------------------------------------- finding runs


def find_runs(organized):
    """Every run directory under ORGANIZED, in order. A run's directory is the
    one with a manifest.json, and nothing below it is a run, so the walk never
    goes inside one: it lists directories, not every media part."""
    runs = []
    for directory, subdirectories, files in os.walk(organized):
        if "manifest.json" in files:
            runs.append(Path(directory))
            subdirectories.clear()
        else:
            subdirectories.sort()
    return sorted(runs)


# ---------------------------------------------------------------- writing CSVs


def parameters(manifest):
    """The run's link parameters, run key first, then the rest the task
    declares, then any extras the link carried, each under its column name."""
    declared = manifest.get("parameters", {})
    extra = manifest.get("extra_parameters", {}) or {}
    names = list(manifest.get("run_key", []))
    names += [n for n in declared if n not in names]
    names += [n for n in extra if n not in names]
    values = {**extra, **declared}
    return {column_for(n): values.get(n) for n in names}


def column_for(parameter):
    if parameter in EVENT_FIELDS or parameter in RUN_COLUMNS:
        return PARAMETER_PREFIX + parameter
    return parameter


def write_run_csv(path, manifest, rows, problem):
    """The run's events.csv. It's written last, once its recordings are, and
    appears all at once, since its being there is what says the run is done."""
    run = {c: manifest.get(c) for c in RUN_COLUMNS}
    params = parameters(manifest)
    seen = {c for _, _, row in rows for c in row}
    for column in params:
        if column in seen:
            problem(path.parent, f"the link parameter {column} is also an event field; add it to EVENT_FIELDS so it gets a {PARAMETER_PREFIX} prefix")
    run.update(params)
    columns = list(run) + list(EVENT_COLUMNS)
    for _, _, row in rows:
        columns += [c for c in row if c not in columns]
    write_csv(path, columns, ({**run, **row} for _, _, row in rows))


def write_combined(path, prepared, run_dirs):
    """Every run's events.csv in one file, read back from the runs' own CSVs so
    nothing has to be held in memory. Its columns are every run's columns:
    manifest fields, then every parameter, then every event field, so a column
    one run doesn't have is empty in its rows."""
    csvs = [d / "events.csv" for d in run_dirs if (d / "events.csv").exists()]
    params, fields = [], list(EVENT_COLUMNS)
    for run_csv in csvs:
        with open(run_csv, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
        split = header.index("event_id") if "event_id" in header else len(header)
        params += [c for c in header[len(RUN_COLUMNS):split] if c not in params]
        fields += [c for c in header[split:] if c not in fields]

    def rows():
        for run_csv in csvs:
            here = run_csv.parent.relative_to(prepared).as_posix()
            with open(run_csv, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("media_file"):
                        row["media_file"] = f"{here}/{row['media_file']}"
                    yield row

    write_csv(path, RUN_COLUMNS + params + fields, rows())
    return len(csvs)


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".partial")
    with open(scratch, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    scratch.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("organized", type=Path, help="a pig organize tree, or any directory in one")
    parser.add_argument("prepared", type=Path, help="where to write the CSVs and recordings")
    parser.add_argument("--force", action="store_true", help="prepare every run again, even ones already in PREPARED")
    parser.add_argument("--no-combined", action="store_true", help="don't rebuild PREPARED/events.csv")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} isn't installed, or isn't on the PATH. It's needed to make the recordings playable.")
    organized = args.organized.resolve()
    prepared = args.prepared.resolve()
    if prepared == organized or organized in prepared.parents or prepared in organized.parents:
        sys.exit("PREPARED has to be outside ORGANIZED, and the other way round.")

    problem = Problems()
    run_dirs = find_runs(organized)
    out_dirs = []
    done = 0
    for run_dir in run_dirs:
        out_dir = prepared / run_dir.relative_to(organized)
        out_dirs.append(out_dir)
        if not args.force and (out_dir / "events.csv").exists():
            continue
        read = read_run(run_dir, problem)
        if read is None:
            continue
        manifest, rows = read
        out_dir.mkdir(parents=True, exist_ok=True)
        add_timing(rows)
        join_media(run_dir, out_dir, manifest, rows, args.force, problem)
        write_run_csv(out_dir / "events.csv", manifest, rows, problem)
        done += 1

    print(f"Prepared {done} new run(s) of {len(run_dirs)} into {prepared}.")
    # Rebuilt only when it's missing or a run was added, since at thousands of
    # runs it's the slowest part of a run with nothing new.
    combined = prepared / "events.csv"
    if not args.no_combined and (done or not combined.exists()):
        count = write_combined(combined, prepared, out_dirs)
        print(f"Wrote {combined}, with {count} run(s).")
    if problem.count:
        sys.exit(f"{problem.count} problem(s); see above.")


if __name__ == "__main__":
    main()
