#!/usr/bin/env python3
import signal
import time
import numpy as np
import zmq
import uhd

# =========================================================
# ====================== CONFIG ===========================
# =========================================================

# -------- Network --------
SERVER_IP = "192.168.2.61"     # server hostname / IP
ZMQ_PORT_TX = 6001             # server ZMQ PUSH (Bind) port

ZMQ_RCV_HWM = 50               # ZMQ receive queue depth

# -------- USRP --------
TX_CHANNEL = 0

TX_RATE = 1e6                  # samples per second
TX_FREQ = 920e6                # Hz
TX_GAIN = 50                   # dB
TX_BW   = 0                    # 0 = do not set

# -------- Streaming --------
TX_CHUNK_SAMPS = 4096          # samples per UHD send()

# =========================================================

STOP = False


def sig_handler(sig, frame):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # ---------------- ZMQ ----------------
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PULL)
    sock.setsockopt(zmq.RCVHWM, ZMQ_RCV_HWM)
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
    md.start_of_burst = True
    md.end_of_burst = False
    md.has_time_spec = False

    buf = np.zeros(TX_CHUNK_SAMPS, dtype=np.complex64)

    print("[TX] Streaming started (Ctrl+C to stop)")

    first = True
    while not STOP:
        try:
            data = sock.recv()
        except zmq.error.Again:
            continue

        iq = np.frombuffer(data, dtype=np.complex64)
        idx = 0

        while idx < iq.size and not STOP:
            n = min(TX_CHUNK_SAMPS, iq.size - idx)
            buf[:n] = iq[idx:idx+n]

            md.start_of_burst = first
            first = False

            tx_streamer.send(buf[:n], md)
            idx += n

    # graceful stop
    try:
        md2 = uhd.types.TXMetadata()
        md2.end_of_burst = True
        tx_streamer.send(np.zeros(0, dtype=np.complex64), md2)
    except Exception:
        pass

    print("[TX] Stopped")


if __name__ == "__main__":
    main()
