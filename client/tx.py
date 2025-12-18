#!/usr/bin/env python3
import signal
import time
import numpy as np
import zmq
import uhd
from collections import deque

# =========================================================
# ====================== CONFIG ===========================
# =========================================================

# -------- Network --------
SERVER_IP = "192.108.2.61"
ZMQ_PORT_TX = 6001

ZMQ_RCV_HWM  = 5
ZMQ_RCVTIMEO = 100    # ms

# -------- USRP --------
TX_CHANNEL = 0
TX_RATE    = 80000
TX_FREQ    = 920e6
TX_GAIN    = 50
TX_BW      = 0

# -------- Streaming --------
TX_CHUNK_SAMPS = 4096

# -------- Buffering --------
PREBUFFER_SEC = 0.2      # 300 ms pre-buffer
MAXBUFFER_SEC = 0.6     # hard limit (drop oldest)

# =========================================================

STOP = False


def sig_handler(sig, frame):
    global STOP
    STOP = True


def main():
    rx_samps = 0
    rx_bytes = 0
    t_rx_start = time.time()
    last_rx_report = t_rx_start
    
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # ---------------- ZMQ ----------------
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PULL)
    sock.setsockopt(zmq.RCVHWM, ZMQ_RCV_HWM)
    sock.setsockopt(zmq.RCVTIMEO, ZMQ_RCVTIMEO)
    sock.setsockopt(zmq.LINGER, 0)

    endpoint = f"tcp://{SERVER_IP}:{ZMQ_PORT_TX}"
    sock.connect(endpoint)
    print(f"[TX] Connected to server IQ stream: {endpoint}")

    # ---------------- USRP ----------------
    usrp = uhd.usrp.MultiUSRP()
    usrp.set_tx_rate(TX_RATE, TX_CHANNEL)
    usrp.set_tx_freq(uhd.types.TuneRequest(TX_FREQ), TX_CHANNEL)
    usrp.set_tx_gain(TX_GAIN, TX_CHANNEL)
    usrp.set_tx_antenna("TX/RX", TX_CHANNEL)

    if TX_BW > 0:
        try:
            usrp.set_tx_bandwidth(TX_BW, TX_CHANNEL)
        except Exception:
            pass

    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = [TX_CHANNEL]
    tx_streamer = usrp.get_tx_stream(st_args)

    md = uhd.types.TXMetadata()
    md.start_of_burst = False
    md.end_of_burst   = False
    md.has_time_spec  = False

    # ---------------- FIFO Buffer ----------------
    prebuffer_samps = int(PREBUFFER_SEC * TX_RATE)
    maxbuffer_samps = int(MAXBUFFER_SEC * TX_RATE)

    fifo = deque()
    fifo_samps = 0
    tx_started = False

    def fifo_push(iq):
        nonlocal fifo_samps
        fifo.append(iq)
        fifo_samps += iq.size
        while fifo_samps > maxbuffer_samps and fifo:
            old = fifo.popleft()
            fifo_samps -= old.size

    def fifo_pop(n):
        nonlocal fifo_samps
        out = np.zeros(n, dtype=np.complex64)
        filled = 0
        while filled < n and fifo:
            blk = fifo[0]
            take = min(n - filled, blk.size)
            out[filled:filled+take] = blk[:take]
            filled += take
            if take == blk.size:
                fifo.popleft()
            else:
                fifo[0] = blk[take:]
            fifo_samps -= take
        return out

    print("[TX] Streaming started (Ctrl+C to stop)")

    last_report = time.time()

    while not STOP:
        # Receive ZMQ data
        try:
            data = sock.recv()
            iq = np.frombuffer(data, dtype=np.complex64)
            if iq.size > 0:
                fifo_push(iq)

                # ====== 统计到达速率 ======
                rx_samps += iq.size
                rx_bytes += len(data)
        except zmq.Again:
            pass


        # Wait until prebuffer is filled
        if not tx_started:
            if fifo_samps >= prebuffer_samps:
                md.start_of_burst = True
                tx_started = True
                print(f"[TX] Prebuffer filled ({fifo_samps} samples), TX started")
            else:
                time.sleep(0.001)
                continue

        # Feed UHD continuously
        out = fifo_pop(TX_CHUNK_SAMPS)
        tx_streamer.send(out, md)
        md.start_of_burst = False

        # Periodic status
        now = time.time()
        if now - last_rx_report >= 1.0:
            dt = now - last_rx_report
            rate_sps = rx_samps / dt
            rate_mbps = (rx_bytes * 8) / dt / 1e6

            print(f"[TX][RX-IN] arrive_rate = {rate_sps:.0f} samp/s "
                f"({rate_mbps:.2f} Mbps)")

            rx_samps = 0
            rx_bytes = 0
            last_rx_report = now

    # Graceful stop
    try:
        md2 = uhd.types.TXMetadata()
        md2.end_of_burst = True
        md2.has_time_spec = False
        tx_streamer.send(np.zeros(0, dtype=np.complex64), md2)
    except Exception:
        pass

    print("[TX] Stopped")


if __name__ == "__main__":
    main()
