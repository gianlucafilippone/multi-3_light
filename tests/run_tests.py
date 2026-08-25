#!/usr/bin/env python3
"""Run a suite of integration tests defined in a spec file.

Each line in the spec file describes a test in the form:
  <N> robot, missions <m1>-<m2>-..., <strategy>

Examples:
  1 robot, missions 1-2, baseline
  2 robot, missions 1-2-3, baseline

For each test the script will:
- start the coordinator with parameter `assignment_strategy` set to <strategy>
- start N executor nodes (robot1..robotN)
- publish each mission JSON in order on `/coordination/new_missions`
- call `/control/execution_start`
- poll `/info/fragment_pool` until all fragments are completed
- collect logs and save a combined log named `run_m{missions}_r{robots}_{strategy}.log`

Logs and per-process outputs are stored under `logs/<timestamp>/`.
"""
import argparse
import os
import re
import shutil
import subprocess
import time
import signal
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import rclpy

from poll_fragment_pool import get_fragment_pool, all_completed


def parse_spec_line(line: str) -> Tuple[int, List[int], str]:
    parts = [p.strip() for p in line.split(',') if p.strip()]
    if len(parts) < 3:
        raise ValueError(f'Invalid spec line: {line}')
    # first part: number of robots
    m = re.search(r'(\d+)', parts[0])
    if not m:
        raise ValueError(f'Cannot parse robot count: {parts[0]}')
    robots = int(m.group(1))
    # second part: missions numbers
    nums = re.findall(r'(\d+)', parts[1])
    if not nums:
        raise ValueError(f'Cannot parse missions: {parts[1]}')
    missions = [int(n) for n in nums]
    # third part: strategy (take first token)
    strategy = parts[2]
    return robots, missions, strategy


def build_run_cmd(ros_setup: str, ws_setup: str, package: str, exe: str, extra: str = '') -> str:
    return f"bash -lc 'source {ros_setup} >/dev/null 2>&1 || true; source {ws_setup} >/dev/null 2>&1 || true; exec ros2 run {package} {exe} {extra}'"


def start_process(cmd: str, log_path: Path) -> subprocess.Popen:
    f = open(log_path, 'wb')
    p = subprocess.Popen(
        cmd,
        shell=True,
        stdout=f,
        stderr=subprocess.STDOUT,
        executable='/bin/bash',
        start_new_session=True,
    )
    return p


def terminate_process_tree(proc: subprocess.Popen, timeout: float = 5.0):
    if proc is None or proc.poll() is not None:
        return

    pgid = os.getpgid(proc.pid)

    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def shutdown_all_ros_processes():
    for proc_name in ['coordinator', 'executor']:
        try:
            subprocess.run(['pkill', '-f', f'ros2 run .*{proc_name}'], check=False)
        except Exception:
            pass
    try:
        subprocess.run(['pkill', '-f', 'multi3_coordinator|multi3_executor'], check=False, shell=True)
    except Exception:
        pass
    time.sleep(1.0)


def clear_test_log_dir(test_dir: Path):
    if not test_dir.exists():
        return
    try:
        shutil.rmtree(test_dir)
    except Exception:
        for child in test_dir.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except Exception:
                pass


def publish_mission_text(node, mission_text: str) -> None:
    from std_msgs.msg import String
    pub = node.create_publisher(String, '/coordination/new_missions', 10)
    msg = String()
    msg.data = mission_text
    time.sleep(0.5)
    pub.publish(msg)
    time.sleep(0.3)


def call_inventory_check(node) -> str:
    from std_srvs.srv import Trigger
    client = node.create_client(Trigger, '/info/inventory_check')
    if not client.wait_for_service(timeout_sec=5.0):
        return ''
    req = Trigger.Request()
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
    if future.done() and future.result() is not None:
        return str(future.result().message).strip()
    return ''


def call_start_service(node) -> bool:
    from std_srvs.srv import Trigger
    client = node.create_client(Trigger, '/control/execution_start')
    if not client.wait_for_service(timeout_sec=5.0):
        return False
    req = Trigger.Request()
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
    if future.done() and future.result() is not None:
        return future.result().success
    return False


def summarize_fragment_pool(pool) -> Tuple[int, int, int]:
    if not isinstance(pool, dict):
        return 0, 0, 0

    completed = 0
    assigned_or_running = 0
    remaining = 0
    for value in pool.values():
        if not isinstance(value, dict):
            continue
        state = value.get('state')
        if state == 'completed':
            completed += 1
        elif state in {'assigned', 'executable', 'running', 'executing'}:
            assigned_or_running += 1
        else:
            remaining += 1
    return completed, assigned_or_running, remaining


def format_missions_for_name(missions: List[int]) -> str:
    return '-'.join(str(m) for m in missions)


def combine_logs(output_path: Path, perproc_dir: Path, header: str):
    with output_path.open('wb') as out:
        out.write(f"=== Test: {header}\n\n".encode())
        for pfile in sorted(perproc_dir.iterdir()):
            out.write(f"\n--- {pfile.name} ---\n".encode())
            with pfile.open('rb') as f:
                out.write(f.read())


