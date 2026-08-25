from threading import Event

class ScanSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()
    
    def exec(self, params):
        self.node.get_logger().info(f"Simulating scan skill...")

        time_to_goal = 3

        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.node.get_logger().info(f"Scan completed!")