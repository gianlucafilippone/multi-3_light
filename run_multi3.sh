#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --robots N"
  echo "  --robots N   Number of executor robots to launch"
  exit 1
}

robot_count=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --robots)
      shift
      robot_count="${1:-}"
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
  shift
done

if [[ -z "$robot_count" ]]; then
  echo "Error: --robots is required." >&2
  usage
fi

if ! [[ "$robot_count" =~ ^[0-9]+$ ]] || [[ "$robot_count" -lt 1 ]]; then
  echo "Error: --robots must be a positive integer." >&2
  exit 1
fi

set +u
source /opt/ros/humble/setup.bash
source /root/ros2_ws/install/setup.bash
set -u

pids=()

launch_process() {
  local cmd="${*}"
  echo "Starting: $cmd"
  bash -lc "$cmd" &
  pids+=("$!")
}

cleanup() {
  echo "Shutting down processes..."
  if [[ ${#pids[@]} -gt 0 ]]; then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup SIGINT SIGTERM EXIT

launch_process "ros2 run multi3_coordinator coordinator"

sleep 3

for i in $(seq 1 "$robot_count"); do
  launch_process "ros2 run multi3_executor executor --ros-args -p robot_name:=robot${i}"
  sleep 1
done

wait
