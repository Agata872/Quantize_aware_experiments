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
ZMQ_PORT_TX = 6001             # server ZMQ PUSH (Bind) port

ZMQ_RCV_HWM   = 5              # 小一点：不积压，保持“最新”
ZMQ_RCVTIMEO  = 100            # ms: recv 最多阻塞 100ms，便于 Ctrl+C 生效
DROP_TO_LATEST = True          # True: 每次尽量丢掉旧包，只发最新的一帧

# -------- USRP --------
TX_CHANNEL = 0
TX_RATE = 1e6
TX_FREQ = 920e6
TX_GAIN = 50
TX_BW   = 0

# -------- Streaming --------
TX_CHUNK_SAMPS = 4096

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

    # 不要积压太多；退出别卡住
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
    md.start_of_burst = True
    md.end_of_burst = False
    md.has_time_spec = False

    buf = np.zeros(TX_CHUNK_SAMPS, dtype=np.complex64)

    print("[TX] Streaming started (Ctrl+C to stop)")

    first = True
    dropped_msgs = 0
    last_print = time.time()

    while not STOP:
        # 1) recv 加超时：不让它无限阻塞，保证能响应 Ctrl+C
        try:
            data = sock.recv()
        except zmq.Again:
            # 没数据也要回到循环，给信号处理机会
            continue

        # 2) 可选：把队列里“旧的数据包”尽量清空，只保留最新（防延迟堆积）
        if DROP_TO_LATEST:
            while True:
                try:
                    data = sock.recv(flags=zmq.DONTWAIT)
                    dropped_msgs += 1
                except zmq.Again:
                    break

        iq = np.frombuffer(data, dtype=np.complex64)
        if iq.size == 0:
            continue

        idx = 0
        while idx < iq.size and not STOP:
            n = min(TX_CHUNK_SAMPS, iq.size - idx)
            buf[:n] = iq[idx:idx+n]

            md.start_of_burst = first
            first = False

            # 3) 发送
            tx_streamer.send(buf[:n], md)
            idx += n

        # 打印丢包统计（如果启用了 DROP_TO_LATEST）
        now = time.time()
        if now - last_print > 2.0:
            if dropped_msgs:
                print(f"[TX] Dropped {dropped_msgs} stale ZMQ messages in last 2s (keeping latest)")
            dropped_msgs = 0
            last_print = now

    # graceful stop：发 EOB
    try:
        md2 = uhd.types.TXMetadata()
        md2.start_of_burst = False
        md2.end_of_burst = True
        md2.has_time_spec = False
        tx_streamer.send(np.zeros(0, dtype=np.complex64), md2)
    except Exception:
        pass

    print("[TX] Stopped")


if __name__ == "__main__":
    main()
