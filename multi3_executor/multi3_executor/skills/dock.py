import random
from threading import Event

class DockSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.virtual_state = node.virtual_state
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()
    
    def exec(self, params):
        self.node.get_logger().info(f"Simulating dock skill...")

        time_to_goal = random.uniform(1, 4)
        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.virtual_state["docked"] = True

        self.node.get_logger().info(f"Dock completed!")