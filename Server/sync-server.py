#!/usr/bin/python3
# usage: sync_server.py <delay> <num_ready_subscribers> <num_tx_subscribers>
#
# VALUE "num_ready_subscribers" --> IMPORTANT
#   The server waits until all subscribers have sent their "alive" or ready
#   message before starting a measurement.
#
# VALUE "num_tx_subscribers" --> IMPORTANT
#   The server waits until this many subscribers have sent their "TX MODE"
#   message before proceeding to the phase measurement.
#

import zmq
import time
import sys
import os
from datetime import datetime
from helper import *

# =============================================================================
#                           Experiment Configuration
# =============================================================================
host = "*"               # Host address to bind to. "*" means all available interfaces.
sync_port = "5557"       # Port used for synchronization messages.
alive_port = "5558"      # Port used for heartbeat/alive messages.
data_port = "5559"       # Port used for data transmission.
# =============================================================================

# ------------------------ CLI 参数解析 ------------------------
# 默认：delay = 10s, 第一阶段等 5 个，第二阶段等 4 个
if len(sys.argv) >= 4:
    delay = int(sys.argv[1])
    num_ready_subscribers = int(sys.argv[2])   # 第一阶段 alive 数量
    num_tx_subscribers = int(sys.argv[3])      # 第二阶段 TX MODE 数量
elif len(sys.argv) == 3:
    delay = int(sys.argv[1])
    num_ready_subscribers = int(sys.argv[2])
    # 如果没给第三个参数，就默认 TX 阶段人数等于 alive 阶段
    num_tx_subscribers = num_ready_subscribers
else:
    delay = 10
    num_ready_subscribers = 5
    num_tx_subscribers = 4

# Creates a socket instance
context = zmq.Context()

sync_socket = context.socket(zmq.PUB)
sync_socket.bind("tcp://{}:{}".format(host, sync_port))

alive_socket = context.socket(zmq.REP)
alive_socket.bind("tcp://{}:{}".format(host, alive_port))

data_socket = context.socket(zmq.REP)
data_socket.bind("tcp://{}:{}".format(host, data_port))

# Measurement and experiment identifiers
meas_id = 0

# Unique ID for the experiment based on current UTC timestamp
unique_id = str(datetime.utcnow().strftime("%Y%m%d%H%M%S"))

# ZeroMQ poller setup
poller = zmq.Poller()
poller.register(alive_socket, zmq.POLLIN)

new_msg_received = 0
WAIT_TIMEOUT = 60.0 * 10.0  # 10 minutes

print(f"Starting experiment: {unique_id}")
print(f"Ready subscribers: {num_ready_subscribers}, TX subscribers: {num_tx_subscribers}")

current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
parent_path = os.path.dirname(current_dir)
os.makedirs(parent_path + "/data", exist_ok=True)
output_path = os.path.join(parent_path, f"data/exp-{unique_id}.yml")

with open(output_path, "w") as f:
    # 写实验元数据
    f.write(f"experiment: {unique_id}\n")
    f.write(f"num_ready_subscribers: {num_ready_subscribers}\n")
    f.write(f"num_tx_subscribers: {num_tx_subscribers}\n")
    f.write(f"measurments:\n")

    while True:
        # ======================== 第一阶段：ALIVE/READY ========================
        print(f"Waiting for {num_ready_subscribers} subscribers to send a READY message...")

        f.write(f"  - meas_id: {meas_id}\n")
        f.write("    active_tiles:\n")

        messages_received = 0

        while messages_received < num_ready_subscribers:
            socks = dict(poller.poll(1000))

            if messages_received > 2 and time.time() - new_msg_received > WAIT_TIMEOUT:
                print("Timeout while waiting for READY messages, break this measurement.")
                break

            if alive_socket in socks and socks[alive_socket] == zmq.POLLIN:
                new_msg_received = time.time()

                message = alive_socket.recv_string()
                messages_received += 1

                print(f"[READY] {message} ({messages_received}/{num_ready_subscribers})")
                f.write(f"     - {message}\n")

                response = "READY_ACK"
                alive_socket.send_string(response)

        print(f"sending 'SYNC' message in {delay}s...")
        f.flush()
        time.sleep(delay)

        meas_id += 1

        sync_socket.send_string(f"{meas_id} {unique_id}")
        print(f"SYNC {meas_id}")

        # ======================== 第二阶段：TX MODE ========================
        print(f"Waiting for {num_tx_subscribers} subscribers to send a TX Mode ...")

        messages_received = 0

        while messages_received < num_tx_subscribers:
            socks = dict(poller.poll(1000))

            if messages_received > 2 and time.time() - new_msg_received > WAIT_TIMEOUT:
                print("Timeout while waiting for TX MODE messages, break this measurement.")
                break

            if alive_socket in socks and socks[alive_socket] == zmq.POLLIN:
                new_msg_received = time.time()

                message = alive_socket.recv_string()
                messages_received += 1

                print(f"[TXMODE] {message} ({messages_received}/{num_tx_subscribers})")

                response = "TXMODE_ACK"
                alive_socket.send_string(response)

        print("Wait 10s ...")
        time.sleep(10)

        print("Measure phases")
        save_phases()
