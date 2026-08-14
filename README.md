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
./send_mission.sh --mission_name example_mission_1
```

Start mission
```
./start_mission.sh
```

Collect logs (on a new shell window/tab)
```
docker logs multi3 > <log_filename>.log
```