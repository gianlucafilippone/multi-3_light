#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0"
  echo "  This script invokes the /control/execution_stop service"
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

ros2 service call /control/execution_stop std_srvs/srv/Trigger '{}'
