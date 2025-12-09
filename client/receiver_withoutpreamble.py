import numpy as np
import adi
import matplotlib.pyplot as plt
import time
import os

# Configuration
NOISE_COUNT_THRESHOLD = 10
fs = 1e6        # Sampling rate
fc = 2.4e9      # Center frequency
sps = 2         # Samples per symbol
SAVE_FIGURES = True  # Save constellation figures
OUTPUT_DIR = "received_constellations"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Receive signal
def receive_signal(fs=1e6, fc=2.4e9, num_samples=200000, noise_threshold=30):
    try:
        sdr = adi.Pluto("ip:192.168.2.1")
        sdr.sample_rate = int(fs)
        sdr.rx_lo = int(fc)
        sdr.rx_rf_bandwidth = int(fs * 2)
        sdr.gain_control_mode = "manual"
        sdr.rx_hardwaregain = 30

        print("Receiving signal...")
        rx_signal = np.concatenate([sdr.rx() for _ in range(5)])
        power_db = 10 * np.log10(np.mean(np.abs(rx_signal) ** 2) + 1e-10)

        if power_db < noise_threshold:
            print(f"No strong signal detected (Power: {power_db:.2f} dB).")
        else:
            print(f"Signal detected! Power: {power_db:.2f} dB")

        del sdr
        return rx_signal, power_db

    except Exception as e:
        print(f"Error receiving signal: {e}")
        return np.zeros(num_samples, dtype=np.complex64), -100

# Plot functions
def plot_psd(signal, fs, title="Power Spectral Density (PSD)"):
    plt.figure(figsize=(10, 4))
    plt.psd(signal, NFFT=1024, Fs=fs, scale_by_freq=True)
    plt.title(title)
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Power/Frequency (dB/Hz)")
    plt.grid()
    plt.show()

# Coarse frequency sync
def coarse_frequency_sync(signal, fs):
    signal_power4 = signal ** 4
    fft_result = np.fft.fftshift(np.fft.fft(signal_power4))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(signal_power4), d=1/fs))
    peak_freq = freqs[np.argmax(np.abs(fft_result))] / 4
    print(f"Estimated frequency offset: {peak_freq:.2f} Hz")
    t = np.arange(len(signal)) / fs
    corrected_signal = signal * np.exp(-1j * 2 * np.pi * peak_freq * t)
    return corrected_signal

# Mueller and Muller Clock Recovery
def mueller_muller_clock_recovery(samples, sps=2):
    mu = 0
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

# 4th Order Costas Loop
def phase_detector_4(sample):
    a = 1.0 if sample.real > 0 else -1.0
    b = 1.0 if sample.imag > 0 else -1.0
    return a * sample.imag - b * sample.real

def costas_loop_4th_order(signal, fs, sps=1, loop_bandwidth=0.01, damping=0.707):
    fs = fs / sps  # Adjust sampling frequency after time sync
    N = len(signal)
    phase = 0
    freq = 0
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

# Plot all constellations together
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

    plt.tight_layout(rect=[0, 0, 1, 0.96])  # leave space for main title
    if save_name:
        plt.savefig(save_name)
        print(f"Saved constellation figure: {save_name}")
    plt.show()

# Main loop
noise_count = 0
capture_id = 0

while True:
    rx_signal, power_db = receive_signal(fs, fc)
    if power_db < 30:
        noise_count += 1
        print(f"Noise detected {noise_count}/{NOISE_COUNT_THRESHOLD} times.")
    else:
        noise_count = 0

    # Save signals at different stages
    signals = {}

    # 1. Before Sync
    signals["Before Sync"] = rx_signal.copy()
    plot_psd(rx_signal, fs, "PSD Before Synchronization")

    # 2. After Coarse Frequency Sync
    rx_signal = coarse_frequency_sync(rx_signal, fs)
    signals["After Coarse Sync"] = rx_signal.copy()
    plot_psd(rx_signal, fs, "PSD After Coarse Frequency Sync")

    # 3. After Time Sync (M&M)
    rx_signal = mueller_muller_clock_recovery(rx_signal, sps=sps)
    rx_signal = rx_signal[~np.isnan(rx_signal)]  # Remove NaNs if any
    fs_symbol = fs / sps
    rx_signal /= np.sqrt(np.mean(np.abs(rx_signal) ** 2))  # Normalize power
    signals["After Time Sync"] = rx_signal.copy()
    plot_psd(rx_signal, fs_symbol, "PSD After Time Sync")

    # 4. After Fine Frequency Sync (Costas Loop)
    rx_signal = costas_loop_4th_order(rx_signal, fs_symbol, sps=1, loop_bandwidth=0.01, damping=0.707)
    signals["After Fine Sync"] = rx_signal.copy()
    plot_psd(rx_signal, fs_symbol, "PSD After Fine Frequency Sync")

    # Plot and optionally save all constellations
    save_path = None
    if SAVE_FIGURES:
        save_path = os.path.join(OUTPUT_DIR, f"constellations_capture_{capture_id}.png")
        capture_id += 1

    plot_all_constellations(signals, save_name=save_path)

    if noise_count >= NOISE_COUNT_THRESHOLD:
        print("Noise detected too many times. Stopping reception...")
        break

    time.sleep(1)
