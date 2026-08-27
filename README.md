# Multi-3 Light

Lightweight ROS2-based implementation of Multi-3 for robotic mission execution process mining


## Deploy and run (Docker)

Build and run the Docker container
```
docker build -t multi3 .
docker run -it --rm --name multi3 multi3
```

Start Multi-3 (simulating N robots)

```
./run_multi3.sh --robots N
```

Send mission to execute (on a new shell window/tab)
```
docker exec -it multi3 bash
```

```
./send_mission.sh <mission_name>
```

`mission_name` is the name of the `.json` file inside the folder `example_mission` (without extension)

Start mission
```
./start_mission.sh
```

Collect logs (on a new shell window/tab)
```
docker logs multi3 > <log_filename>.log
```

**Run tests in Docker**

Build a test image (includes test scripts and tools):
```
docker build -t multi3-test .
```

Prepare a host directory to receive logs (example uses a `logs` folder in repo root):
```
mkdir -p $(pwd)/execution_logs
```

Run the container and mount the host `logs` directory into the container so test outputs are persisted on the host:
```
docker run --rm -it \
	-v "$(pwd)/execution_logs":/root/ros2_ws/logs \
	--entrypoint /bin/bash \
	multi3-test \
	-lc "source /opt/ros/humble/setup.bash && source /root/ros2_ws/install/setup.bash && python3 tests/run_tests.py test/test_order_management_baseline.txt"
```

Notes:
- The runner saves per-test outputs under `/root/ros2_ws/logs/<timestamp>/` inside the container — these will appear on the host under `./logs/<timestamp>/` because of the bind mount.
- Combined log files are named with the pattern `run_m{missions}_r{robots}_{strategy}.log`, e.g. `run_m1-2_r1_baseline.log` and will be in the same `logs/<timestamp>/` directory.
- If you forgot to mount a host folder you can still copy logs out of a running or exited container with:
```
docker cp <container_id>:/root/ros2_ws/logs ./logs_from_container
```

If you need a different ROS distro or alternate workspace path, pass different `--ros_setup` and `--ws_setup` arguments to the runner commands inside the container.