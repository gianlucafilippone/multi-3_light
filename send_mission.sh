#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --mission_name NAME"
  echo "  --mission_name NAME   Mission JSON file name without .json extension"
  exit 1
}

mission_name=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mission_name)
      shift
      mission_name="${1:-}"
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
  shift
done

if [[ -z "$mission_name" ]]; then
  echo "Error: --mission_name is required." >&2
  usage
fi

mission_file="$(pwd)/example_missions/${mission_name}.json"
if [[ ! -f "$mission_file" ]]; then
  echo "Error: mission file not found: $mission_file" >&2
  exit 1
fi

set +u
source /opt/ros/humble/setup.bash
source /root/ros2_ws/install/setup.bash
set -u

ros2 topic pub --once /coordination/receive_mission std_msgs/msg/String "$(jq -Rs '{data: .}' "$mission_file")"
