import numpy as np
import matplotlib.pyplot as plt
import uhd
import time
from scipy.signal import find_peaks

# OFDM Parameters
fft_len = 64
cp_len = 16
num_symbols = 10

# USRP Parameters
frequency = 920e6  # Center frequency in Hz
gain = 20              # Transmission gain in dB
rate = 1e6             # Sample rate in samples per second
duration = 10          # Duration of transmission in seconds

def receive_samples(usrp, rate, frequency, gain, num_samps):
    """
    Receive samples using the USRP.

    :param usrp: The MultiUSRP object
    :param rate: The sample rate for reception
    :param frequency: The center frequency for reception
    :param gain: The reception gain
    :param num_samps: The number of samples to receive
    """
    usrp.set_rx_rate(rate)
    usrp.set_rx_freq(uhd.types.TuneRequest(frequency))
    usrp.set_rx_gain(gain)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [0]  # specify channel number
    streamer = usrp.get_rx_stream(stream_args)

    # Start continuous streaming
    stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    stream_cmd.stream_now = True
    streamer.issue_stream_cmd(stream_cmd)

    recv_buffer = np.zeros((1, 1024), dtype=np.complex64)  # Use the same buffer size for fetching data
    samples = np.zeros(int(num_samps), dtype=np.complex64)
    metadata = uhd.types.RXMetadata()

    try:
        for i in range(int(num_samps) // 1024):
            streamer.recv(recv_buffer, metadata)
            if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                print("Error receiving samples:", metadata.strerror())
                break
            samples[i*1024:(i+1)*1024] = recv_buffer[0]
    finally:
        # Ensure the stream is properly stopped regardless of errors
        stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        streamer.issue_stream_cmd(stream_cmd)

    return samples

def calculate_snr(signal, noise_floor):
    """
    Calculate the Signal-to-Noise Ratio.

    :param signal: The received signal array
    :param noise_floor: An array segment where only noise is present
    :return: SNR in decibels
    """
    signal_power = np.mean(np.abs(signal)**2)
    noise_power = np.mean(np.abs(noise_floor)**2)
    snr = 10 * np.log10(signal_power / noise_power)
    return snr

def main():
    usrp = uhd.usrp.MultiUSRP()
    print("Receiving samples...")
    received_samples = receive_samples(usrp, rate, frequency, gain, rate * duration)

    if np.any(received_samples):
    snr = calculate_snr(received_samples, received_samples[-10000:])
    print(f"Calculated SNR: {snr:.2f} dB")

    visualize_time_domain(received_samples[:fft_len + cp_len])
    visualize_frequency_domain(received_samples[:fft_len])

    known_symbols = np.random.choice([-1, 1], size=fft_len)  # Assuming we know the original symbols
    received_symbols = np.sign(np.real(received_samples[:fft_len]))  # BPSK demodulation
    errors = np.sum(received_symbols != known_symbols)
    ber = errors / len(known_symbols)
    print(f"Bit Error Rate (BER): {ber:.5f}")
    ber = compute_ber(received_samples[:fft_len], np.random.choice([-1, 1], size=fft_len))
    print(f"Bit Error Rate (BER): {ber:.5f}")

    else:
        print("No valid samples were received. The received buffer is empty.")

if __name__ == "__main__":
    main()