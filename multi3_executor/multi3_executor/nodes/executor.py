import json
import sys
import time
import rclpy
from std_msgs.msg import String
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from ..skills.dock import DockSkill
from ..skills.undock import UndockSkill
from ..skills.localize import LocalizeSkill
from ..skills.navigate import NavigateSkill
from ..skills.pick import PickSkill
from ..skills.scan import ScanSkill
from ..skills.place import PlaceSkill
from ..skills.pack import PackSkill
from ..skills.load_pack import LoadPackSkill
from ..skills.unload_pack import UnloadPackSkill
from ..skills.inspect import InspectSkill
from ..skills.irrigate import IrrigateSkill
from ..skills.spray import SpraySkill
from ..skills.align_camera import AlignCameraSkill
from ..skills.check_room import CheckRoomSkill
from ..skills.vacuum import VacuumSkill
from ..skills.mop import MopSkill
from ..skills.disinfect import DisinfectSkill

class ExecutorNode(Node):
    def __init__(self, robot_name: str = "robotx") -> None:
        super().__init__("fragment_exec_node", namespace=robot_name)

        self.callback_group = ReentrantCallbackGroup()

        # Allow overriding robot_name and robot_capabilities via CLI args
        parsed_caps = _parse_capabilities_from_args(sys.argv)
        self.declare_parameter("robot_name", robot_name)
        self.declare_parameter("robot_capabilities", parsed_caps if parsed_caps is not None else [])

        self.robot_name = self.get_parameter("robot_name").value
        self.robot_capabilities = self.get_parameter("robot_capabilities").value or []

        self.heartbeat_period = 3 # 3 seconds

        self.virtual_state = {
            "position": {
                "x": .0,
                "y": .0,
                "z": .0
            },
            "docked": True,
        }

        self.skills_map = {
            "dock": DockSkill(self) if "dock" in self.robot_capabilities else None,
            "undock": UndockSkill(self) if "undock" in self.robot_capabilities else None,
            "pick": PickSkill(self) if "pick" in self.robot_capabilities else None,
            "scan": ScanSkill(self) if "scan" in self.robot_capabilities else None,
            "move_to": NavigateSkill(self) if "move_to" in self.robot_capabilities else None,
            "place": PlaceSkill(self) if "place" in self.robot_capabilities else None,
            "pack": PackSkill(self) if "pack" in self.robot_capabilities else None,
            "localize": LocalizeSkill(self) if "localize" in self.robot_capabilities else None,
            "load_pack": LoadPackSkill(self) if "load_pack" in self.robot_capabilities else None,
            "unload_pack": UnloadPackSkill(self) if "unload_pack" in self.robot_capabilities else None,
            "inspect": InspectSkill(self) if "inspect" in self.robot_capabilities else None,
            "irrigate": IrrigateSkill(self) if "irrigate" in self.robot_capabilities else None,
            "spray": SpraySkill (self) if "spray" in self.robot_capabilities else None,
            "final_inspection": InspectSkill(self) if "inspect" in self.robot_capabilities else None,
            "align_camera": AlignCameraSkill(self) if "align_camera" in self.robot_capabilities else None,
            "check_room": CheckRoomSkill(self) if "check_room" in self.robot_capabilities else None,
            "vacuum": VacuumSkill (self) if "vacuum" in self.robot_capabilities else None,
            "mop": MopSkill(self) if "mop" in self.robot_capabilities else None,
            "disinfect": DisinfectSkill(self) if "disinfect" in self.robot_capabilities else None,
            "final_room_check": CheckRoomSkill(self) if "check_room" in self.robot_capabilities else None
        }

        # Control actions to be completed before executing the task
        self.task_controls = {
            "pick": ["undock", "move_to", "localize"],
            "move_to": ["undock"],
            "place": ["undock", "move_to", "localize"],
            "scan": ["localize"], 
            "pack": ["localize"],
            "load_pack": ["undock", "move_to", "localize"],
            "unload_pack": ["undock", "move_to", "localize"],
            "inspect": ["move_to", "align_camera"],
            "irrigate": ["move_to"],
            "spray": ["move_to"],
            "final_inspection": ["move_to", "align_camera"],
            "check_room": ["undock", "move_to"],
            "vacuum": ["undock", "move_to"],
            "mop": ["undock", "move_to"],
            "disinfect": ["undock", "move_to"],
            "final_room_check": ["undock", "move_to"]
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
            "operational_state": "idle",
            "capabilities": self.robot_capabilities,
            "state": self.virtual_state
        }
        self.robot_subscription_publisher.publish(String(data=json.dumps(robot_info)))
        self.get_logger().info(f"Published robot registration: {robot_info}")

    def update_robot_state(self):
        update_message = {
            "robot_name": self.robot_name,
            "robot_state": {
                "operational_state": "working" if self.busy else "idle",
                "current_assigned_fragment": self.current_fragment.get("id") if self.current_fragment else None,
                "state": self.virtual_state
            }
        }
        self.robot_state_update_publisher.publish(String(data=json.dumps(update_message)))

    def send_coordination_message(self, task_label):
        coordination_message = {
            "robot_name": self.robot_name,
            "completed_task": f"{task_label}"
        }
        self.coordination_messages_publisher.publish(String(data=json.dumps(coordination_message)))
        self.get_logger().debug(f'Sent coordination message: {coordination_message}')

    def send_fragment_completion_message(self, fragment_id):
        coordination_message = {
            "robot_name": self.robot_name,
            "completed_fragment": fragment_id,
            "state": self.virtual_state
        }
        self.coordination_messages_publisher.publish(String(data=json.dumps(coordination_message)))
        self.get_logger().debug(f'Sent coordination message (completion of fragment): fragment {coordination_message} completed')

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
            self.get_logger().info(f"Received fragment assignment: {fragment_assignment}")
            if self.busy:
                self.get_logger().info(f"Received fragment assignment while busy. Ignoring fragment {fragment['id']}.")
                return
            self.get_logger().info(f"Starting execution of fragment {fragment['id']}...")
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
            "robot_name": self.robot_name,
            "robot_state": "working" if self.busy else "idle",
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
        fragment_id = fragment.get("id")
        mission_name = fragment.get("mission")
        segment_name = fragment.get("segment")
        wait_list = fragment.get("wait")
        task_list = fragment.get("tasks") # A task has the following structure: {"name": "name", "params": {params...}}

        # 1- Check if the wait list is already complete (it is a double check)
        while wait_list and not all(task in self.received_coordination_messages for task in wait_list):
            self.get_logger().info(f"Waiting for coordination messages before executing fragment {fragment_id}: {wait_list}")
            time.sleep(2)

        # 3- Iterate over the task list, check the preconditinons (eg., undock, move, etc.), then execute the skill
        for task in task_list:

            task_name = task.get("name")
            task_label = task.get("label")

            if task_name not in self.robot_capabilities:
                self.get_logger().error(f"Robot {self.robot_name} was assigned a fragment without required capabilities!")

            self.get_logger().info(f"Executing task {task_label}. Params: {task.get('params')}, Virtual state: {self.virtual_state}")

            task_controls = self.task_controls[task_name]

            # First, execute control actions
            for task_control in task_controls:

                if task_control == "undock":
                    if self.virtual_state["docked"]:
                        self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.time()}")
                        self.skills_map["undock"].exec({})
                        self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: undock, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.time()}")

                elif task_control == "move_to":
                    # Navigate has to be executed only if the robot is not in position already
                    if not self._in_position(task.get("params")):
                        self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: move_to, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.time()}")
                        self.skills_map["move_to"].exec(task.get("params"))
                        self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: move_to, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.time()}")

                else:
                    self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: {task_control}, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.time()}")
                    self.skills_map[task_control].exec(task.get("params"))
                    self.get_logger().info(f"EventType: Control (Task: {task_name}), Activity: {task_control}, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.time()}")

            self.get_logger().info(f"EventType: Task, Activity: {task_name}, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, Start: {time.time()}")
            self.skills_map[task_name].exec(task.get("params"))
            self.get_logger().info(f"EventType: Task, Activity: {task_name}, Robot: {self.robot_name}, Mission: {mission_name}, Segment: {segment_name}, End: {time.time()}")

            self.send_coordination_message(task_label)

        self.send_fragment_completion_message(fragment_id)


