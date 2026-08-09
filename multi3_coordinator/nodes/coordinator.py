import json
import time
import rclpy
from std_msgs.msg import String, Empty
from rclpy.node import Node

class CoordinatorNode(Node):
    def __init__(self):
        super().__init__("multi3_coordinator_node")

        self.capability_map = { # task -> [required capability]
            "pick": ["pick"],
            "scan": ["scan"],
            "transport": ["transport"],
            "pack": ["pack"]
        }

        # Internal data structures
        self.robot_inventory = {} # Element structure: 'robot_name': {name: 'name', 'state': 'idle/assigned/working/offline', 'current_assigned_fragment': fragment_id, 'capabilities': [cap1, cap2, ...], 'last_heartbeat': timestamp}
        self.fragments_pool = {} # Element structure: 'fragment_id': {id: 'id', 'mission': 'mission_name','state': 'waiting/executable/assigned/completed', 'wait': [task1, ..., taskn], 'tasks': [task1, ..., taskn], 'arrive_timestamp': 1231231, 'priority': 1, 'segment': 'segment_id'}

        self.received_coordination_messages = set() # Each element of this set is a string that has the following structure: "mission/segment/task"

        # Subscribers (only handled by the coordinator)
        self.new_robot_subscription_callback = self.create_subscription(String, '/coordination/new_robot', self.new_robot_subscription_callback, 10)
        self.receive_mission_subscription = self.create_subscription(String, '/coordination/receive_mission', self.receive_mission_callback, 10)
        self.robot_state_update_subscription = self.create_subscription(String, '/coordination/robot_state_update', self.robot_state_update_callback, 10)
        self.execution_start_subscription = self.create_subscription(Empty, '/coordination/execution_start', self.execution_start_callback, 10)
        self.execution_stop_subscription = self.create_subscription(Empty, '/coordination/execution_stop', self.execution_stop_callback, 10)

        # Subscribers (broadcasted to all robots+coordinator)
        self.coordination_messages_subscription = self.create_subscription(String, '/coordination_messages', self.receive_coordination_messages_callback, 10)
        self.hearthbeat_subscription = self.create_subscription(String, '/heartbeat', self.heartbeat_callback, 10)

        # Publishers
        self.fragment_assignment_publisher = self.create_publisher(String, '/fragment_assignment', 10)
        self.coordination_messages_publisher = self.create_publisher(String, '/coordination_messages_batch_updater', 10)

        # Timers
        self.coordination_messages_updater_timer = self.create_timer(2.0, self.publish_all_coordination_messages)
        self.fragment_assignment_timer = self.create_timer(1.0, self.assign_fragments)
        self.fragment_assignment_timer.cancel()
        self.robot_status_monitor_timer = self.create_timer(5.0, self.monitor_robot_status)

        self.get_logger().info('Coordinator node started.')

    def execution_start_callback(self, msg: Empty):
        self.get_logger().info('Execution start signal received. Starting fragment assignment.')
        self.update_fragments_executability()
        self.fragment_assignment_timer.reset()

    def execution_stop_callback(self, msg: Empty):
        self.get_logger().info('Execution stop signal received. Stopping fragment assignment.')
        self.fragment_assignment_timer.cancel()

    def new_robot_subscription_callback(self, msg: String):
        try:
            robot_info = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/new_robot: {exc}')
            return
        robot_name = robot_info.get("name")
        if robot_name:
            self.robot_inventory[robot_name] = robot_info

        self.get_logger().info(f'Received new robot info: {robot_info}')

    def receive_mission_callback(self, msg: String):
        try:
            mission = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/receive_mission: {exc}')
            return

        # Mission is: {"mission": "mission", "priority": 23132, fragments: {"id": "id", "segment": "segment", "wait": ["...."], "tasks": [{...}]}}
        fragments = mission.get("fragments", [])
        for fragment in fragments:
            fragment_id = fragment.get("id")
            if not fragment_id:
                continue

            self.fragments_pool[fragment_id] = {
                "id": fragment_id,
                "mission": mission.get("mission"),
                "state": "waiting",
                "wait": fragment.get("wait", []),
                "tasks": fragment.get("tasks", []),
                "arrive_timestamp": int(time.time()),
                "priority": mission.get("priority", 0),
                "segment": fragment.get("segment")
            }

        self.get_logger().info(f'Received mission: {mission}')

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

        self.get_logger().info(f'Received robot state update: {robot_state}')

    def receive_coordination_messages_callback(self, msg: String):
        try:
            coordination_message = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/coordination_messages: {exc}')
            return
        if "completed_task" in coordination_message:
            self.received_coordination_messages.add(coordination_message["completed_task"])
            self.update_fragments_executability()
        self.get_logger().info(f'Received coordination message: {coordination_message}')

    def heartbeat_callback(self, msg: String):
        try:
            heartbeat_info = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f'Invalid JSON received on /coordination/heartbeat: {exc}')
            return
        robot_name = heartbeat_info.get("robot_name")
        if robot_name and robot_name in self.robot_inventory:
            self.robot_inventory[robot_name]["last_heartbeat"] = int(time.time())
            self.get_logger().info(f'Received heartbeat from {robot_name}.')
        else:
            self.get_logger().warning(f"Received heartbeat from unknown robot: {robot_name}")

    def publish_all_coordination_messages(self):
        if self.received_coordination_messages:
            messages_list = list(self.received_coordination_messages)
            batch_message = json.dumps(messages_list)
            self.coordination_messages_publisher.publish(String(data=batch_message))
            self.get_logger().debug(f'Published coordination messages batch: {batch_message}')

    def assign_fragments(self):
        self.get_logger().debug("Running fragment assigment cycle")
        executable_fragments = self.get_executable_fragments()
        ordered_fragments = sorted(executable_fragments, key=lambda fragment: (fragment.get("priority", 0), fragment.get("arrive_timestamp", 0)))

        for fragment in ordered_fragments:
            eligible_robots = self.get_idle_eligibile_robots(fragment)
            if not eligible_robots:
                continue

            selected_robot = min(eligible_robots,key=lambda robot_name: len(self.robot_inventory[robot_name].get("capabilities", [])))

            fragment["state"] = "assigned"
            self.robot_inventory[selected_robot]["state"] = "assigned"
            self.robot_inventory[selected_robot]["current_assigned_fragment"] = fragment.get("id")

            assignment_message = {
                "robot": selected_robot,
                "fragment": fragment
            }
            self.fragment_assignment_publisher.publish(String(data=json.dumps(assignment_message)))
            self.get_logger().info(f"Assigned fragment {fragment.get('id')} to robot {selected_robot}.")

    def monitor_robot_status(self):
        current_time = int(time.time())
        for robot_name, robot_info in self.robot_inventory.items():
            last_heartbeat = robot_info.get("last_heartbeat", 0)
            if current_time - last_heartbeat > 10:  # Assuming a heartbeat timeout of 10 seconds (more than 3 missed heartbeats)
                self.get_logger().warning(f"Robot {robot_name} has not sent a heartbeat for {current_time - last_heartbeat} seconds. Marking as offline.")
                robot_info["state"] = "offline"
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

    def get_idle_eligibile_robots(self, fragment):
        fragment_required_capabilities = fragment.get("capabilities", [])
        eligible_robots = []
        for robot_name, robot_info in self.robot_inventory.items():
            if robot_info.get("state") == "idle":
                robot_capabilities = robot_info.get("capabilities", [])
                if all(cap in robot_capabilities for cap in fragment_required_capabilities):
                    eligible_robots.append(robot_name)
        return eligible_robots

def main(args=None):
    rclpy.init(args=args)
    node = CoordinatorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()