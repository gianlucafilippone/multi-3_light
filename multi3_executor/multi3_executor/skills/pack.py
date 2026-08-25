from threading import Event

class PackSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()
    
    def exec(self, params):
        self.node.get_logger().info(f"Simulating pack skill...")

        items = params["items"]
        time_to_goal = items * 6
        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.node.get_logger().info(f"Packing completed!")