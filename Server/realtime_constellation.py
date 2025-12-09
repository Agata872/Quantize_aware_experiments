#!/usr/bin/env python3
import zmq
import numpy as np
import matplotlib.pyplot as plt

# ================== 配置 ==================
BIND_ADDR = "tcp://*:50001"   # 在 server 端监听的地址和端口
TOPIC = b"CONST"              # 订阅的主题，与客户端保持一致
DECIM = 4                     # 下采样因子，防止点太多卡 UI
MAX_POINTS = 5000             # 每次最多画多少个点
# =========================================

def main():
    # 1. 建立 ZMQ SUB
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.bind(BIND_ADDR)
    socket.setsockopt(zmq.SUBSCRIBE, TOPIC)
    print(f"[Server] Listening on {BIND_ADDR}, topic={TOPIC!r}")

    # 2. Matplotlib 实时绘图设置
    plt.ion()
    fig, ax = plt.subplots(figsize=(6, 6))
    scat = ax.scatter([], [], s=2, alpha=0.6)

    ax.set_title("Real-time QPSK Constellation (After Fine Sync)")
    ax.set_xlabel("In-phase (I)")
    ax.set_ylabel("Quadrature (Q)")
    ax.grid(True)
    ax.set_xlim(-2, 2)
    ax.set_ylim(-2, 2)
    ax.axhline(0, color="gray", lw=0.5)
    ax.axvline(0, color="gray", lw=0.5)

    print("[Server] Waiting for data...")

    try:
        while True:
            # 3. 接收一帧数据：topic + payload
            topic, payload = socket.recv_multipart()
            # 解析复基带 IQ：complex64
            data = np.frombuffer(payload, dtype=np.complex64)

            if data.size == 0:
                continue

            # 简单下采样，防止一帧太多点
            data = data[::DECIM]
            if data.size > MAX_POINTS:
                data = data[:MAX_POINTS]

            I = data.real
            Q = data.imag

            # 4. 更新散点图
            points = np.column_stack((I, Q))
            scat.set_offsets(points)

            # 自适应缩放（可关掉）
            lim = max(1.5, np.max(np.abs(data)) * 1.2)
            ax.set_xlim(-lim, lim)
            ax.set_ylim(-lim, lim)

            fig.canvas.draw()
            fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("\n[Server] Stopped by user.")
    finally:
        plt.ioff()
        plt.show()
        socket.close()
        context.term()

if __name__ == "__main__":
    main()
