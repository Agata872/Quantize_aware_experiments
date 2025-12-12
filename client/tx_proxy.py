#!/usr/bin/env python3
import argparse, socket, time
import numpy as np
import uhd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="", help="UHD device args, e.g. 'serial=XXXX'")
    ap.add_argument("--freq", type=float, default=800e6)
    ap.add_argument("--rate", type=float, default=1e6)
    ap.add_argument("--gain", type=float, default=30)
    ap.add_argument("--ant", default="TX/RX")
    ap.add_argument("--udp_bind", default="0.0.0.0")
    ap.add_argument("--udp_port", type=int, default=5001)
    ap.add_argument("--spb", type=int, default=1024, help="samples per UDP packet")
    args = ap.parse_args()

    usrp = uhd.usrp.MultiUSRP(args.addr)
    usrp.set_tx_rate(args.rate)
    usrp.set_tx_freq(uhd.types.TuneRequest(args.freq))
    usrp.set_tx_gain(args.gain)
    usrp.set_tx_antenna(args.ant)

    st_args = uhd.usrp.StreamArgs("sc16", "sc16")
    st_args.channels = [0]
    tx_streamer = usrp.get_tx_stream(st_args)

    md = uhd.types.TXMetadata()
    md.start_of_burst = True
    md.end_of_burst = False
    md.has_time_spec = False

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.udp_bind, args.udp_port))
    sock.settimeout(1.0)

    print(f"[TX_PROXY] Listening UDP {args.udp_bind}:{args.udp_port} -> USRP TX @ {args.freq/1e6:.3f} MHz, rate={args.rate/1e6:.3f} Msps, gain={args.gain} dB")

    # Buffer for UHD send: complex int16
    # UHD Python expects numpy complex64 usually, but for sc16 it accepts int16 interleaved via view.
    # We'll build a (spb,) complex64 then view as int16 pairs? Safer: build complex64 then cast by UHD? Not ideal.
    # Better: interpret incoming bytes as int16 and pack into numpy int16 interleaved and pass as is.
    # UHD python accepts a numpy array of dtype=np.int16 with shape (2*spb,) for sc16.
    while True:
        try:
            data, _ = sock.recvfrom(args.spb * 4)
        except socket.timeout:
            continue

        if len(data) != args.spb * 4:
            continue  # ignore partial packets for simplicity

        iq_i16 = np.frombuffer(data, dtype=np.int16)  # length = 2*spb (I,Q interleaved)
        # Send
        tx_streamer.send(iq_i16, md)
        md.start_of_burst = False

if __name__ == "__main__":
    main()
