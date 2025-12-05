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
import json              # NEW: for BF message
import numpy as np       # NEW: for BF computation
from datetime import datetime
from helper import *
from beamforming import compute_bf

# =============================================================================
#                           Experiment Configuration
# =============================================================================
host = "*"               # Host address to bind to. "*" means all available interfaces.
sync_port = "5557"       # Port used for synchronization messages.
alive_port = "5558"      # Port used for heartbeat/alive messages.
data_port = "5559"       # Port used for BF / data transmission.
bf_port = "5560"        # Port used for BF weight transmission.
results_port = "5561"    # NEW: Port used for DL results from Rx.py
# =============================================================================

# ------------------------ CLI 参数解析 ------------------------
# 默认：delay = 10s, 第一阶段等 5 个，第二阶段等 4 个
if len(sys.argv) >= 4:
    delay = int(sys.argv[1])
    num_ready_subscribers = int(sys.argv[2])   # 第一阶段 alive 数量
    num_tx_subscribers = int(sys.argv[3])      # 第二/三阶段 TX 相关的数量
elif len(sys.argv) == 3:
    delay = int(sys.argv[1])
    num_ready_subscribers = int(sys.argv[2])
    # 如果没给第三个参数，就默认 TX 阶段人数等于 alive 阶段
    num_tx_subscribers = num_ready_subscribers
else:
    delay = 10
    num_ready_subscribers = 2
    num_tx_subscribers = 1

# Creates a socket instance
context = zmq.Context()

# PUB: 同步 SYNC 广播
sync_socket = context.socket(zmq.PUB)
sync_socket.bind(f"tcp://{host}:{sync_port}")

# REP: READY / TXMODE 控制信息
alive_socket = context.socket(zmq.REP)
alive_socket.bind(f"tcp://{host}:{alive_port}")

# ROUTER: BF 阶段 CSI/权重交互（和客户端的 DEALER 配对）
bf_socket = context.socket(zmq.ROUTER)
bf_socket.bind(f"tcp://{host}:{bf_port}")

# NEW: ROUTER: 接收 Rx.py 发送的 DL 结果
results_socket = context.socket(zmq.ROUTER)
results_socket.bind(f"tcp://{host}:{results_port}")

# Measurement and experiment identifiers
meas_id = 0

# Unique ID for the experiment based on current UTC timestamp
unique_id = str(datetime.utcnow().strftime("%Y%m%d%H%M%S"))

# Poller for READY / TXMODE messages
poller = zmq.Poller()
poller.register(alive_socket, zmq.POLLIN)

# BF CSI
bf_poller = zmq.Poller()
bf_poller.register(bf_socket, zmq.POLLIN)

# DL results (from Rx.py)
results_poller = zmq.Poller()
results_poller.register(results_socket, zmq.POLLIN)


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

        # 这里 READY 阶段结束，准备发 SYNC
        print(f"sending 'SYNC' message in {delay}s...")
        f.flush()
        time.sleep(delay)

        meas_id += 1

        sync_socket.send_string(f"{meas_id} {unique_id}")
        print(f"SYNC {meas_id}")

        # ======================== 第二阶段：BF 计算 & 权重下发 ========================
        # 客户端此时调用 get_BF(ip, phase)，用 DEALER 连接 5560
        print(f"Waiting for CSI from {num_tx_subscribers} subscribers for BF computation...")

        bf_messages_received = 0
        bf_poller.register(bf_socket, zmq.POLLIN)

        f.write("    bf_tiles:\n")

        while bf_messages_received < num_tx_subscribers:
            socks = dict(bf_poller.poll(1000))

            if bf_messages_received > 0 and time.time() - new_msg_received > WAIT_TIMEOUT:
                print("Timeout while waiting for BF CSI messages, break this measurement.")
                break

            if bf_socket in socks and socks[bf_socket] == zmq.POLLIN:
                # ROUTER <-> DEALER：两帧 [identity, msg]
                identity, msg = bf_socket.recv_multipart()

                data = json.loads(msg.decode())
                host = data["host"]
                phase = np.array(data["csi_phase"])

                print(f"[BF] Received CSI from {host}: phase={phase}")

                bf0 = compute_bf(phase, method="mrt")

                response_bytes = json.dumps({
                    "real": float(np.real(bf0)),
                    "imag": float(np.imag(bf0)),
                }).encode()
                print("bf0 (rad) =", np.angle(bf0))
                bf_socket.send_multipart([identity, response_bytes])

                bf_messages_received += 1
                new_msg_received = time.time()

                print(f"[BF] Sent weight to {host} ({bf_messages_received}/{num_tx_subscribers})")
                f.write(f"     - {host}\n")

        f.flush()

        # ======================== 第三阶段：TX MODE（保持原逻辑） ========================
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

        # ======================== 第四阶段：接收 Rx.py 的 DL 结果 ========================
        # 非阻塞地收一波结果消息（如果有多个 Rx 节点，会收到多条）
        results_filename = os.path.join(parent_path, f"data/exp-{unique_id}-dl-results.csv")

        # 如果文件不存在，先写表头
        write_header = not os.path.exists(results_filename)

        # 给一点时间窗口（例如 5 秒）接收本轮的结果
        print("Collecting DL results from Rx nodes for up to 5 seconds ...")
        t_end = time.time() + 10.0

        while time.time() < t_end:
            socks = dict(results_poller.poll(200))  # 200 ms 一次

            if results_socket in socks and socks[results_socket] == zmq.POLLIN:
                identity, msg = results_socket.recv_multipart()
                try:
                    data = json.loads(msg.decode())
                except Exception as e:
                    print("Error decoding DL results JSON:", e)
                    continue

                host_id   = data.get("host", "unknown")
                meas      = data.get("meas_id", meas_id)
                dac_bits  = data.get("dac_bits", None)
                tx_gain   = data.get("tx_gain", None)
                rx_gain   = data.get("rx_gain", None)
                snr_db    = data.get("snr_db", None)
                rate      = data.get("rate_bpsphz", None)

                print(f"[RESULT] From {host_id}: meas={meas}, bits={dac_bits}, "
                      f"SNR={snr_db:.2f} dB, R={rate:.3f} bps/Hz")

                # 追加到 CSV
                import csv
                with open(results_filename, "a", newline="") as csvfile:
                    fieldnames = ["experiment", "meas_id", "host",
                                  "dac_bits", "tx_gain", "rx_gain",
                                  "snr_db", "rate_bpsphz"]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    if write_header:
                        writer.writeheader()
                        write_header = False

                    writer.writerow({
                        "experiment": unique_id,
                        "meas_id": meas,
                        "host": host_id,
                        "dac_bits": dac_bits,
                        "tx_gain": tx_gain,
                        "rx_gain": rx_gain,
                        "snr_db": snr_db,
                        "rate_bpsphz": rate,
                    })

            else:
                # 没有新结果，短暂 sleep 一下
                time.sleep(0.05)