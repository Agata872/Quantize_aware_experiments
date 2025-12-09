import numpy as np
import wave
import time
from scipy.signal import upfirdn
import matplotlib.pyplot as plt
import uhd  # Using UHD Python API to control USRP B210

# ===================== Baseband Processing =====================

def rrc_filter(beta, sps, num_taps):
    """
    Generate Root Raised Cosine (RRC) filter coefficients (energy-normalized).
    beta: roll-off factor
    sps: samples per symbol (oversampling rate)
    num_taps: number of filter taps (preferably odd)
    """
    T = 1.0  # Symbol duration
    t = np.arange(-num_taps // 2, num_taps // 2 + 1) / sps  # Time axis in symbol periods

    h = np.zeros_like(t, dtype=np.float64)
    for i, ti in enumerate(t):
        if np.isclose(ti, 0.0):
            # t = 0
            h[i] = (1.0 / T) * (1 + beta * (4 / np.pi - 1))
        elif beta != 0 and np.isclose(np.abs(ti), T / (4 * beta)):
            # t = ±T/(4β)
            h[i] = (beta / (T * np.sqrt(2))) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta))
            )
        else:
            # General case
            numerator = (
                np.sin(np.pi * ti * (1 - beta) / T)
                + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta) / T) / T
            )
            denominator = (
                np.pi
                * ti
                * (1 - (4 * beta * ti / T) ** 2)
            )
            h[i] = (1 / T) * numerator / denominator

    # Energy normalization
    h = h / np.sqrt(np.sum(h ** 2))
    return h


def wav_to_binary(filename, num_bits=8):
    with wave.open(filename, 'rb') as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        n_frames = wav_file.getnframes()

        frames = wav_file.readframes(n_frames)
        dtype = np.int16 if sample_width == 2 else np.uint8
        audio_data = np.frombuffer(frames, dtype=dtype)

        # Convert to mono
        if n_channels == 2:
            audio_data = audio_data.reshape(-1, 2).mean(axis=1).astype(dtype)

        audio_data = audio_data.astype(np.float32)
        audio_min, audio_max = audio_data.min(), audio_data.max()
        if audio_max - audio_min > 0:
            audio_data = ((audio_data - audio_min) / (audio_max - audio_min) * 255).astype(np.uint8)

        bitstream = ''.join(format(byte, f'0{num_bits}b') for byte in audio_data)
        return bitstream


def qpsk_modulation(bitstream, preamble_bits=None, os_factor=4, beta=0.35, num_taps=101):
    """
    QPSK baseband modulation + RRC shaping filter.
    Output is oversampled baseband IQ.
    RF upconversion is handled by USRP LO.
    """
    if preamble_bits is not None:
        bitstream = preamble_bits + bitstream

    bits = np.array([int(b) for b in bitstream], dtype=np.int8)
    if len(bits) % 2 != 0:
        bits = np.append(bits, 0)  # Add one padding bit if odd

    # Map to {-1, +1}
    odd_bits = bits[0::2]
    even_bits = bits[1::2]
    odd_bits = np.where(odd_bits == 0, -1, 1)
    even_bits = np.where(even_bits == 0, -1, 1)

    # QPSK symbols: I + jQ
    symbols = odd_bits + 1j * even_bits

    # Oversampling
    symbols_oversampled = upfirdn([1], symbols, up=os_factor)

    # RRC shaping
    h_rrc = rrc_filter(beta=beta, sps=os_factor, num_taps=num_taps)
    shaped_signal = np.convolve(symbols_oversampled, h_rrc, mode='same')

    return shaped_signal  # Baseband IQ


def plot_psd(signal, fs):
    plt.figure(figsize=(10, 5))
    plt.psd(signal, NFFT=1024, Fs=fs, scale_by_freq=True)
    plt.title("Power Spectral Density of Transmitted Signal")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Power/Frequency (dB/Hz)")
    plt.grid()
    plt.savefig("transmitted_signal_psd.png")
    plt.show()


