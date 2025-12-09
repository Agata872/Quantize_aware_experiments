import numpy as np
import uhd                       # <<=== Using UHD to control B210
import matplotlib.pyplot as plt
import time
import os
import zmq



# ================== Configuration ==================
SERVER_IP = "192.108.2.61"   # Change to your server IP
SERVER_PORT = 50001
TOPIC = b"CONST"
NOISE_COUNT_THRESHOLD = 10
fs = 1e6          # Sampling rate (must match transmitter)
fc = 920e6        # Center frequency: 920 MHz
sps = 4           # Samples per symbol (must match transmitter oversampling)
SAVE_FIGURES = False       # Save constellation figures or not
OUTPUT_DIR = "received_constellations"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEVICE_ARGS = "type=b200"  # B210 is part of the B200 series; add serial/addr if multiple units
RX_CHANNEL = 1
RX_GAIN = 30.0             # Moderate gain; tune depending on environment

# ================== Receive signal (B210) ==================

def receive_signal(fs=1e6, fc=920e6, num_samples=200000, noise_threshold=30.0):
    """
    Receive num_samples IQ samples using B210 + UHD.
    Follows the same start_cont / stop_cont style used in your previous project.
    """
    try:
        print("Creating USRP (B210) device for RX...")
        usrp = uhd.usrp.MultiUSRP(DEVICE_ARGS)

        # Basic configuration
        usrp.set_rx_rate(fs, RX_CHANNEL)
        usrp.set_rx_freq(fc, RX_CHANNEL)
        usrp.set_rx_gain(RX_GAIN, RX_CHANNEL)

        print(f"RX rate       : {usrp.get_rx_rate(RX_CHANNEL)} Hz")
        print(f"RX center freq: {usrp.get_rx_freq(RX_CHANNEL)} Hz")
        print(f"RX gain       : {usrp.get_rx_gain(RX_CHANNEL)} dB")

        # Create RX streamer
        st_args = uhd.usrp.StreamArgs("fc32", "sc16")
        st_args.channels = [RX_CHANNEL]
        rx_streamer = usrp.get_rx_stream(st_args)

        num_channels = rx_streamer.get_num_channels()  # Should be 1
        max_samps_per_packet = rx_streamer.get_max_num_samps()
        print(f"Num channels         : {num_channels}")
        print(f"Max samps per packet : {max_samps_per_packet}")

        # Receive buffer: shape (num_channels, max_samps_per_packet)
        recv_buffer = np.zeros((num_channels, max_samps_per_packet), dtype=np.complex64)
        rx_md = uhd.types.RXMetadata()

        # Send start_cont command — same style as your old project
        stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        stream_cmd.stream_now = True           # Start streaming immediately
        # No time_spec needed for simplicity
        rx_streamer.issue_stream_cmd(stream_cmd)

        print("Receiving signal...")
        rx_signal = np.zeros(num_samples, dtype=np.complex64)
        num_rx = 0
        timeout = 1.0  # seconds

        while num_rx < num_samples:
            samps = rx_streamer.recv(recv_buffer, rx_md, timeout)
            if rx_md.error_code != uhd.types.RXMetadataErrorCode.none:
                print("RX metadata error:", rx_md.strerror())
                break

            if samps > 0:
                # Use only the configured channel (index 0)
                take = min(samps, num_samples - num_rx)
                rx_signal[num_rx:num_rx + take] = recv_buffer[0, :take]
                num_rx += take
            else:
                print("Received 0 samples in this packet, stopping.")
                break

        # Stop continuous receiving
        stop_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        rx_streamer.issue_stream_cmd(stop_cmd)

        if num_rx == 0:
            print("No samples received at all.")
            return np.zeros(num_samples, dtype=np.complex64), -100.0

        rx_signal = rx_signal[:num_rx]
        print(f"Total samples received: {num_rx}")
        print("First 10 samples:", rx_signal[:10])

        power_db = 10 * np.log10(np.mean(np.abs(rx_signal) ** 2) + 1e-10)
        print(f"Signal Power: {power_db:.2f} dB")

        if power_db < noise_threshold:
            print(f"No strong signal detected (Power: {power_db:.2f} dB).")
        else:
            print(f"Signal detected! Power: {power_db:.2f} dB")

        return rx_signal, power_db

    except Exception as e:
        print(f"Error receiving signal: {e}")
        return np.zeros(num_samples, dtype=np.complex64), -100.0




# ================== Plot functions ==================

