#!/usr/bin/env python3

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
    # Deprecated for new spec format. Keep fallback parsing for single-line specs.
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


def mission_file_path(mission_dir: Path, mission_name: str) -> Path:
    filename = mission_name if mission_name.endswith('.json') else f'{mission_name}.json'
    return mission_dir / filename


def format_capabilities_param(capabilities: List[str]) -> str:
    # ROS 2 parses parameter values as YAML. The capability names used by the
    # test specs are simple identifiers, so a YAML flow sequence is sufficient.
    return '[' + ','.join(capabilities) + ']'


def run_test(
    ros_setup: str,
    ws_setup: str,
    robot_specs: List[Tuple[int, List[str]]],
    mission_arrivals: List[Tuple[int, List[str]]],
    strategy: str,
    mission_dir: Path,
    log_root: Path,
    timeout: int,
    test_number: int,
    test_total: int,
    test_name: str,
) -> int:
    max_attempts = 10
    # total_robots = sum(count for count, _ in robot_specs)

    # The parser maps `init:` to delay 0. Everything at t=0 is therefore
    # published before execution_start; positive delays are relative to start.
    arrivals = sorted(mission_arrivals, key=lambda item: item[0])
    init_batches = [missions for delay, missions in arrivals if delay == 0]
    timed_batches = [(delay, missions) for delay, missions in arrivals if delay > 0]

    # test_name = f'r{total_robots}_{strategy}'

    for attempt in range(1, max_attempts + 1):
        test_dir = log_root / test_name
        test_dir.mkdir(parents=True, exist_ok=True)

        coord_proc = None
        exec_procs = []
        node = None
        result_code = 0
        restart_test = False

        try:
            # Start coordinator with assignment_strategy param.
            coord_log = test_dir / 'coordinator.log'
            coord_extra = f'--ros-args -p assignment_strategy:={strategy}'
            coord_cmd = build_run_cmd(
                ros_setup, ws_setup, 'multi3_coordinator', 'coordinator', coord_extra
            )
            coord_proc = start_process(coord_cmd, coord_log)
            time.sleep(3)

            # Start executors. robotN is global/incremental across all robot specs.
            robot_index = 1
            for count, capabilities in robot_specs:
                capabilities_param = format_capabilities_param(capabilities)
                for _ in range(count):
                    name = f'robot{robot_index}'
                    exec_log = test_dir / f'executor_{name}.log'
                    extra = (
                        f'--ros-args '
                        f'-p robot_name:={name} '
                        f'-p capabilities:=\"{capabilities_param}\"'
                    )
                    cmd = build_run_cmd(
                        ros_setup, ws_setup, 'multi3_executor', 'executor', extra
                    )
                    p = start_process(cmd, exec_log)
                    exec_procs.append(p)
                    print(f'Started {name} with capabilities={capabilities}')
                    robot_index += 1
                    time.sleep(2)

            # rclpy client used to publish missions, trigger start, and poll status.
            rclpy.init()
            node = rclpy.create_node('test_suite_client')

            def publish_one_mission(mission_name: str) -> bool:
                nonlocal result_code, restart_test

                fname = mission_file_path(mission_dir, mission_name)
                if not fname.exists():
                    print('Mission file missing:', fname)
                    result_code = 4
                    return False

                text = fname.read_text(encoding='utf-8')
                publish_mission_text(node, text)
                print('Published mission', fname.name)
                time.sleep(1)
                return True

            # 1) Publish all init missions BEFORE signaling execution_start.
            for batch in init_batches:
                for mission_name in batch:
                    if not publish_one_mission(mission_name):
                        break
                if result_code != 0 or restart_test:
                    break

            time.sleep(6)
            inventory_status = call_inventory_check(node)
            print(
                'Inventory check after publishing',
                '->',
                inventory_status,
            )
            if inventory_status.lower() == 'misaligned':
                print(
                    f'Inventory misaligned during attempt {attempt}; '
                    'restarting test from scratch.'
                )
                restart_test = True

            if result_code == 0 and not restart_test:
                # 2) Signal start only after every init mission was published.
                started = call_start_service(node)
                print('Start service result:', started)
                if not started:
                    result_code = 5
                else:
                    start_time = time.monotonic()
                    next_batch = 0

                    # 3) Send positive-delay arrivals relative to start_time.
                    # 4) Accept completion only after every scheduled batch was sent.
                    while True:
                        elapsed = time.monotonic() - start_time

                        # Publish every batch that is due. Recompute elapsed after each
                        # batch so closely spaced arrivals are not unnecessarily delayed.
                        while next_batch < len(timed_batches):
                            delay, batch = timed_batches[next_batch]
                            elapsed = time.monotonic() - start_time
                            if elapsed < delay:
                                break

                            print(
                                f'[arrival] t={elapsed:.1f}s: publishing batch '
                                f'scheduled at {delay}s: {batch}'
                            )
                            batch_ok = True
                            for mission_name in batch:
                                if not publish_one_mission(mission_name):
                                    batch_ok = False
                                    break

                            if not batch_ok:
                                break

                            next_batch += 1

                        if result_code != 0 or restart_test:
                            break

                        all_missions_sent = next_batch == len(timed_batches)

                        # Keep polling responsive enough to honor scheduled arrivals.
                        pool = get_fragment_pool(node, timeout=min(5, timeout))
                        if pool is not None:
                            completed, assigned_or_running, remaining = summarize_fragment_pool(pool)
                            print(
                                f'[poll] [test {test_number} of {test_total}] '
                                f'completed={completed}, '
                                f'assigned/running={assigned_or_running}, '
                                f'remaining={remaining}, '
                                f'total={completed + assigned_or_running + remaining}, '
                                f'all_missions_sent={all_missions_sent}'
                            )

                            if all_missions_sent and all_completed(pool):
                                print('All missions sent and all fragments completed for', test_name)
                                result_code = 0
                                break

                            if not all_missions_sent and all_completed(pool):
                                next_delay = timed_batches[next_batch][0]
                                print(
                                    'Current fragments are complete, but future missions '
                                    f'are still pending (next arrival at {next_delay}s).'
                                )

                        elapsed = time.monotonic() - start_time
                        if elapsed > timeout:
                            print(
                                'Timeout waiting for fragments for',
                                test_name,
                                f'(all_missions_sent={all_missions_sent})',
                            )
                            result_code = 3
                            break

                        # Sleep at most 2 seconds, but wake up near the next arrival.
                        sleep_for = 2.0
                        if next_batch < len(timed_batches):
                            next_delay = timed_batches[next_batch][0]
                            until_next = next_delay - elapsed
                            if until_next > 0:
                                sleep_for = min(sleep_for, until_next)
                        time.sleep(max(0.05, sleep_for))

        finally:
            if node is not None:
                try:
                    node.destroy_node()
                except Exception:
                    pass

            try:
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:
                pass

            # Teardown every process launched by this attempt.
            for p in exec_procs:
                terminate_process_tree(p)
            if coord_proc is not None:
                terminate_process_tree(coord_proc)

            # Preserve the old broad fallback cleanup as an additional safeguard.
            shutdown_all_ros_processes()

        time.sleep(2.0)

        if restart_test:
            print(f'Restarting failed test attempt {attempt}/{max_attempts}.')
            clear_test_log_dir(test_dir)
            if attempt < max_attempts:
                continue

            skipped_file = log_root / 'max_attempts_exceeded.txt'
            skipped_file.parent.mkdir(parents=True, exist_ok=True)
            with skipped_file.open('a', encoding='utf-8') as f:
                f.write(
                    f'{test_name} | robots={robot_specs}, '
                    f'mission_arrivals={mission_arrivals}, strategy={strategy}\n'
                )
            return 6

        if result_code != 0:
            error_file = log_root / 'failed_runs.txt'
            error_file.parent.mkdir(parents=True, exist_ok=True)
            with error_file.open('a', encoding='utf-8') as f:
                f.write(
                    f'{test_name} | robots={robot_specs}, '
                    f'mission_arrivals={mission_arrivals}, strategy={strategy} | '
                    f'code={result_code}\n'
                )

        # Keep the per-process logs and the combined log.
        combined_name = f'{test_name}.log'
        combined_path = log_root / combined_name
        combine_logs(
            combined_path,
            test_dir,
            (
                f'spec: robots={robot_specs}, '
                f'mission_arrivals={mission_arrivals}, strategy={strategy}'
            ),
        )
        print('Saved combined log to', combined_path)

        return result_code

    return 6

