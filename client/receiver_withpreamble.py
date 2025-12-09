import numpy as np
import uhd                       # <<=== Using UHD to control B210
import matplotlib.pyplot as plt
import time
import os
import zmq

# ================== Configuration ==================
SERVER_IP = "192.108.2.61"   # Change to your server IP
SERVER_PORT = 50001

TOPIC_CONST = b"CONST"       # 保留星座发送的 topic（如需要）
TOPIC_RATE  = b"RATE"        # 新增：发送平均可达速率的 topic

NOISE_COUNT_THRESHOLD = 10
fs = 1e6          # Sampling rate (must match transmitter)
fc = 920e6        # Center frequency: 920 MHz
sps = 4           # Samples per symbol (must match transmitter oversampling)
SAVE_FIGURES = False       # Save constellation figures or not
OUTPUT_DIR = "received_constellations"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEVICE_ARGS = "type=b200"  # B210 is part of the B200 series; add serial/addr if multiple units
RX_CHANNEL = 1
RX_GAIN = 50.0             # RX 增益

# *** 请在每次实验前把这个值改成当前 TX 端使用的增益(dB) ***
TX_GAIN_DB = 60.0          # <<< 你手动改，比如这次发射端设置 40 dB，就写 40.0

# 每个 TX 增益下要重复多少次测量
N_MEAS = 100

# ================== Receive signal (B210) ==================

def receive_signal(fs=1e6, fc=920e6, num_samples=200000, noise_threshold=30.0):
    """
    单次拉取 num_samples 个 IQ（旧函数，主流程现在用 iq_block_stream，可保留以备用）
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

    plt.tight_layout(rect=[0, 0, 1, 0.96])
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
    rx_streamer = usrp.get_tx_stream if False else usrp.get_rx_stream(st_args)  # 只是防止误改提示

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
            continue

        if samps > 0:
            new_data = recv_buffer[0, :samps]
            buf = np.concatenate([buf, new_data])

            while buf.size >= block_len:
                block = buf[:block_len].copy()
                buf = buf[block_len:]
                yield block
        else:
            print("Received 0 samples in this packet.")

def estimate_snr_and_rate(rx_syms):
    """
    输入: rx_syms 为符号速率上的 QPSK 符号（Costas + 时间同步后）
    返回: snr_lin, snr_db, R_bps_per_Hz
    """
    rx_syms = rx_syms.astype(np.complex64)
    rx_syms = rx_syms[~np.isnan(rx_syms)]
    if rx_syms.size == 0:
        return 0.0, -np.inf, 0.0

    rx_syms = rx_syms / np.sqrt(np.mean(np.abs(rx_syms) ** 2) + 1e-12)

    dec_syms = np.sign(rx_syms.real) + 1j * np.sign(rx_syms.imag)
    dec_syms.real[dec_syms.real == 0] = 1.0
    dec_syms.imag[dec_syms.imag == 0] = 1.0

    noise = rx_syms - dec_syms
    sig_power = np.mean(np.abs(dec_syms) ** 2)   # ~2
    noise_power = np.mean(np.abs(noise) ** 2) + 1e-12

    snr_lin = sig_power / noise_power
    snr_db = 10 * np.log10(snr_lin)
    R_bps_per_Hz = np.log2(1.0 + snr_lin)
    return snr_lin, snr_db, R_bps_per_Hz

# ================== Main loop ==================

BLOCK_LEN = 200000  # 每次处理的 IQ 数量

# ---- Initialize USRP + RX stream ----
usrp, rx_streamer, recv_buffer, rx_md = init_rx(fs, fc)

# ---- Initialize ZeroMQ ----
context = zmq.Context()
pub_socket = context.socket(zmq.PUB)
pub_socket.connect(f"tcp://{SERVER_IP}:{SERVER_PORT}")
print(f"[Client] Connected to server tcp://{SERVER_IP}:{SERVER_PORT}")

capture_id = 0

snr_list = []
R_theo_list = []
R_qpsk_list = []

try:
    for rx_signal in iq_block_stream(rx_streamer, recv_buffer, rx_md, BLOCK_LEN):

        print(f"=== Capture {capture_id+1}/{N_MEAS} ===")

        power_db = 10 * np.log10(np.mean(np.abs(rx_signal) ** 2) + 1e-10)
        print(f"Block power: {power_db:.2f} dB")

        signals = {}
        signals["Before Sync"] = rx_signal.copy()

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

        # 4. SNR & Achievable Rate estimation
        snr_lin, snr_db, R_bps_per_Hz = estimate_snr_and_rate(rx_signal)
        R_qpsk_max = min(2.0, R_bps_per_Hz)

        print(f"[RATE] Estimated SNR: {snr_db:.2f} dB, "
              f"R_theoretical ≈ {R_bps_per_Hz:.3f} bit/s/Hz")
        print(f"[RATE] QPSK-constrained max rate ≈ {R_qpsk_max:.3f} bit/s/Hz")

        snr_list.append(snr_db)
        R_theo_list.append(R_bps_per_Hz)
        R_qpsk_list.append(R_qpsk_max)

        # 如果你还想看星座，可以继续发到 server（可选）
        try:
            sig_to_send = rx_signal.astype(np.complex64)
            pub_socket.send_multipart([TOPIC_CONST, sig_to_send.tobytes()])
            print(f"[Client] Sent {sig_to_send.size} symbols to server (block {capture_id})")
        except Exception as e:
            print(f"[Client] Failed to send constellations: {e}")

        if SAVE_FIGURES:
            save_path = os.path.join(OUTPUT_DIR, f"constellations_capture_{capture_id}.png")
            plot_all_constellations(signals, save_name=save_path)

        capture_id += 1

        # ---- 收够 N_MEAS 次后，计算平均速率并发给 server ----
        if capture_id >= N_MEAS:
            avg_snr = float(np.mean(snr_list))
            avg_R_theo = float(np.mean(R_theo_list))
            avg_R_qpsk = float(np.mean(R_qpsk_list))

            print("======================================")
            print(f"[SUMMARY] TX_GAIN_DB = {TX_GAIN_DB:.1f} dB")
            print(f"[SUMMARY] Avg SNR       = {avg_snr:.2f} dB")
            print(f"[SUMMARY] Avg R_theo    = {avg_R_theo:.3f} bit/s/Hz")
            print(f"[SUMMARY] Avg R_QPSKmax = {avg_R_qpsk:.3f} bit/s/Hz")
            print("======================================")

            # 组成一行 CSV 文本: tx_gain_db, avg_snr_db, avg_R_theo, avg_R_qpsk
            csv_line = f"{TX_GAIN_DB:.1f},{avg_snr:.4f},{avg_R_theo:.4f},{avg_R_qpsk:.4f}"
            try:
                pub_socket.send_multipart([TOPIC_RATE, csv_line.encode("utf-8")])
                print(f"[Client] Sent averaged rate line to server: {csv_line}")
            except Exception as e:
                print(f"[Client] Failed to send averaged rate: {e}")

            # 做完当前 TX 增益下的 100 次测量后退出
            break

except KeyboardInterrupt:
    print("KeyboardInterrupt, stopping RX...")

finally:
    # Stop continuous receive
    stop_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
    rx_streamer.issue_stream_cmd(stop_cmd)
    print("RX stream stopped.")