def plot_psd(signal, fs, title="Power Spectral Density (PSD)"):
    plt.figure(figsize=(10, 4))
    plt.psd(signal, NFFT=1024, Fs=fs, scale_by_freq=True)
    plt.title(title)
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Power/Frequency (dB/Hz)")
    plt.grid()
    plt.show()

# ================== Coarse frequency sync ==================

def coarse_frequency_sync(signal, fs):
    signal_power4 = signal ** 4
    fft_result = np.fft.fftshift(np.fft.fft(signal_power4))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(signal_power4), d=1/fs))
    peak_freq = freqs[np.argmax(np.abs(fft_result))] / 4
    print(f"Estimated frequency offset: {peak_freq:.2f} Hz")
    t = np.arange(len(signal)) / fs
    corrected_signal = signal * np.exp(-1j * 2 * np.pi * peak_freq * t)
    return corrected_signal

# ================== Mueller and Muller Clock Recovery ==================

def mueller_muller_clock_recovery(samples, sps=2):
    mu = 0.0
    out = np.zeros(len(samples) + 10, dtype=np.complex64)
    out_rail = np.zeros(len(samples) + 10, dtype=np.complex64)
    i_in = 0
    i_out = 2
    while i_out < len(out) and i_in + 16 < len(samples):
        out[i_out] = samples[i_in]
        out_rail[i_out] = (np.sign(out[i_out].real) + 1j * np.sign(out[i_out].imag))
        x = (out_rail[i_out] - out_rail[i_out - 2]) * np.conj(out[i_out - 1])
        y = (out[i_out] - out[i_out - 2]) * np.conj(out_rail[i_out - 1])
        mm_val = np.real(y - x)
        mu += sps + 0.3 * mm_val
        i_in += int(np.floor(mu))
        mu -= np.floor(mu)
        i_out += 1
    out = out[2:i_out]
    return out

# ================== 4th Order Costas Loop ==================

def phase_detector_4(sample):
    a = 1.0 if sample.real > 0 else -1.0
    b = 1.0 if sample.imag > 0 else -1.0
    return a * sample.imag - b * sample.real

def costas_loop_4th_order(signal, fs, sps=4, loop_bandwidth=0.01, damping=0.707):
    fs = fs / sps  # Adjust sampling frequency after timing sync
    N = len(signal)
    phase = 0.0
    freq = 0.0
    alpha = loop_bandwidth
    beta = loop_bandwidth ** 2 / 4
    out = np.zeros(N, dtype=np.complex64)
    for i in range(N):
        out[i] = signal[i] * np.exp(-1j * phase)
        error = phase_detector_4(out[i])
        freq += beta * error
        phase += freq + alpha * error
        while phase >= 2 * np.pi:
            phase -= 2 * np.pi
        while phase < 0:
            phase += 2 * np.pi
    print("Costas Loop Fine Frequency Synchronization Completed.")
    return out

# ================== Plot all constellations together ==================

def plot_all_constellations(signals_dict, save_name=None):
    fig, axs = plt.subplots(2, 2, figsize=(12, 12))
    fig.suptitle("QPSK Constellations at Different Stages", fontsize=16)

    for ax, (stage, signal_stage) in zip(axs.flatten(), signals_dict.items()):
        ax.scatter(signal_stage.real, signal_stage.imag, s=5, color="blue", alpha=0.7)
        lim = max(2, np.max(np.abs(signal_stage)) * 1.2)
        ax.set_xlim([-lim, lim])
        ax.set_ylim([-lim, lim])
        ax.axhline(0, color="black", lw=0.5)
        ax.axvline(0, color="black", lw=0.5)
        ax.set_title(stage)
        ax.grid()
        ax.set_xlabel("In-phase")
        ax.set_ylabel("Quadrature")

    plt.tight_layout(rect=[0, 0, 1, 0.96])  # Leave space for main title
    if save_name:
        plt.savefig(save_name)
        print(f"Saved constellation figure: {save_name}")
    plt.show()

def init_rx(fs, fc):
    print("Creating USRP (B210) device for RX...")
    usrp = uhd.usrp.MultiUSRP(DEVICE_ARGS)

    usrp.set_rx_rate(fs, RX_CHANNEL)
    usrp.set_rx_freq(fc, RX_CHANNEL)
    usrp.set_rx_gain(RX_GAIN, RX_CHANNEL)

    print(f"RX rate       : {usrp.get_rx_rate(RX_CHANNEL)} Hz")
    print(f"RX center freq: {usrp.get_rx_freq(RX_CHANNEL)} Hz")
    print(f"RX gain       : {usrp.get_rx_gain(RX_CHANNEL)} dB")

    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = [RX_CHANNEL]
    rx_streamer = usrp.get_rx_stream(st_args)

    num_channels = rx_streamer.get_num_channels()
    max_samps_per_packet = rx_streamer.get_max_num_samps()
    print(f"Num channels         : {num_channels}")
    print(f"Max samps per packet : {max_samps_per_packet}")

    recv_buffer = np.zeros((num_channels, max_samps_per_packet), dtype=np.complex64)
    rx_md = uhd.types.RXMetadata()

    # Only send start_cont once
    stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    stream_cmd.stream_now = True
    rx_streamer.issue_stream_cmd(stream_cmd)

    return usrp, rx_streamer, recv_buffer, rx_md

