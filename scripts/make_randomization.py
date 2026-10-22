#!/usr/bin/env python3
"""Turn a randomization schedule CSV into REDCap randomization tables.

The schedule has one row per participant per phase, with columns like
participant_id, true_order, phase, category_1, video_1, ..., sentence_1_cat,
sentence_1, ..., log_1, log_2.

Writes to the output directory:

  stimulus_categories.csv  type, id, category for every reel and sentence
  <phase>.csv              one per phase (REDCap event), with the columns
                           redcap_randomization_number and
                           redcap_randomization_group, both set to the
                           session's Teddy modules parameter, like
                           questions:1,2,3,4,5|reels:73,65,38|sentence:31,61|videolog:02,04
  allocation_key.csv       which schedule participant each row came from

Row N of every phase file belongs to the same participant, so REDCap hands
out a participant's phases together.

--copies N appends N more copies of the participant list, each shuffled with
participants kept whole, for when the schedule runs out of participants.
--seed makes the shuffle repeatable.

Usage:
  python3 make_randomization.py schedule.csv out/ --copies 2 --seed 1234
"""

import argparse
import csv
import random
import re
import sys
from collections import OrderedDict
from pathlib import Path


def numbered_columns(fieldnames, pattern):
    """Columns matching `pattern` (with one number group), in number order."""
    found = []
    for name in fieldnames:
        m = re.fullmatch(pattern, name)
        if m:
            found.append((int(m.group(1)), name))
    return [name for _, name in sorted(found)]


def read_schedule(path):
    """Returns (fieldnames, {participant_id: [rows in phase order]}, [phases])."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        participants = OrderedDict()
        phases = []
        for row in reader:
            participants.setdefault(row["participant_id"], []).append(row)
            if row["phase"] not in phases:
                phases.append(row["phase"])
    for pid, rows in participants.items():
        rows.sort(key=lambda r: int(r["true_order"]))
        got = [r["phase"] for r in rows]
        if got != phases:
            sys.exit(f"{pid} has phases {got}, expected {phases}")
    return fieldnames, participants, phases


def modules_param(row, questions, reel_cols, sentence_cols, log_cols):
    parts = []
    if questions:
        parts.append("questions:" + ",".join(questions))
    parts.append("reels:" + ",".join(row[c] for c in reel_cols))
    parts.append("sentence:" + ",".join(row[c] for c in sentence_cols))
    parts.append("videolog:" + ",".join(row[c] for c in log_cols))
    return "|".join(parts)


def stimulus_categories(participants, reel_cols, sentence_cols):
    """{(type, id): category}, stopping if an ID shows up with two categories."""
    seen = {}
    for rows in participants.values():
        for row in rows:
            pairs = [("reels", row[c], row[c.replace("video_", "category_")]) for c in reel_cols]
            pairs += [("sentence", row[c], row[c + "_cat"]) for c in sentence_cols]
            for kind, sid, cat in pairs:
                prev = seen.setdefault((kind, sid), cat)
                if prev != cat:
                    sys.exit(f"{kind} {sid} is both {prev!r} and {cat!r}")
    return seen


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("schedule", type=Path, help="randomization schedule CSV")
    p.add_argument("outdir", type=Path, help="directory for the output files")
    p.add_argument("--questions", default="1,2,3,4,5",
                   help="question IDs put first in every session (default: %(default)s; empty for none)")
    p.add_argument("--sentences", type=int, default=2,
                   help="how many of each row's sentences to use (default: %(default)s)")
    p.add_argument("--copies", type=int, default=0,
                   help="shuffled copies of the participant list to append (default: %(default)s)")
    p.add_argument("--seed", type=int, help="seed for the shuffle, to make it repeatable")
    args = p.parse_args()

    fieldnames, participants, phases = read_schedule(args.schedule)
    reel_cols = numbered_columns(fieldnames, r"video_(\d+)")
    sentence_cols = numbered_columns(fieldnames, r"sentence_(\d+)")
    log_cols = numbered_columns(fieldnames, r"log_(\d+)")
    if args.sentences > len(sentence_cols):
        sys.exit(f"--sentences {args.sentences}, but rows only have {len(sentence_cols)}")
    used_sentence_cols = sentence_cols[:args.sentences]
    questions = [q for q in args.questions.split(",") if q]

    # The schedule's participants in order, then each shuffled copy.
    ids = list(participants)
    order = [(0, pid) for pid in ids]
    rng = random.Random(args.seed)
    for copy in range(1, args.copies + 1):
        shuffled = ids[:]
        rng.shuffle(shuffled)
        order += [(copy, pid) for pid in shuffled]

    args.outdir.mkdir(parents=True, exist_ok=True)

    categories = stimulus_categories(participants, reel_cols, sentence_cols)
    with open(args.outdir / "stimulus_categories.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "id", "category"])
        for (kind, sid), cat in sorted(categories.items()):
            w.writerow([kind, sid, cat])

    for i, phase in enumerate(phases):
        with open(args.outdir / f"{phase}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["redcap_randomization_number", "redcap_randomization_group"])
            for _, pid in order:
                param = modules_param(participants[pid][i], questions, reel_cols, used_sentence_cols, log_cols)
                w.writerow([param, param])

    with open(args.outdir / "allocation_key.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row", "copy", "participant_id"])
        for row, (copy, pid) in enumerate(order, start=1):
            w.writerow([row, copy, pid])

    print(f"{len(order)} rows ({len(ids)} participants, {args.copies} shuffled copies) "
          f"for phases {', '.join(phases)} in {args.outdir}")


if __name__ == "__main__":
    main()
