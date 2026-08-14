#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 NAME"
  echo "NAME:  Mission JSON file name without .json extension"
  exit 1
}

if [[ $# -lt 1 ]]; then
  echo "Error: mission name is required." >&2
  usage
fi

mission_file="$(pwd)/example_missions/$1.json"
if [[ ! -f "$mission_file" ]]; then
  echo "Error: mission file not found: $mission_file" >&2
  exit 1
fi

set +u
source /opt/ros/humble/setup.bash
source /root/ros2_ws/install/setup.bash
set -u

ros2 topic pub --once /coordination/new_missions std_msgs/msg/String "$(jq -Rs '{data: .}' "$mission_file")"
