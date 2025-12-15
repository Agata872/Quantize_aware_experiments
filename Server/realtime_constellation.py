#!/usr/bin/env python3
import io
import threading

import numpy as np
import zmq
import matplotlib
matplotlib.use("Agg")  # Use Agg backend so plotting works on a headless server
import matplotlib.pyplot as plt

from flask import Flask, send_file, render_template_string

# ================== Configuration ==================
BIND_ADDR = "tcp://*:50001"   # ZMQ listening address; clients must connect to this
TOPIC = b"CONST"              # ZMQ topic name; must match the client
DECIM = 4                     # Decimation factor (to avoid too many points)
MAX_POINTS = 5000             # Maximum number of points per frame
HTTP_HOST = "0.0.0.0"         # Flask listening address
HTTP_PORT = 8000              # Port for browser access
# =========================================

# Store the latest constellation PNG (binary data)
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
        // Refresh the image every 300 ms (timestamp added to avoid browser caching)
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

    # If no data has been received yet, return an empty placeholder figure instead of 404
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
    Background thread:
    Receive IQ data from ZMQ, draw the constellation, and update latest_png.
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

            # Downsample and limit the number of points
            data = data[::DECIM]
            if data.size > MAX_POINTS:
                data = data[:MAX_POINTS]

            I = data.real
            Q = data.imag

            # Draw a new constellation figure
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
    # Start the ZMQ receiving thread
    t = threading.Thread(target=zmq_worker, daemon=True)
    t.start()

    print(f"[Server] HTTP server on http://{HTTP_HOST}:{HTTP_PORT}/")
    # Start Flask HTTP server
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False)


if __name__ == "__main__":
    main()
