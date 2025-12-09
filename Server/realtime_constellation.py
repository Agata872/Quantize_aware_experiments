#!/usr/bin/env python3
import io
import threading

import numpy as np
import zmq
import matplotlib
matplotlib.use("Agg")  # 后端用 Agg，在服务器上也能画图
import matplotlib.pyplot as plt

from flask import Flask, send_file, render_template_string

# ================== 配置 ==================
BIND_ADDR = "tcp://*:50001"   # ZMQ 监听地址，客户端要 connect 到这里
TOPIC = b"CONST"              # ZMQ 主题名，要与客户端一致
DECIM = 4                     # 下采样因子（防止点太多）
MAX_POINTS = 5000             # 每帧最多点数
HTTP_HOST = "0.0.0.0"         # Flask 监听地址
HTTP_PORT = 8000              # 浏览器访问的端口
# =========================================

# 存储最新一张星座图的 PNG（二进制）
latest_png = None
png_lock = threading.Lock()

app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Real-time QPSK Constellation</title>
    <style>
        body { font-family: sans-serif; text-align: center; }
        img  { max-width: 90vw; max-height: 90vh; border: 1px solid #ccc; }
    </style>
</head>
<body>
    <h2>Real-time QPSK Constellation (After Fine Sync)</h2>
    <p>Server: {{ server_addr }}</p>
    <img id="const_img" src="/constellation.png?ts={{ ts }}" alt="constellation">

    <script>
        // 每 300ms 刷新一下图片（加时间戳防浏览器缓存）
        setInterval(function () {
            const img = document.getElementById("const_img");
            const now = Date.now();
            img.src = "/constellation.png?ts=" + now;
        }, 300);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    from time import time
    return render_template_string(
        HTML_PAGE,
        server_addr=f"{HTTP_HOST}:{HTTP_PORT}",
        ts=int(time()*1000),
    )

@app.route("/constellation.png")
def constellation_png():
    global latest_png
    with png_lock:
        img = latest_png

    # 还没任何数据时返回一张空图，避免 404
    if img is None:
        buf = io.BytesIO()
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.set_title("Waiting for data ...")
        ax.set_xlabel("I")
        ax.set_ylabel("Q")
        ax.grid(True)
        ax.set_xlim(-2, 2)
        ax.set_ylim(-2, 2)
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    return send_file(io.BytesIO(img), mimetype="image/png")


def zmq_worker():
    """
    后台线程：从 ZMQ 收 IQ，画星座，更新 latest_png。
    """
    global latest_png

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.bind(BIND_ADDR)
    socket.setsockopt(zmq.SUBSCRIBE, TOPIC)

    print(f"[Server] ZMQ SUB listening on {BIND_ADDR}, topic={TOPIC!r}")

    while True:
        try:
            topic, payload = socket.recv_multipart()
            data = np.frombuffer(payload, dtype=np.complex64)
            if data.size == 0:
                continue

            # 下采样，限制点数
            data = data[::DECIM]
            if data.size > MAX_POINTS:
                data = data[:MAX_POINTS]

            I = data.real
            Q = data.imag

            # 画一张新的星座图
            fig, ax = plt.subplots(figsize=(6, 6))
            ax.scatter(I, Q, s=2, alpha=0.6)
            ax.set_xlabel("In-phase (I)")
            ax.set_ylabel("Quadrature (Q)")
            ax.grid(True)
            lim = max(1.5, np.max(np.abs(data)) * 1.2)
            ax.set_xlim(-lim, lim)
            ax.set_ylim(-lim, lim)
            ax.axhline(0, color="gray", lw=0.5)
            ax.axvline(0, color="gray", lw=0.5)
            ax.set_title("QPSK Constellation (After Fine Sync)")

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)

            with png_lock:
                latest_png = buf.getvalue()

        except Exception as e:
            print(f"[Server] ZMQ worker error: {e}")


def main():
    # 启动 ZMQ 接收线程
    t = threading.Thread(target=zmq_worker, daemon=True)
    t.start()

    print(f"[Server] HTTP server on http://{HTTP_HOST}:{HTTP_PORT}/")
    # 开启 Flask
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False)


if __name__ == "__main__":
    main()
