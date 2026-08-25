import math
import random
from threading import Event

class NavigateSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.virtual_state = node.virtual_state
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()

    # Not in use for now
    def _estimate_mov_time(self, pos_a, pos_b, velocity):
        dist = math.sqrt((pos_a[0] - pos_b[0])**2 + (pos_a[0] - pos_b[1])**2)
        t = dist / velocity
        return t
    
    def exec(self, params):
        target_x = params["x"]
        target_y = params["y"]

        initial_x = self.virtual_state["position"]["x"]
        initial_y = self.virtual_state["position"]["y"]

        time_to_goal = self._estimate_mov_time((initial_x, initial_y), (target_x, target_y), 0.5)

        self.node.get_logger().info(f"Simulating navigate skill from position ({initial_x}, {initial_y}) to ({target_x}, {target_y}) ({time_to_goal} seconds)...")

        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.virtual_state["position"] = {
                "x": target_x,
                "y": target_y,
                "z": .0
            }

        self.node.get_logger().info(f"Navigation completed!")