def parse_structured_spec(spec_path: Path):
    text = spec_path.read_text(encoding='utf-8')
    lines = [l.rstrip() for l in text.splitlines()]
    strategy = None
    robot_specs: List[Tuple[int, List[str]]] = []
    mission_arrivals: List[Tuple[int, List[str]]] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.lower().startswith('strategy:'):
            # next non-empty line is strategy value
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                strategy = lines[j].strip()
                i = j + 1
                continue
        if line.lower().startswith('robots:'):
            j = i + 1
            while j < len(lines):
                l = lines[j].strip()
                if not l or not l.startswith('-'):
                    break
                # pattern: - 3 x [pick, move_to, place]
                m = re.match(r'-\s*(\d+)\s*x\s*\[(.*)\]', l)
                if m:
                    count = int(m.group(1))
                    caps_raw = m.group(2)
                    caps = [c.strip().strip('\"\'') for c in caps_raw.split(',') if c.strip()]
                    robot_specs.append((count, caps))
                else:
                    m2 = re.match(r'-\s*(\d+)\s*x\s*(.*)', l)
                    if m2:
                        count = int(m2.group(1))
                        caps_str = m2.group(2).strip()
                        caps = []
                        if caps_str.startswith('[') and caps_str.endswith(']'):
                            inner = caps_str[1:-1]
                            caps = [c.strip().strip('\"\'') for c in inner.split(',') if c.strip()]
                        robot_specs.append((count, caps))
                j += 1
            i = j
            continue
        if line.lower().startswith('mission arrivals'):
            j = i + 1
            while j < len(lines):
                l = lines[j].strip()
                if not l or not l.startswith('-'):
                    break
                m = re.match(r'-\s*([0-9]+)s:\s*\[(.*)\]', l, flags=re.IGNORECASE)
                if m:
                    delay = int(m.group(1))
                    items_raw = m.group(2)
                    items = [it.strip() for it in items_raw.split(',') if it.strip()]
                    mission_arrivals.append((delay, items))
                else:
                    m2 = re.match(r'-\s*init:\s*\[(.*)\]', l, flags=re.IGNORECASE)
                    if m2:
                        items_raw = m2.group(1)
                        items = [it.strip() for it in items_raw.split(',') if it.strip()]
                        mission_arrivals.append((0, items))
                j += 1
            i = j
            continue
        i += 1

    return strategy, robot_specs, mission_arrivals