def iq_block_stream(rx_streamer, recv_buffer, rx_md, block_len, timeout=1.0):
    """
    Continuously receive IQ from B210.
    Maintains an internal rolling buffer.
    Whenever block_len samples accumulate, yield one block.
    """
    num_channels = rx_streamer.get_num_channels()
    assert num_channels == 1  # Only one channel is used now

    buf = np.zeros(0, dtype=np.complex64)  # Rolling buffer

    while True:
        samps = rx_streamer.recv(recv_buffer, rx_md, timeout)
        if rx_md.error_code != uhd.types.RXMetadataErrorCode.none:
            print("RX metadata error:", rx_md.strerror())
            continue  # or break depending on desired behavior

        if samps > 0:
            new_data = recv_buffer[0, :samps]
            # Append to rolling buffer
            buf = np.concatenate([buf, new_data])

            # If enough samples for a block (or multiple blocks), output them
            while buf.size >= block_len:
                block = buf[:block_len].copy()
                buf = buf[block_len:]
                yield block
        else:
            print("Received 0 samples in this packet.")

# ================== Main loop ==================

BLOCK_LEN = 200000  # Process this many IQ samples per block

# ---- Initialize USRP + RX stream ----
usrp, rx_streamer, recv_buffer, rx_md = init_rx(fs, fc)

# ---- Initialize ZeroMQ (only created once) ----
context = zmq.Context()
pub_socket = context.socket(zmq.PUB)
pub_socket.connect(f"tcp://{SERVER_IP}:{SERVER_PORT}")
print(f"[Client] Connected to server tcp://{SERVER_IP}:{SERVER_PORT}")

capture_id = 0

try:
    for rx_signal in iq_block_stream(rx_streamer, recv_buffer, rx_md, BLOCK_LEN):

        # Compute power of this block
        power_db = 10 * np.log10(np.mean(np.abs(rx_signal) ** 2) + 1e-10)
        print(f"Block power: {power_db:.2f} dB")

        # ==== Below is your original “processing + plotting + sending” logic ====
        signals = {}
        signals["Before Sync"] = rx_signal.copy()

        # Draw PSD only when debugging (slow if used continuously)
        # plot_psd(rx_signal, fs, "PSD Before Synchronization")

        # 1. Coarse frequency offset correction
        rx_signal = coarse_frequency_sync(rx_signal, fs)
        signals["After Coarse Sync"] = rx_signal.copy()

        # 2. Mueller & Muller timing recovery
        rx_signal = mueller_muller_clock_recovery(rx_signal, sps=sps)
        rx_signal = rx_signal[~np.isnan(rx_signal)]
        fs_symbol = fs / sps
        rx_signal /= np.sqrt(np.mean(np.abs(rx_signal) ** 2) + 1e-10)
        signals["After Time Sync"] = rx_signal.copy()

        # 3. Costas Loop fine carrier synchronization
        rx_signal = costas_loop_4th_order(
            rx_signal, fs_symbol, sps=1,
            loop_bandwidth=0.05, damping=0.707
        )
        signals["After Fine Sync"] = rx_signal.copy()

        # ---- Send constellation to server ----
        try:
            sig_to_send = rx_signal.astype(np.complex64)
            pub_socket.send_multipart([TOPIC, sig_to_send.tobytes()])
            print(f"[Client] Sent {sig_to_send.size} symbols to server (block {capture_id})")
        except Exception as e:
            print(f"[Client] Failed to send constellations: {e}")

        # ---- Constellation plot (optional / debugging) ----
        if SAVE_FIGURES:
            save_path = os.path.join(OUTPUT_DIR, f"constellations_capture_{capture_id}.png")
            plot_all_constellations(signals, save_name=save_path)
        capture_id += 1

except KeyboardInterrupt:
    print("KeyboardInterrupt, stopping RX...")

finally:
    # Stop continuous receive
    stop_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
    rx_streamer.issue_stream_cmd(stop_cmd)
    print("RX stream stopped.")
