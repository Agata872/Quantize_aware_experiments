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
SERVER_IP = "192.108.2.61"     # server hostname / IP
ZMQ_PORT_RX = 6002             # server ZMQ PULL (Bind) port

ZMQ_SND_HWM = 50               # ZMQ send queue depth

# -------- USRP --------
RX_CHANNEL = 0

RX_RATE = 200000                  # samples per second
RX_FREQ = 920e6                # Hz
RX_GAIN = 50                   # dB
RX_BW   = 0                    # 0 = do not set

# -------- Streaming --------
RX_BUF_SAMPS = 4096            # UHD recv buffer size
RX_TIMEOUT   = 0.1             # seconds

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
    sock = ctx.socket(zmq.PUSH)
    sock.setsockopt(zmq.SNDHWM, ZMQ_SND_HWM)
    sock.setsockopt(zmq.LINGER, 0)

    endpoint = f"tcp://{SERVER_IP}:{ZMQ_PORT_RX}"
    sock.connect(endpoint)
    print(f"[RX] Connected to server IQ sink: {endpoint}")

    # ---------------- USRP ----------------
    usrp = uhd.usrp.MultiUSRP()

    usrp.set_rx_rate(RX_RATE, RX_CHANNEL)
    usrp.set_rx_freq(uhd.types.TuneRequest(RX_FREQ), RX_CHANNEL)
    usrp.set_rx_gain(RX_GAIN, RX_CHANNEL)
    usrp.set_rx_antenna("TX/RX", RX_CHANNEL)
    if RX_BW > 0:
        try:
            usrp.set_rx_bandwidth(RX_BW, RX_CHANNEL)
        except Exception:
            pass

    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = [RX_CHANNEL]
    rx_streamer = usrp.get_rx_stream(st_args)

    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    cmd.stream_now = True
    rx_streamer.issue_stream_cmd(cmd)

    buf = np.zeros(RX_BUF_SAMPS, dtype=np.complex64)
    md = uhd.types.RXMetadata()

    print("[RX] Streaming started (Ctrl+C to stop)")

    while not STOP:
        n = rx_streamer.recv(buf, md, timeout=RX_TIMEOUT)

        if md.error_code != uhd.types.RXMetadataErrorCode.none:
            # overflow / timeout 等
            continue

        if n > 0:
            try:
                sock.send(buf[:n].tobytes(), flags=zmq.DONTWAIT, copy=False)
            except zmq.Again:
                pass

    try:
        cmd2 = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        cmd2.stream_now = True
        rx_streamer.issue_stream_cmd(cmd2)
    except Exception:
        pass

    print("[RX] Stopped")


if __name__ == "__main__":
    main()
