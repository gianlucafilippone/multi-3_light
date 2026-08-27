#!/usr/bin/env python3

import csv
import re
from collections import defaultdict, deque
from pathlib import Path

from datetime import datetime, timezone


LOG_DIR = Path("../execution_logs")
RESULTS_DIR = Path("../results")


CSV_COLUMNS = [
    "event_id",
    "event_type",
    "activity",
    "start_time",
    "end_time",
    "robot_id",
    "mission_id",
    "segment_id",
]


LOG_PATTERN = re.compile(
    r"EventType:\s*(?P<event_type>[^,(]+?)"
    r"(?:\s*\(Task:\s*[^)]+\))?"
    r"\s*,\s*"
    r"Activity:\s*(?P<activity>[^,]*)"
    r"\s*,\s*"
    r"Robot:\s*(?P<robot_id>[^,]*)"
    r"\s*,\s*"
    r"Mission:\s*(?P<mission_id>[^,]*)"
    r"\s*,\s*"
    r"Segment:\s*(?P<segment_id>[^,]*)"
    r"\s*,\s*"
    r"(?P<time_type>Start|End):\s*(?P<time>[0-9.eE+-]+)"
)


def parse_line(line):
    match = LOG_PATTERN.search(line)

    if not match:
        return None

    data = match.groupdict()

    for key, value in data.items():
        if value is not None:
            data[key] = value.strip()

    return data


def format_timestamp(timestamp_str):
    timestamp = float(timestamp_str)
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")


def convert_log(log_path, csv_path):

    pending_events = defaultdict(deque)

    occurrence_count = defaultdict(int)

    completed_events = []

    start_counter = 0

    with log_path.open("r", encoding="utf-8", errors="replace") as log_file:

        for line_number, line in enumerate(log_file, start=1):

            data = parse_line(line)

            if data is None:
                continue

            key = (
                data["event_type"],
                data["activity"],
                data["robot_id"],
                data["mission_id"],
                data["segment_id"],
            )

            if data["time_type"] == "Start":

                occurrence_count[key] += 1

                # if occurrence_count[key] > 1:
                #     print(
                #         f"[WARNING] {log_path.name}:{line_number}: "
                #         "Duplicated event combination: "
                #         f"EventType={data['event_type']}, "
                #         f"Activity={data['activity']}, "
                #         f"Robot={data['robot_id']}, "
                #         f"Mission={data['mission_id']}, "
                #         f"Segment={data['segment_id'] or '<vuoto>'}. "
                #         f"Occorrenza #{occurrence_count[key]}"
                #     )

                event = {
                    "event_type": data["event_type"],
                    "activity": data["activity"],
                    "start_time": format_timestamp(data["time"]),
                    "end_time": "",
                    "robot_id": data["robot_id"],
                    "mission_id": data["mission_id"],
                    "segment_id": data["segment_id"],
                    "_order": start_counter,
                    "_start_line": line_number,
                }

                start_counter += 1

                pending_events[key].append(event)

            elif data["time_type"] == "End":

                if not pending_events[key]:
                    print(
                        f"[WARNING] {log_path.name}:{line_number}: "
                        "End event without Start corrispondence: "
                        f"EventType={data['event_type']}, "
                        f"Activity={data['activity']}, "
                        f"Robot={data['robot_id']}, "
                        f"Mission={data['mission_id']}, "
                        f"Segment={data['segment_id'] or '<vuoto>'}"
                    )
                    continue

                event = pending_events[key].popleft()

                event["end_time"] = format_timestamp(data["time"])

                completed_events.append(event)

    for queue in pending_events.values():
        for event in queue:
            print(
                f"[WARNING] {log_path.name}:{event['_start_line']}: "
                "Start event without End corrispondence: "
                f"EventType={event['event_type']}, "
                f"Activity={event['activity']}, "
                f"Robot={event['robot_id']}, "
                f"Mission={event['mission_id']}, "
                f"Segment={event['segment_id'] or '<vuoto>'}"
            )

    completed_events.sort(key=lambda event: event["_order"])

    for i, event in enumerate(completed_events, start=1):
        event["event_id"] = f"e{i}"

    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=CSV_COLUMNS,
        )

        writer.writeheader()

        for event in completed_events:
            writer.writerow({
                column: event[column]
                for column in CSV_COLUMNS
            })

    print(
        f"[OK] {log_path.name} -> {csv_path.name} "
        f"({len(completed_events)} events)"
    )


def main():

    if not LOG_DIR.exists():
        raise FileNotFoundError(
            f"Log directory does not exist: {LOG_DIR.resolve()}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    log_files = sorted(
        path
        for path in LOG_DIR.glob("*.log")
        if path.is_file()
    )

    if not log_files:
        print(f"No .log files in {LOG_DIR.resolve()}")
        return

    print(f"{len(log_files)} .log files found")

    for log_path in log_files:

        csv_path = RESULTS_DIR / f"{log_path.stem}.csv"

        convert_log(log_path, csv_path)


if __name__ == "__main__":
    main()