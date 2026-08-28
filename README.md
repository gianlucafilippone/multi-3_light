# Multi-3 Light

Lightweight ROS2-based implementation of Multi-3 for robotic mission execution process mining


## Deploy and run tests (Docker)

Build and run the Docker container:
```
docker build -t multi3-test .
```

Create a directory to collect logs (e.g., `execution_logs` folder in repo root):
```
mkdir -p $(pwd)/execution_logs
```

Run the container and mount the host `execution_logs` directory into the container so test outputs are persisted on the host:
```
docker run --rm -it \
	-v "$(pwd)/execution_logs":/root/ros2_ws/logs \
	--entrypoint /bin/bash \
	multi3-test \
	-lc "source /opt/ros/humble/setup.bash && source /root/ros2_ws/install/setup.bash && python3 tests/run_tests.py <test-configuration-name>.txt"
```

