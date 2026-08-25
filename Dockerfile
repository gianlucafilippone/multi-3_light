FROM ros:humble-ros-base

WORKDIR /root/ros2_ws

COPY ./multi3_coordinator ./src/multi3_coordinator
COPY ./multi3_executor ./src/multi3_executor

COPY ./example_missions ./example_missions
COPY run_multi3.sh .
COPY send_mission.sh .
COPY start_mission.sh .
COPY stop_mission.sh .
COPY get_fragments.sh .
COPY get_robots.sh .
COPY ./tests ./tests
COPY test_specs.txt .

RUN apt update && apt install -y \
    python3-pip \
    python3-colcon-common-extensions \
    jq \
    && rm -rf /var/lib/apt/lists/* \
    && rosdep update

RUN apt-get update && rosdep install --from-paths src -y --ignore-src

RUN /bin/bash -c "source /opt/ros/humble/setup.bash && colcon build"

RUN chmod +x run_multi3.sh
RUN chmod +x send_mission.sh
RUN chmod +x start_mission.sh
RUN chmod +x stop_mission.sh
RUN chmod +x get_fragments.sh
RUN chmod +x get_robots.sh
RUN chmod +x tests/run_tests.py || true

ENV RCUTILS_COLORIZED_OUTPUT=0

CMD ["/bin/bash"]