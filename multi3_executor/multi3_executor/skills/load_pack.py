from threading import Event

class LoadPackSkill():
    def __init__(self, node) -> None:
        self.node = node
        self.node.get_logger().info(f"Setting up skill: {self.__class__.__name__}")
        self.exec_event = Event()
    
    def exec(self, params):
        self.node.get_logger().info(f"Simulating load_pack skill...")

        item_weight = params["weight"]

        time_to_goal = item_weight if item_weight < 10 else 10

        self.exec_event.wait(time_to_goal)
        self.exec_event.clear()

        self.node.get_logger().info(f"LoadPack completed!")