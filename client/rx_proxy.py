#!/usr/bin/env python3
import argparse, socket
import numpy as np
import uhd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="", help="UHD device args, e.g. 'serial=XXXX'")
    ap.add_argument("--freq", type=float, default=800e6)
    ap.add_argument("--rate", type=float, default=1e6)
    ap.add_argument("--gain", type=float, default=30)
    ap.add_argument("--ant", default="TX/RX")
    ap.add_argument("--pc_ip", required=True)
    ap.add_argument("--pc_port", type=int, default=6001)
    ap.add_argument("--spb", type=int, default=1024, help="samples per UDP packet")
    args = ap.parse_args()

    usrp = uhd.usrp.MultiUSRP(args.addr)
    usrp.set_rx_rate(args.rate)
    usrp.set_rx_freq(uhd.types.TuneRequest(args.freq))
    usrp.set_rx_gain(args.gain)
    usrp.set_rx_antenna(args.ant)

    st_args = uhd.usrp.StreamArgs("sc16", "sc16")
    st_args.channels = [0]
    rx_streamer = usrp.get_rx_stream(st_args)

    # Start continuous streaming
    cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    cmd.stream_now = True
    rx_streamer.issue_stream_cmd(cmd)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"[RX_PROXY] USRP RX @ {args.freq/1e6:.3f} MHz, rate={args.rate/1e6:.3f} Msps, gain={args.gain} dB -> UDP {args.pc_ip}:{args.pc_port}")

    buff = np.zeros(2 * args.spb, dtype=np.int16)  # interleaved I,Q
    md = uhd.types.RXMetadata()

    while True:
        n = rx_streamer.recv(buff, md, timeout=1.0)
        if md.error_code != uhd.types.RXMetadataErrorCode.none:
            # 丢一丢错误包，先保证不断流
            continue
        # n 是“complex samples”的数量；buff 是 int16 interleaved，所以有效字节 = 2*n*2
        payload = buff[: 2*n].tobytes()
        sock.sendto(payload, (args.pc_ip, args.pc_port))

if __name__ == "__main__":
    main()
