import json
import time
import rclpy
import random
from std_msgs.msg import String
from std_srvs.srv import Trigger
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup

class CoordinatorNode(Node):
    def __init__(self):
        super().__init__("multi3_coordinator_node")

        self.declare_parameter('assignment_strategy', 'baseline') # baseline, random, greedy
        try:
            self.assignment_strategy = self.get_parameter('assignment_strategy').get_parameter_value().string_value
            self.get_logger().info(f"Assignment strategy parameter: {self.assignment_strategy}")
        except Exception:
            self.assignment_strategy = 'baseline'
        self.get_logger().info(f"Using assignment strategy: {self.assignment_strategy}")

        self.callback_group = ReentrantCallbackGroup()

        self.capability_map = { # task -> [required capability]
            "pick": ["pick"],
            "move_to": ["move_to"],
            "pick": ["place"],
            "scan": ["scan"],
            "pack": ["pack"],
            "load_pack": ["load_pack"],
            "unload_pack": ["unload_pack"],
            "inspect": ["inspect"],
            "irrigate": ["irrigate"],
            "spray": ["spray"],
            "final_inspection": ["inspect"],
            "check_room": ["check_room"],
            "vacuum": ["vacuum"],
            "mop": ["mop"],
            "disinfect": ["disinfect"],
            "final_room_check": ["check_room"]
        }

        # Internal data structures
        self.robot_inventory = {} # Element structure: 'robot_name': {name: 'name', 'operational_state': 'idle/working/offline/selected', 'current_assigned_fragment': fragment_id, 'capabilities': [cap1, cap2, ...], 'last_heartbeat': timestamp, 'state': {}}
        self.fragments_pool = {} # Element structure: 'fragment_id': {id: 'id', 'mission': 'mission_name','state': 'waiting/executable/assigned/completed', 'wait': [task1, ..., taskn], 'tasks': [task1, ..., taskn], 'arrive_timestamp': 1231231, 'priority': 1, 'segment': 'segment_id'}

        self.received_coordination_messages = set()

        self.misligned_inventory = False

        # Subscribers (only handled by the coordinator)
        self.new_robot_subscription_callback = self.create_subscription(String, '/coordination/new_robot', self.new_robot_subscription_callback, 10, callback_group=self.callback_group)
        self.receive_mission_subscription = self.create_subscription(String, '/coordination/new_missions', self.receive_mission_callback, 10, callback_group=self.callback_group)
        self.robot_state_update_subscription = self.create_subscription(String, '/coordination/robot_state_update', self.robot_state_update_callback, 10, callback_group=self.callback_group)

        # Subscribers (broadcasted to all robots+coordinator)
        self.coordination_messages_subscription = self.create_subscription(String, '/coordination_messages', self.receive_coordination_messages_callback, 10, callback_group=self.callback_group)
        self.hearthbeat_subscription = self.create_subscription(String, '/heartbeat', self.heartbeat_callback, 10, callback_group=self.callback_group)

        # Publishers
        self.fragment_assignment_publisher = self.create_publisher(String, '/fragment_assignment', 10)
        self.coordination_messages_publisher = self.create_publisher(String, '/coordination_messages_batch_updater', 10)

        # Services for control
        self.execution_start_subscription = self.create_service(Trigger, '/control/execution_start', self.execution_start_callback, callback_group=self.callback_group)
        self.execution_stop_subscription = self.create_service(Trigger, '/control/execution_stop', self.execution_stop_callback, callback_group=self.callback_group)

        # Services for getting status info
        self.get_robot_inventory_service = self.create_service(Trigger, '/info/robot_inventory', self.get_robot_inventory_callback, callback_group=self.callback_group)
        self.get_fragments_pool_service = self.create_service(Trigger, '/info/fragment_pool', self.get_fragments_pool_callback, callback_group=self.callback_group)
        self.get_inventory_misalignment = self.create_service(Trigger, '/info/inventory_check', self.get_inventory_misalignment_callback, callback_group=self.callback_group)

        # Timers
        self.coordination_messages_updater_timer = self.create_timer(2.0, self.publish_all_coordination_messages)
        self.fragment_assignment_timer = self.create_timer(1.0, self.assign_fragments)
        self.fragment_assignment_timer.cancel()
        self.robot_status_monitor_timer = self.create_timer(3.0, self.monitor_robot_status)

        self.get_logger().info('Coordinator node started.')

    def execution_start_callback(self, request, response):
        self.get_logger().info('Execution start signal received. Starting fragment assignment.')
        self.fragment_assignment_timer.reset()
        response.success = True
        response.message = "Execution started!"
        return response

    def execution_stop_callback(self, request, response):
        self.get_logger().info('Execution stop signal received. Stopping fragment assignment.')
        self.fragment_assignment_timer.cancel()
        response.success = True
        response.message = "Fragment assignment stopped."
        return response

    def get_robot_inventory_callback(self, request, response):
        response.success = True
        response.message = json.dumps(self.robot_inventory)
        return response

    def get_fragments_pool_callback(self, resquest, response):
        response.success = True
        response.message = json.dumps(self.fragments_pool)
        return response

    def get_inventory_misalignment_callback(self, request, response):
        response.success = True
        response.message = "Misaligned" if self.misligned_inventory else "Ok"
        return response

    def new_robot_subscription_callback(self, msg: String):
        try:
            robot_info = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/new_robot: {exc}')
            return
        robot_name = robot_info.get("name")
        if robot_name:
            self.robot_inventory[robot_name] = robot_info
            self.robot_inventory[robot_name]["last_heartbeat"] = int(time.time())

        self.get_logger().info(f'Received new robot info: {robot_info}')

    def receive_mission_callback(self, msg: String):
        try:
            mission = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/receive_mission: {exc}')
            return

        self.get_logger().info(f'Received mission: {mission}')

        # Mission is: {"mission": "mission", "priority": 23132, fragments: {"id": "id", "segment": "segment", "wait": ["...."], "tasks": [{...}]}}
        fragments = mission.get("fragments", [])
        for fragment in fragments:
            fragment_id = fragment.get("id")
            if not fragment_id:
                continue

            if fragment_id in self.fragments_pool:
                self.get_logger().warning(f"A fragment with id {fragment_id} already exists in the pool! Skipping the new one...")
            else:
                self.fragments_pool[fragment_id] = {
                    "id": fragment_id,
                    "mission": mission.get("mission"),
                    "state": "waiting",
                    "wait": fragment.get("wait", []),
                    "tasks": fragment.get("tasks", []),
                    "arrive_timestamp": int(time.time()),
                    "priority": mission.get("priority", 0),
                    "segment": fragment.get("segment"),
                }
        self.update_fragments_executability()

    def robot_state_update_callback(self, msg: String):
        try:
            robot_state_update = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/robot_state_update: {exc}')
            return

        robot_name = robot_state_update.get("robot_name")
        robot_state = robot_state_update.get("robot_state")
        if robot_name and robot_name in self.robot_inventory:
            self.robot_inventory[robot_name].update(robot_state)
        else:
            self.get_logger().warning(f"Received state update for unknown robot: {robot_name}")
            self.misligned_inventory = True

        self.get_logger().info(f'Received robot state update from robot {robot_name}: {robot_state}')

    def receive_coordination_messages_callback(self, msg: String):
        try:
            coordination_message = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/coordination_messages: {exc}')
            return
        if "completed_task" in coordination_message:
            self.received_coordination_messages.add(coordination_message["completed_task"])
            self.update_fragments_executability()
        if "completed_fragment" in coordination_message:
            self.fragments_pool[coordination_message["completed_fragment"]]["state"] = "completed"
        if "state" in coordination_message:
            robot_name = coordination_message["robot_name"]
            self.robot_inventory[robot_name]["state"] = coordination_message["state"]
        self.get_logger().info(f'Received coordination message: {coordination_message}')

    def heartbeat_callback(self, msg: String):
        try:
            heartbeat_info = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/heartbeat: {exc}')
            return
        robot_name = heartbeat_info.get("robot_name")
        robot_state = heartbeat_info.get("robot_state")
        robot_conditions = heartbeat_info.get("conditions")
        if robot_name and robot_name in self.robot_inventory:
            self.robot_inventory[robot_name]["last_heartbeat"] = int(time.time())
            if self.robot_inventory[robot_name]["operational_state"] == "offline":
                self.get_logger().info(f'Received heartbeat from {robot_name}. Updating robot state.')
            if self.robot_inventory[robot_name]["operational_state"] != "selected" and robot_state == "idle": # Avoiding changing state if the robot is currently selected for the fragment assignment
                self.robot_inventory[robot_name]["operational_state"] = robot_state
            self.robot_inventory[robot_name]["conditions"] = robot_conditions
        else:
            self.get_logger().warning(f"Received heartbeat from unknown robot: {robot_name}")
            self.misligned_inventory = True

    def publish_all_coordination_messages(self):
        if self.received_coordination_messages:
            messages_list = list(self.received_coordination_messages)
            batch_message = json.dumps(messages_list)
            self.coordination_messages_publisher.publish(String(data=batch_message))
            self.get_logger().debug(f'Published coordination messages batch: {batch_message}')

    def assign_fragments(self):
        self.get_logger().debug("Running fragment assigment cycle")
        executable_fragments = self.get_executable_fragments()
        ordered_fragments = sorted(executable_fragments, key=lambda fragment: (fragment.get("priority", 0), int(fragment.get("arrive_timestamp", 0) // 60)))

        if self.assignment_strategy == "closest":

            available_robots = [robot_name for robot_name, robot_info in self.robot_inventory.items() if robot_info.get("operational_state") == "idle"]

            for robot_name in available_robots:
                eligible_fragments = [fragment for fragment in ordered_fragments if fragment.get("state") == "executable" and robot_name in self.get_idle_eligibile_robots(fragment)]

                if not eligible_fragments:
                    continue

                selected_fragment = self.get_closest_fragment(robot_name, eligible_fragments)

                selected_fragment["state"] = "assigned"
                self.robot_inventory[robot_name]["operational_state"] = "selected"
                self.robot_inventory[robot_name]["current_assigned_fragment"] = selected_fragment.get("id")

                assignment_message = {
                    "robot": robot_name,
                    "fragment": selected_fragment
                }

                self.fragment_assignment_publisher.publish(
                    String(data=json.dumps(assignment_message))
                )

                self.get_logger().info(f"Assigned fragment {selected_fragment.get('id')} to robot {robot_name}.")
        else:
            for fragment in ordered_fragments:
                eligible_robots = self.get_idle_eligibile_robots(fragment)
                if not eligible_robots:
                    continue

                # if self.assignment_strategy == "closest":
                #     # Greedy assignment (the closest)
                #     selected_robot = self.get_closest_robot(eligible_robots, fragment)
                if self.assignment_strategy == "capability-preserving":
                    # Greedy assignment (capability preserving)
                    selected_robot = min(eligible_robots,key=lambda robot_name: len(self.robot_inventory[robot_name].get("capabilities", [])))
                else:
                    # Random assignemnt (baseline)
                    selected_robot = random.choice(eligible_robots)

                fragment["state"] = "assigned"
                self.robot_inventory[selected_robot]["operational_state"] = "selected"
                self.robot_inventory[selected_robot]["current_assigned_fragment"] = fragment.get("id")

                assignment_message = {
                    "robot": selected_robot,
                    "fragment": fragment
                }
                self.fragment_assignment_publisher.publish(String(data=json.dumps(assignment_message)))
                self.get_logger().info(f"Assigned fragment {fragment.get('id')} to robot {selected_robot}.")

    def get_closest_fragment(self, robot_name, fragments):
        if not fragments:
            return None

        robot_info = self.robot_inventory.get(robot_name, {})
        robot_state = robot_info.get("state", {})
        robot_position = robot_state.get("position", {})

        robot_x = robot_position.get("x", 0.0)
        robot_y = robot_position.get("y", 0.0)
        robot_z = robot_position.get("z", 0.0)

        def distance_to_fragment(fragment):
            tasks = fragment.get("tasks") or []

            if not tasks:
                return float("inf")

            target_position = tasks[0].get("params", {})
            target_x = target_position.get("x", 0.0)
            target_y = target_position.get("y", 0.0)
            target_z = target_position.get("z", 0.0)
            return ((robot_x - target_x) ** 2 +
                    (robot_y - target_y) ** 2 +
                    (robot_z - target_z) ** 2) ** 0.5

        return min(fragments, key=distance_to_fragment)

    def get_closest_robot(self, robots, fragment):
        if not robots:
            return None

        tasks = fragment.get("tasks") or []
        if not tasks:
            return robots[0]

        target_position = tasks[0].get("params", {})
        target_x = target_position.get("x", 0.0)
        target_y = target_position.get("y", 0.0)
        target_z = target_position.get("z", 0.0)

        def distance_to_target(robot_name):
            robot_info = self.robot_inventory.get(robot_name, {})
            robot_state = robot_info.get("state", {})
            position = robot_state.get("position", {})
            robot_x = position.get("x", 0.0)
            robot_y = position.get("y", 0.0)
            robot_z = position.get("z", 0.0)
            return ((robot_x - target_x) ** 2 +
                    (robot_y - target_y) ** 2 +
                    (robot_z - target_z) ** 2) ** 0.5

        return min(robots, key=distance_to_target)

    def monitor_robot_status(self):
        current_time = int(time.time())
        for robot_name, robot_info in self.robot_inventory.items():
            last_heartbeat = robot_info.get("last_heartbeat", 0)
            if (current_time - last_heartbeat > 10) and (robot_info["operational_state"] != "offline"):  # Assuming a heartbeat timeout of 10 seconds (more than 3 missed heartbeats)
                self.get_logger().warning(f"Robot {robot_name} has not sent a heartbeat for {current_time - last_heartbeat} seconds. Marking as offline.")
                robot_info["operational_state"] = "offline"
                # Handle reassigning fragments or recovery actions here.

    def update_fragments_executability(self):
        for fragment_id, fragment in self.fragments_pool.items():
            if fragment.get("state") == "waiting":
                wait_tasks = fragment.get("wait", [])
                if not wait_tasks or all(task in self.received_coordination_messages for task in wait_tasks):
                    fragment["state"] = "executable"
                    self.get_logger().info(f"Fragment {fragment_id} is now executable because all signal messages were received.")

    def get_executable_fragments(self):
        executable_fragments = []
        for fragment_id, fragment in self.fragments_pool.items():
            if fragment.get("state") == "executable":
                executable_fragments.append(fragment)
        return executable_fragments

    def get_fragment_capabilities(self, fragment):
        required_capabilities = set()
        for task in fragment.get("tasks", []):
            task_name = task["name"]
            task_capabilities = self.capability_map.get(task_name, [])
            required_capabilities.update(task_capabilities)
        return list(required_capabilities)

    def get_idle_eligibile_robots(self, fragment):
        fragment_required_capabilities = self.get_fragment_capabilities(fragment)
        eligible_robots = []
        for robot_name, robot_info in self.robot_inventory.items():
            if robot_info.get("operational_state") == "idle" and all(cap in robot_info.get("capabilities", []) for cap in fragment_required_capabilities) and all(precondition in robot_info.get("conditions") for precondition in fragment.get("preconditions", [])):
                eligible_robots.append(robot_name)
        return eligible_robots

def main(args=None):
    rclpy.init(args=args)
    node = CoordinatorNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    executor.spin()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()