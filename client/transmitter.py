import numpy as np
import wave
import time
from scipy.signal import upfirdn
import matplotlib.pyplot as plt
import uhd  # 使用 UHD Python API 控制 B210

# ===================== 基带处理部分 =====================

def rrc_filter(beta, sps, num_taps):
    """
    生成 Root Raised Cosine (RRC) 滤波器系数（能量归一化）。
    beta: roll-off 系数
    sps: samples per symbol（过采样倍数）
    num_taps: 滤波器 tap 数（最好是奇数）
    """
    T = 1.0  # 符号间隔
    t = np.arange(-num_taps // 2, num_taps // 2 + 1) / sps  # 以符号周期为单位的时间

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
            # 一般情况
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

    # 能量归一化
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

        # 转单声道
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
    QPSK 基带调制 + RRC 成形滤波，输出过采样基带 IQ 信号。
    不在这里做射频上变频，交给 USRP 的 LO。
    """
    if preamble_bits is not None:
        bitstream = preamble_bits + bitstream

    bits = np.array([int(b) for b in bitstream], dtype=np.int8)
    if len(bits) % 2 != 0:
        bits = np.append(bits, 0)  # 补 1 bit 保证偶数

    # 映射到 {-1, +1}
    odd_bits = bits[0::2]
    even_bits = bits[1::2]
    odd_bits = np.where(odd_bits == 0, -1, 1)
    even_bits = np.where(even_bits == 0, -1, 1)

    # QPSK 星座：I + jQ
    symbols = odd_bits + 1j * even_bits

    # 过采样
    symbols_oversampled = upfirdn([1], symbols, up=os_factor)

    # RRC 成形滤波
    h_rrc = rrc_filter(beta=beta, sps=os_factor, num_taps=num_taps)
    shaped_signal = np.convolve(symbols_oversampled, h_rrc, mode='same')

    return shaped_signal  # 基带 IQ


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
    filter_delay = 50  # 近似 FIR 延时
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
    仅用于本地自检：从发射的基带信号中重新判决出 bits。
    （注意不是真正的接收链路，只是 check pipeline）
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


# ===================== USRP B210 发射部分 =====================

def transmit_signal_b210(
    baseband_signal,
    bitstream,
    fs=1e6,
    fc=920e6,
    os_factor=4,
    device_args="type=b200",  # B210 属于 B200 系列
    tx_gain=0.0,
    channel=0
):
    """
    使用 USRP B210 (UHD) 连续发送基带 QPSK 信号。
    思路：参考你之前工程的 tx_ref/tx_qpsk，
    把 baseband_signal 循环填满一个大 buffer，在 while True 里不断 send。
    """
    try:
        print("Creating USRP (B210) device...")
        usrp = uhd.usrp.MultiUSRP(device_args)

        # 设置采样率 / 频率 / 增益
        usrp.set_tx_rate(fs, channel)
        usrp.set_tx_freq(fc, channel)
        usrp.set_tx_gain(tx_gain, channel)

        print(f"TX rate set to {usrp.get_tx_rate(channel)} Hz")
        print(f"TX center frequency set to {usrp.get_tx_freq(channel)} Hz")
        print(f"TX gain set to {usrp.get_tx_gain(channel)} dB")

        # 创建 TX streamer
        st_args = uhd.usrp.StreamArgs("fc32", "sc16")
        st_args.channels = [channel]
        tx_streamer = usrp.get_tx_stream(st_args)

        max_samps_per_packet = tx_streamer.get_max_num_samps()
        print(f"Max samps per packet: {max_samps_per_packet}")

        # 归一化幅度，留点 headroom 避免溢出
        sig = baseband_signal.astype(np.complex64)
        sig /= (np.max(np.abs(sig)) + 1e-6)
        sig *= 0.7  # 0.7 全幅，避免裁剪

        # 构造一个大 buffer：把 sig 循环铺满
        buf_len = 1000 * max_samps_per_packet      # 随便取个比较大的长度
        tx_buffer = np.zeros(buf_len, dtype=np.complex64)
        for i in range(buf_len):
            tx_buffer[i] = sig[i % len(sig)]

        # 画一次星座/PSD 自检就行，不要在主循环里画
        # plot_constellation(sig, os_factor=os_factor)
        # plot_psd(sig, fs=fs)

        # TX Metadata
        tx_md = uhd.types.TXMetadata()
        # 第一次用定时启动，后面就不用 time_spec 了
        start_time = usrp.get_time_now().get_real_secs() + 0.1
        tx_md.time_spec = uhd.types.TimeSpec(start_time)
        tx_md.has_time_spec = True
        tx_md.start_of_burst = True
        tx_md.end_of_burst = False

        print("Starting continuous transmission, press Ctrl+C to stop...")
        iteration = 0

        try:
            while True:
                # 发送整个大 buffer
                sent = tx_streamer.send(tx_buffer, tx_md)
                if sent != len(tx_buffer):
                    print(f"Warning: sent {sent}/{len(tx_buffer)} samples")

                # 之后就不再带 time_spec / SOB 了
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
        # 发送一个 end-of-burst 告诉 USRP 停止发射
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
    # filename = "song3.wav"
    barker_code = '1111100110101'  # Barker preamble

    fs = 1e6       # USRP 采样率
    fc = 920e6     # USRP 射频中心频率
    os_factor = 4  # 过采样倍数 -> 符号率 = fs / os_factor = 250 ksym/s

    # bitstream = wav_to_binary(filename)
    bitstream = ''.join(np.random.choice(['0', '1'], size=20000))
    baseband_signal = qpsk_modulation(
        bitstream,
        preamble_bits=barker_code,
        os_factor=os_factor,
        beta=0.35,
        num_taps=101
    )

    # PSD 画的是基带谱
    # plot_psd(baseband_signal, fs=fs)

    # 发射
    transmit_signal_b210(
        baseband_signal,
        bitstream,
        fs=fs,
        fc=fc,
        os_factor=os_factor,
        device_args="type=b200",
        tx_gain=40.0,   # 建议先不要 70，先 20~40 试
        channel=1       # 这里要和 RX 用的通道/天线口对应
    )
