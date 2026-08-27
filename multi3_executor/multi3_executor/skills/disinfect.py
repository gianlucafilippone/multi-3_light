from threading import Event

class DisinfectSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()
    
    def exec(self, params):
        time_to_goal = params["size"] * 2 if "size" in params else 10

        self.node.get_logger().info(f"Simulating disinfect skill ({time_to_goal}s to complete)...")

        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.node.get_logger().info(f"Disinfect completed!")