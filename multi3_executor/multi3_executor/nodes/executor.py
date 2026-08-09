import json
import re
import sys
import time
import rclpy
import threading
from std_msgs.msg import String
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from ..skills.pick import PickSkill
from ..skills.scan import ScanSkill
from ..skills.navigate import NavigateSkill
from ..skills.dock import DockSkill
from ..skills.undock import UndockSkill
from ..skills.pack import PackSkill

# ROBOT_NAME = os.getenv("ROBOT_NAME","robotx")

class ExecutorNode(Node):
    def __init__(self, robot_name: str = "robotx") -> None:
        super().__init__("fragment_exec_node", namespace=robot_name)

        self.callback_group = ReentrantCallbackGroup()

        # self.declare_parameter("skill_list", "-")
        self.declare_parameter("robot_name", robot_name)
        # self.skill_list = self.get_parameter("skill_list").value
        self.robot_name = self.get_parameter("robot_name").value

        self.heartbeat_period = 3 # 3 seconds

        self.robot_capabilities = ["pick", "scan", "transport", "pack"] # Assuming all robots have the same capabilities for now

        self.virtual_state = {
            "position": {
                "x": .0,
                "y": .0,
                "z": .0
            },
            "docked": True,
            "battery": 100.0
        }

        self.skills_map = {
            "pick": PickSkill(self),
            "scan": ScanSkill(self),
            "transport": NavigateSkill(self),
            "navigate": NavigateSkill(self),
            "dock": DockSkill(self),
            "undock": UndockSkill(self),
            "pack": PackSkill(self)
        }

        # Publish:
        self.robot_subscription_publisher = self.create_publisher(String, '/coordination/new_robot', 10)
        self.robot_state_update_publisher = self.create_publisher(String, '/coordination/robot_state_update', 10)
        self.robot_heartbeat_publisher = self.create_publisher(String, '/heartbeat', 10)
        self.coordination_messages_publisher = self.create_publisher(String, '/coordination_messages', 10)

        # Subscribe to:
        self.coordination_messages_subscription = self.create_subscription(String, '/coordination_messages_batch_updater', self.receive_coordination_messages_callback, 10, callback_group=self.callback_group)
        self.fragment_assignment_subscription = self.create_subscription(String, '/fragment_assignment', self.fragment_assignment_callback, 10, callback_group=self.callback_group)

        self.heartbeat_timer = self.create_timer(self.heartbeat_period, self.send_heartbeat)
        self.busy = False
        self.current_fragment = None # {id: 'id', 'mission': 'mission_name','state': 'waiting/executable/assigned/completed', 'wait': [task1, ..., taskn], 'tasks': [task1, ..., taskn], 'arrive_timestamp': 1231231, 'priority': 1, 'segment_id': 'segment_id'}
        self.received_coordination_messages = set()

        self.subscribe_robot()

    def subscribe_robot(self):
        robot_info = {
            "name": self.robot_name,
            "state": "idle",
            "capabilities": self.robot_capabilities
        }
        self.robot_subscription_publisher.publish(String(data=json.dumps(robot_info)))
        self.get_logger().info(f"Published robot registration: {robot_info}")

    def update_robot_state(self):
        update_message = {
            "robot_name": self.robot_name,
            "robot_state": {
                "state": "working" if self.busy else "idle",
                "current_assigned_fragment": self.current_fragment.get("fragment_id") if self.current_fragment else None
            }
        }
        self.robot_state_update_publisher.publish(String(data=json.dumps(update_message)))

    def send_coordination_message(self, task_name, mission_id, segment_id):
        coordination_message = {
            "robot_name": self.robot_name,
            "completed_task": f"{mission_id}/{segment_id}/{task_name}"
        }
        self.coordination_messages_publisher.publish(String(data=json.dumps(coordination_message)))
        self.get_logger().debug(f'Sent coordination message: {coordination_message}')

    def receive_coordination_messages_callback(self, msg: String):
        try:
            coordination_messages = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/coordination_messages: {exc}')
            return
        self.received_coordination_messages = coordination_messages
        # HERE WE NEED TO RESUME FRAGMENTS THAT ARE WAITING!!!
        self.get_logger().debug(f'Received coordination messages: {coordination_messages}')

    def fragment_assignment_callback(self, msg: String):
        try:
            fragment_assignment = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/fragment_assignment: {exc}')
            return

        # If the fragment has been assigned to the robot, then consider it, otherwise, discharge.
        assignee = fragment_assignment.get('robot', '')
        fragment = fragment_assignment.get('fragment', {})
        if assignee == self.robot_name:
            if self.busy:
                self.get_logger().info(f"Received fragment assignment while busy. Ignoring fragment {fragment['id']}.")
                return
            self.get_logger().info(f"Received fragment assignment: {fragment['id']}. Starting execution.")
            self.current_fragment = fragment

            self.busy = True
            self.update_robot_state()

            self.exec(fragment)

            self.get_logger().info(f"Execution of fragment {fragment['id']} completed!")

            # At the end update again the state
            self.current_fragment = None
            self.busy = False
            self.update_robot_state()

    def send_heartbeat(self):
        message = {
            "robot_name": self.robot_name
        }
        self.robot_heartbeat_publisher.publish(String(data=json.dumps(message)))

    def _in_position(self, task_params):
        if "x" not in task_params or "y" not in task_params:
            return True
        else:
            position = self.virtual_state.get("position", {})
            return (
                task_params.get("x") == position.get("x") and
                task_params.get("y") == position.get("y")
            )

    def exec(self, fragment):
        # {id: 'id', 'mission': 'mission_name','state': 'waiting/executable/assigned/completed', 'wait': [task1, ..., taskn], 'tasks': [task1, ..., taskn], 'arrive_timestamp': 1231231, 'priority': 1, 'segment': 'segment_id'}
        fragment_name = fragment.get("id")
        mission_name = fragment.get("mission")
        segment_name = fragment.get("segment")
        wait_list = fragment.get("wait")
        task_list = fragment.get("tasks") # A task has the following structure: {"name": "name", "params": {params...}}

        # 1- Check if the wait list is already complete (it is a double check)
        while wait_list and not all(task in self.received_coordination_messages for task in wait_list):
            self.get_logger().info(f"Waiting for coordination messages before executing fragment {fragment_name}: {wait_list}")
            time.sleep(2)

        # 2- Iterate over the task list, check the preconditinons (eg., undock, move, etc.), then execute the skill
        for task in task_list:
            if self.virtual_state["docked"]:
                self.get_logger().log(f"EventType: Control, Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.perf_counter()}")
                self.skills_map["undock"].exec()
                self.get_logger().log(f"EventType: Control, Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.perf_counter()}")

            if not _in_position(task.get("params")):
                self.get_logger().log(f"EventType: Control, Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.perf_counter()}")
                self.skills_map["navigate"].exec()
                self.get_logger().log(f"EventType: Control, Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.perf_counter()}")

            task_name = task.get("name")

            self.get_logger().log(f"EventType: Task, Activity: task_name, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.perf_counter()}")
            self.skills_map[task_name].exec(task.get("params"))
            self.get_logger().log(f"EventType: Task, Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.perf_counter()}")

            self.send_coordination_message(task_name, mission_name, segment_name)

def _parse_robot_name_from_args(argv):
    for idx, arg in enumerate(argv[1:], start=1):
        if arg.startswith("robot_name:="):
            return arg.split(":=", 1)[1]
        if arg == "-p" and idx + 1 < len(argv):
            next_arg = argv[idx + 1]
            if next_arg.startswith("robot_name:="):
                return next_arg.split(":=", 1)[1]
    return "robotx"


def main(args=None):
    robot_name = _parse_robot_name_from_args(sys.argv)
    rclpy.init(args=args)
    node = ExecutorNode(robot_name=robot_name)
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
    