def plot_constellation(signal, os_factor=4):
    filter_delay = 50  # Approximate FIR delay
    signal_downsampled = signal[filter_delay::os_factor]

    plt.figure(figsize=(6, 6))
    plt.plot(np.real(signal_downsampled), np.imag(signal_downsampled),
             'o', markersize=2, alpha=0.5, label='Transmitted Symbols')

    ideal_points = np.array([
        [-1, -1],
        [-1,  1],
        [ 1, -1],
        [ 1,  1]
    ])
    plt.plot(ideal_points[:, 0], ideal_points[:, 1],
             'rx', markersize=10, label='Ideal QPSK Points')

    plt.grid(True)
    plt.axhline(0, color='gray', lw=0.5)
    plt.axvline(0, color='gray', lw=0.5)
    plt.xticks([-2, -1, 0, 1, 2])
    plt.yticks([-2, -1, 0, 1, 2])
    plt.xlim(-2.5, 2.5)
    plt.ylim(-2.5, 2.5)
    plt.xlabel("In-phase (I)")
    plt.ylabel("Quadrature (Q)")
    plt.title("QPSK Constellation Diagram (Baseband)")
    plt.axis('equal')
    plt.legend()
    plt.show()


def extract_bits_from_signal(modulated_signal, os_factor=4):
    """
    For local self-check only:
    Recover bits from the transmitted baseband signal.
    (This is NOT a real RX chain — only for debugging the TX pipeline.)
    """
    filter_delay = 50
    signal_downsampled = modulated_signal[filter_delay::os_factor]

    I = np.real(signal_downsampled)
    Q = np.imag(signal_downsampled)

    bits = []
    for i in range(len(signal_downsampled)):
        bits.append(1 if I[i] > 0 else 0)
        bits.append(1 if Q[i] > 0 else 0)

    return ''.join(map(str, bits))


# ===================== 1-bit DAC Quantization =====================

def quantize_1bit_dac(
    x,
    target_amp=0.7,
    add_dither=False,
    dither_std=0.3,
    rng=None
):
    """
    Simulate complex 1-bit DAC:
    - I/Q 各 1 bit: sign(Re{x}), sign(Im{x}) ∈ {−1, +1}
    - 输出映射到幅度约为 target_amp 的 QPSK 星座上: ±target_amp/√2

    x: complex np.array，任意幅度
    target_amp: 量化后最大幅度（与原代码中 0.7 保持一致）
    add_dither: 是否在量化前加抖动
    dither_std: 高斯抖动标准差（相对于归一化后的信号）
    """
    x = np.asarray(x, dtype=np.complex64)

    if rng is None:
        rng = np.random.default_rng()

    # 先做个粗归一化，避免数值非常大/非常小
    max_abs = np.max(np.abs(x)) + 1e-6
    x_norm = x / max_abs

    if add_dither:
        # 复高斯抖动，实部/虚部各 N(0, dither_std^2/2)
        d = dither_std * (
            rng.standard_normal(x.shape) + 1j * rng.standard_normal(x.shape)
        ) / np.sqrt(2.0)
        x_norm = x_norm + d

    # 1-bit 量化：I/Q 分别取符号
    q = np.sign(x_norm.real) + 1j * np.sign(x_norm.imag)

    # 映射到幅度 target_amp：|q| = target_amp
    q = (target_amp / np.sqrt(2.0)) * q

    return q.astype(np.complex64)


# ===================== USRP B210 Transmission =====================

