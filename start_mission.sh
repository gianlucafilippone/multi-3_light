#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0"
  echo "  This script publishes an empty ROS2 message to /coordination/execution_start"
  exit 1
}

if [[ $# -ne 0 ]]; then
  echo "Unknown arguments: $*" >&2
  usage
fi

set +u
source /opt/ros/humble/setup.bash
source /root/ros2_ws/install/setup.bash
set -u

ros2 topic pub --once /coordination/execution_start std_msgs/msg/Empty '{}'