def _parse_robot_name_from_args(argv):
    for idx, arg in enumerate(argv[1:], start=1):
        if arg.startswith("robot_name:="):
            return arg.split(":=", 1)[1]
        if arg == "-p" and idx + 1 < len(argv):
            next_arg = argv[idx + 1]
            if next_arg.startswith("robot_name:="):
                return next_arg.split(":=", 1)[1]
    return "robotx"


def _parse_capabilities_string(raw: str):
    # Try JSON first (handles ["a","b"] or ['a','b'])
    try:
        caps = json.loads(raw)
        if isinstance(caps, list):
            return [str(c) for c in caps]
    except Exception:
        pass

    # Fallback: strip brackets and split by comma
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts = [p.strip().strip("'\"") for p in s.split(",") if p.strip()]
    return parts if parts else None


def _parse_capabilities_from_args(argv):
    for idx, arg in enumerate(argv[1:], start=1):
        if arg.startswith("capabilities:="):
            raw = arg.split(":=", 1)[1]
            return _parse_capabilities_string(raw)
        if arg == "-p" and idx + 1 < len(argv):
            next_arg = argv[idx + 1]
            if next_arg.startswith("capabilities:="):
                raw = next_arg.split(":=", 1)[1]
                return _parse_capabilities_string(raw)
    return None


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