def transmit_signal_b210(
    baseband_signal,
    bitstream,
    fs=1e6,
    fc=920e6,
    os_factor=4,
    device_args="type=b200",  # B210 belongs to the B200 series
    tx_gain=0.0,
    channel=0,
    use_1bit=False,          # <--- 新增：是否启用 1-bit DAC 量化
    add_dither=False,        # <--- 新增：量化前是否加抖动
    dither_std=0.3           # <--- 新增：抖动强度
):
    """
    Continuously transmit baseband QPSK using USRP B210 (UHD).
    Concept: similar to your previous tx_ref/tx_qpsk code,
    fill a large buffer by repeating baseband_signal and send it inside a while-loop.

    如果 use_1bit=True，则在送给 USRP 之前对基带信号做 1-bit DAC 量化。
    """
    try:
        print("Creating USRP (B210) device...")
        usrp = uhd.usrp.MultiUSRP(device_args)

        # Configure sampling rate / frequency / gain
        usrp.set_tx_rate(fs, channel)
        usrp.set_tx_freq(fc, channel)
        usrp.set_tx_gain(tx_gain, channel)

        print(f"TX rate set to {usrp.get_tx_rate(channel)} Hz")
        print(f"TX center frequency set to {usrp.get_tx_freq(channel)} Hz")
        print(f"TX gain set to {usrp.get_tx_gain(channel)} dB")

        # Create TX streamer
        st_args = uhd.usrp.StreamArgs("fc32", "sc16")
        st_args.channels = [channel]
        tx_streamer = usrp.get_tx_stream(st_args)

        max_samps_per_packet = tx_streamer.get_max_num_samps()
        print(f"Max samps per packet: {max_samps_per_packet}")

        # Baseband to complex64
        sig = baseband_signal.astype(np.complex64)

        # ==== 在这里插入 1-bit DAC 量化 ====
        if use_1bit:
            print("Using 1-bit DAC quantization at TX side...")
            sig = quantize_1bit_dac(
                sig,
                target_amp=0.7,
                add_dither=add_dither,
                dither_std=dither_std
            )
        else:
            # 原来的浮点归一化：留出 headroom，避免溢出
            sig /= (np.max(np.abs(sig)) + 1e-6)
            sig *= 0.7  # 0.7 full scale to avoid clipping
        # ==================================

        # 可选：在量化后画星座图，确认已经是 {±A/√2 ± jA/√2}
        # plot_constellation(sig, os_factor=os_factor)
        # plot_psd(sig, fs=fs)

        # Build a large buffer by repeating sig
        buf_len = 1000 * max_samps_per_packet  # Arbitrary large size
        tx_buffer = np.zeros(buf_len, dtype=np.complex64)
        for i in range(buf_len):
            tx_buffer[i] = sig[i % len(sig)]

        # TX metadata
        tx_md = uhd.types.TXMetadata()
        # Use timed transmission for first packet
        start_time = usrp.get_time_now().get_real_secs() + 0.1
        tx_md.time_spec = uhd.types.TimeSpec(start_time)
        tx_md.has_time_spec = True
        tx_md.start_of_burst = True
        tx_md.end_of_burst = False

        print("Starting continuous transmission, press Ctrl+C to stop...")
        iteration = 0

        try:
            while True:
                sent = tx_streamer.send(tx_buffer, tx_md)
                if sent != len(tx_buffer):
                    print(f"Warning: sent {sent}/{len(tx_buffer)} samples")

                # Subsequent packets do not need time_spec or start_of_burst
                tx_md.has_time_spec = False
                tx_md.start_of_burst = False

                iteration += 1
                if iteration % 100 == 0:
                    print(f"Continuous TX iterations: {iteration}")

        except KeyboardInterrupt:
            print("Transmission stopped by user.")

    except Exception as e:
        print(f"Error during transmission: {e}")
    finally:
        # Send an end-of-burst to stop transmission cleanly
        try:
            tx_md = uhd.types.TXMetadata()
            tx_md.start_of_burst = False
            tx_md.end_of_burst = True
            tx_md.has_time_spec = False
            tx_streamer.send(np.zeros(0, dtype=np.complex64), tx_md)
        except Exception:
            pass
        print("Transmission finished / cleaned up.")


# ===================== Main =====================

if __name__ == "__main__":
    # filename = "5song.wav"
    # bitstream = wav_to_binary(filename)
    bitstream = ''.join(np.random.choice(['0', '1'], size=20000))
    barker_code = '1111100110101'  # Barker preamble

    fs = 1e6       # USRP sampling rate
    fc = 920e6     # USRP RF center frequency
    os_factor = 4  # Oversampling factor -> symbol rate = fs / os_factor = 250 ksym/s

    baseband_signal = qpsk_modulation(
        bitstream,
        preamble_bits=barker_code,
        os_factor=os_factor,
        beta=0.35,
        num_taps=101
    )

    # PSD of baseband signal (before DAC quantization)
    # plot_psd(baseband_signal, fs=fs)

    # Transmit
    transmit_signal_b210(
        baseband_signal,
        bitstream,
        fs=fs,
        fc=fc,
        os_factor=os_factor,
        device_args="type=b200",
        tx_gain=10.0,   # Suggested: do not start with 70; try 20–40 first
        channel=1,      # Must match the RX antenna/port
        use_1bit=False,          # <--- 打开 1-bit DAC 量化
        add_dither=False,       # <--- 如果想测试论文里的抖动，可以设为 True
        dither_std=0.3          # <--- 抖动强度可调
    )