def run_test(ros_setup: str, ws_setup: str, robots: int, missions: List[int], strategy: str, mission_dir: Path, log_root: Path, timeout: int) -> int:
    max_attempts = 10
    for attempt in range(1, max_attempts + 1):
        test_name = f"m{format_missions_for_name(missions)}_r{robots}_{strategy}"
        test_dir = log_root / test_name
        test_dir.mkdir(parents=True, exist_ok=True)

        # Start coordinator with assignment_strategy param
        coord_log = test_dir / 'coordinator.log'
        coord_extra = f"--ros-args -p assignment_strategy:={strategy}"
        coord_cmd = build_run_cmd(ros_setup, ws_setup, 'multi3_coordinator', 'coordinator', coord_extra)
        coord_proc = start_process(coord_cmd, coord_log)
        time.sleep(3)

        # Start executors
        exec_procs = []
        for i in range(1, robots + 1):
            name = f'robot{i}'
            exec_log = test_dir / f'executor_{name}.log'
            extra = f"--ros-args -p robot_name:={name}"
            cmd = build_run_cmd(ros_setup, ws_setup, 'multi3_executor', 'executor', extra)
            p = start_process(cmd, exec_log)
            exec_procs.append(p)
            time.sleep(2)

        # rclpy client to publish missions and start
        rclpy.init()
        node = rclpy.create_node('test_suite_client')
        result_code = 0
        restart_test = False
        try:
            # publish each mission in order
            for m in missions:
                fname = mission_dir / f'mission_{m}.json'
                if not fname.exists():
                    print('Mission file missing:', fname)
                    result_code = 4
                    break
                text = fname.read_text()
                publish_mission_text(node, text)
                print('Published mission', fname.name)

                inventory_status = call_inventory_check(node)
                print('Inventory check after publishing', fname.name, '->', inventory_status)
                if inventory_status.lower() == 'misaligned':
                    print(f'Inventory misaligned during attempt {attempt}; restarting test from scratch.')
                    restart_test = True
                    break

            if result_code != 0:
                pass
            elif restart_test:
                pass
            else:
                # call start
                started = call_start_service(node)
                print('Start service result:', started)

                # poll for completion
                start = time.time()
                while True:
                    pool = get_fragment_pool(node, timeout=2.0)
                    if pool is not None:
                        completed, assigned_or_running, remaining = summarize_fragment_pool(pool)
                        print(
                            f'[poll] completed={completed}, assigned/running={assigned_or_running}, remaining={remaining}, total={completed + assigned_or_running + remaining}'
                        )
                        if all_completed(pool):
                            print('All fragments completed for', test_name)
                            result_code = 0
                            break
                    if time.time() - start > timeout:
                        print('Timeout waiting for fragments for', test_name)
                        result_code = 3
                        break
                    time.sleep(2.0)
        finally:
            try:
                node.destroy_node()
            except Exception:
                pass
            rclpy.shutdown()

            # Teardown processes
            terminate_process_tree(coord_proc)
            for p in exec_procs:
                terminate_process_tree(p)

        time.sleep(2.0)

        if restart_test:
            print(f'Restarting failed test attempt {attempt}/{max_attempts}.')
            clear_test_log_dir(test_dir)
            if attempt < max_attempts:
                continue
            skipped_file = log_root / 'max_attempts_exceeded.txt'
            skipped_file.parent.mkdir(parents=True, exist_ok=True)
            with skipped_file.open('a', encoding='utf-8') as f:
                f.write(f"{test_name} | robots={robots}, missions={missions}, strategy={strategy}\n")
            return 6

        # Create combined log file
        combined_name = f'run_{format_missions_for_name(missions)}_r{robots}_{strategy}.log'
        combined_path = log_root / combined_name
        combine_logs(combined_path, test_dir, f'spec: robots={robots}, missions={missions}, strategy={strategy}')
        print('Saved combined log to', combined_path)

        return result_code

    return 6


def main():
    p = argparse.ArgumentParser()
    p.add_argument('spec_file', help='Path to test specs file')
    p.add_argument('--ros_setup', default='/opt/ros/humble/setup.bash')
    p.add_argument('--ws_setup', default='/root/ros2_ws/install/setup.bash')
    p.add_argument('--mission_dir', default='example_missions')
    p.add_argument('--log_root', default='logs')
    p.add_argument('--timeout', type=int, default=300)
    args = p.parse_args()

    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        print('Spec file not found:', spec_path)
        return 2

    mission_dir = Path(args.mission_dir)
    log_root = Path(args.log_root)

    with spec_path.open() as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    overall_ok = True
    for idx, line in enumerate(lines, start=1):
        try:
            robots, missions, strategy = parse_spec_line(line)
        except Exception as e:
            print('Skipping invalid spec line', idx, line, 'reason:', e)
            overall_ok = False
            continue

        print(f'Running test {idx}: robots={robots}, missions={missions}, strategy={strategy}')
        rc = run_test(args.ros_setup, args.ws_setup, robots, missions, strategy, mission_dir, log_root, args.timeout)
        if rc != 0:
            print('Test', idx, 'failed with code', rc)
            overall_ok = False
        else:
            print('Test', idx, 'succeeded')

        # small delay between tests
        time.sleep(1.0)

    return 0 if overall_ok else 5


if __name__ == '__main__':
    raise SystemExit(main())
