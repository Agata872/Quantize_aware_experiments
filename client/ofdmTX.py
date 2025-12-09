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
RX_CHANNEL = 1          # RX channel index

def generate_ofdm_symbol(fft_len, cp_len):
    # Generate random BPSK symbols
    symbols = np.random.choice([-1, 1], size=fft_len)
    # Perform the IFFT
    ofdm_time = np.fft.ifft(symbols) * fft_len
    # Add cyclic prefix
    ofdm_symbol = np.concatenate([ofdm_time[-cp_len:], ofdm_time])
    return ofdm_symbol


def transmit_ofdm(usrp, ofdm_symbol, num_symbols, rate, frequency, gain):
    """
    Transmit OFDM symbols using the USRP.

    :param usrp: The MultiUSRP object
    :param ofdm_symbol: The OFDM symbol to transmit
    :param num_symbols: The number of OFDM symbols to transmit
    :param rate: The sample rate for transmission
    :param frequency: The center frequency for transmission
    :param gain: The transmission gain
    """
    # Configure the USRP for transmission
    usrp.set_tx_antenna("TX/RX", RX_CHANNEL)
    usrp.set_tx_rate(rate)
    usrp.set_tx_freq(uhd.types.TuneRequest(frequency))
    usrp.set_tx_gain(gain)

    # Set up a streamer
    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    streamer = usrp.get_tx_stream(stream_args)

    # Transmit the symbols
    metadata = uhd.types.TXMetadata()
    metadata.start_of_burst = True
    metadata.end_of_burst = False
    for _ in range(num_symbols):
        streamer.send(ofdm_symbol.astype(np.complex64), metadata)
    metadata.end_of_burst = True
    streamer.send(np.zeros(fft_len + cp_len, dtype=np.complex64), metadata)

def main():
    usrp = uhd.usrp.MultiUSRP()
    ofdm_symbol = generate_ofdm_symbol(fft_len, cp_len)
    print("OFDM Symbol generated.")

    print("Starting OFDM transmission...")
    transmit_ofdm(usrp, ofdm_symbol, num_symbols, rate, frequency, gain)

    time.sleep(2)  # Wait for 2 seconds to ensure the transmitter is fully operational

if __name__ == "__main__":
    main()