def main():
    p = argparse.ArgumentParser()
    p.add_argument('spec_file', help='Path to test specs file')
    p.add_argument('--ros_setup', default='/opt/ros/humble/setup.bash')
    p.add_argument('--ws_setup', default='/root/ros2_ws/install/setup.bash')
    p.add_argument('--mission_dir', default='test_missions')
    p.add_argument('--log_root', default='logs')
    p.add_argument('--timeout', type=int, default=1200)
    args = p.parse_args()

    # Resolve spec_path relative to this script file when a relative path is given
    tests_dir = Path(__file__).resolve().parent
    spec_path = Path(args.spec_file)
    if not spec_path.is_absolute():
        spec_path = (tests_dir / "configurations" / spec_path).resolve()
    if not spec_path.exists():
        print('Spec file not found:', spec_path)
        return 2

    mission_dir = Path(args.mission_dir)
    log_root = Path(args.log_root)

    try:
        strategy, robot_specs, mission_arrivals = parse_structured_spec(spec_path)
    except Exception as e:
        print('Cannot parse structured spec:', e)
        return 2

    if not strategy:
        print('Invalid spec: missing Strategy')
        return 2
    if not robot_specs:
        print('Invalid spec: no Robots entries')
        return 2
    if not mission_arrivals:
        print('Invalid spec: no Mission arrivals entries')
        return 2

    test_name = args.spec_file.split("/")[-1].replace(".txt", "")

    print(
        f'Running structured test: {test_name}',
        f'strategy={strategy},',
        f'robots={robot_specs},',
        f'mission_arrivals={mission_arrivals}',
    )

    rc = run_test(
        args.ros_setup,
        args.ws_setup,
        robot_specs,
        mission_arrivals,
        strategy,
        mission_dir,
        log_root,
        args.timeout,
        1,
        1,
        test_name
    )

    if rc != 0:
        print('Test failed with code', rc)
        return 5

    print('Test succeeded')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
