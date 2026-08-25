#!/usr/bin/env python3
"""Helper to call /info/fragment_pool and parse the returned JSON.

Provides a function `get_fragment_pool()` for programmatic use and a
CLI that polls until all fragments are completed or a timeout occurs.
"""
import json
import time
import sys
from typing import Any, Dict, Optional

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger


def get_fragment_pool(node: Node, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    client = node.create_client(Trigger, '/info/fragment_pool')
    if not client.wait_for_service(timeout_sec=timeout):
        return None
    req = Trigger.Request()
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
    if future.done() and future.result() is not None:
        res = future.result()
        try:
            return json.loads(res.message)
        except Exception:
            # If message isn't valid JSON, return raw message in dict
            return {'raw_message': res.message}
    return None


def all_completed(pool: Dict[str, Any]) -> bool:
    # pool expected to be a mapping of fragment ids -> metadata including `state`
    for v in pool.values():
        state = v.get('state') if isinstance(v, dict) else None
        if state != 'completed':
            return False
    return True


def cli_poll(timeout: int = 300, interval: float = 2.0) -> int:
    rclpy.init()
    node = rclpy.create_node('poll_fragment_pool_client')
    try:
        elapsed = 0.0
        while elapsed < timeout:
            pool = get_fragment_pool(node, timeout=2.0)
            if pool is None:
                print('no-response')
            else:
                print(json.dumps(pool))
                if all_completed(pool):
                    print('ALL_COMPLETED')
                    return 0
            time.sleep(interval)
            elapsed += interval
        print('TIMEOUT')
        return 2
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--interval', type=float, default=2.0)
    args = p.parse_args()
    raise SystemExit(cli_poll(timeout=args.timeout, interval=args.interval))
