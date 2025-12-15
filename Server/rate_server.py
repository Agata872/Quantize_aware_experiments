import os
import zmq
from datetime import datetime

# ================== Configuration ==================
BIND_ADDR = "tcp://*:50001"      # 和客户端的 SERVER_IP/SERVER_PORT 对应
TOPIC_RATE = b"RATE"
TOPIC_CONST = b"CONST"           # 如果将来要用，可以顺便订阅上
CSV_PATH = "achievable_rate_results.csv"

# ================== Utility: ensure CSV header ==================

def ensure_csv_header(path):
    """
    如果 CSV 文件不存在，则写入表头：
    timestamp, tx_gain_db, avg_snr_db, avg_R_theo, avg_R_qpsk
    """
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("timestamp,tx_gain_db,avg_snr_db,avg_R_theoretical,avg_R_qpsk\n")
        print(f"[Server] Created new CSV with header: {path}")
    else:
        print(f"[Server] CSV file exists, will append to: {path}")

# ================== Main ==================

def main():
    # 1. 准备 CSV
    ensure_csv_header(CSV_PATH)

    # 2. 建立 ZeroMQ SUB socket
    context = zmq.Context()
    sub_socket = context.socket(zmq.SUB)
    sub_socket.bind(BIND_ADDR)

    # 订阅 RATE 和 CONST 两种 topic（后者可选）
    sub_socket.setsockopt(zmq.SUBSCRIBE, TOPIC_RATE)
    sub_socket.setsockopt(zmq.SUBSCRIBE, TOPIC_CONST)

    print(f"[Server] Listening on {BIND_ADDR}")
    print(f"[Server] Subscribed topics: {TOPIC_RATE}, {TOPIC_CONST}")
    print(f"[Server] Writing RATE results to: {CSV_PATH}")

    try:
        while True:
            # 按照 client 端的 send_multipart([topic, payload]) 接收
            topic, payload = sub_socket.recv_multipart()

            if topic == TOPIC_RATE:
                # payload 是类似 "40.0,12.3456,3.2100,2.0000"
                line = payload.decode("utf-8").strip()
                now = datetime.now().isoformat(timespec="seconds")

                print(f"[Server][RATE] Received: {line}")

                # 解析一下，顺便做个简单检查
                parts = line.split(",")
                if len(parts) != 4:
                    print(f"[Server][WARN] RATE line has {len(parts)} fields, expected 4, skipping.")
                    continue

                tx_gain_db, avg_snr_db, avg_R_theo, avg_R_qpsk = parts

                # 追加到 CSV，加上 timestamp 一列
                with open(CSV_PATH, "a", encoding="utf-8") as f:
                    f.write(f"{now},{tx_gain_db},{avg_snr_db},{avg_R_theo},{avg_R_qpsk}\n")

                print(f"[Server][RATE] Appended to CSV at {now}")

            elif topic == TOPIC_CONST:
                # 如果你不需要在 server 端处理星座，这里可以直接忽略
                # 或者简单打印一下收到的大小
                print(f"[Server][CONST] Received constellation block of {len(payload)} bytes (ignored).")

    except KeyboardInterrupt:
        print("\n[Server] Interrupted by user, exiting...")

    finally:
        sub_socket.close()
        context.term()
        print("[Server] Cleaned up ZeroMQ context.")

if __name__ == "__main__":
    main()
