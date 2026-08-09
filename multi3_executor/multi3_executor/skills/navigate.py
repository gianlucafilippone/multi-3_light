import math
import random
from threading import Event

class NavigateSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()

    def _estimate_mov_time(pos_a, pos_b, velocity):
        dist = math.sqrt((pos_a[0] - pos_b[0])**2 + (pos_a[0] - pos_b[1])**2)
        t = dist / velocity
        return t
    
    def exec(self, robot_state, params):
        self.node.get_logger().info(f"Simulating navigate skill...")

        time_to_goal = random.uniform(3, 8)
        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.node.get_logger().info(f"Navigation completed!")