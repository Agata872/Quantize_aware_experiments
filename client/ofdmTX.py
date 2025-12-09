import numpy as np
import uhd
import time

# OFDM Parameters
fft_len = 64
cp_len = 16

# USRP Parameters
frequency = 920e6      # Hz
gain = 50              # dB
rate = 1e6             # S/s
duration = 1000          # seconds
TX_CHANNEL = 1         # 用 0 通道发

def generate_ofdm_symbol(fft_len, cp_len):
    symbols = np.random.choice([-1, 1], size=fft_len)
    ofdm_time = np.fft.ifft(symbols) * fft_len
    ofdm_symbol = np.concatenate([ofdm_time[-cp_len:], ofdm_time])
    return ofdm_symbol.astype(np.complex64)

def transmit_ofdm(usrp, ofdm_symbol, rate, frequency, gain, duration):
    symbol_len = len(ofdm_symbol)   # = fft_len + cp_len

    # 根据 duration 计算需要发送多少个符号
    num_symbols = int(duration * rate / symbol_len)
    print(f"symbol_len = {symbol_len}, num_symbols = {num_symbols}")

    # 配置 USRP 发射
    usrp.set_tx_antenna("TX/RX", TX_CHANNEL)
    usrp.set_tx_rate(rate, TX_CHANNEL)
    usrp.set_tx_freq(uhd.types.TuneRequest(frequency), TX_CHANNEL)
    usrp.set_tx_gain(gain, TX_CHANNEL)

    stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
    stream_args.channels = [TX_CHANNEL]
    streamer = usrp.get_tx_stream(stream_args)

    metadata = uhd.types.TXMetadata()
    metadata.start_of_burst = True
    metadata.end_of_burst = False

    for i in range(num_symbols):
        if i > 0:
            metadata.start_of_burst = False
        streamer.send(ofdm_symbol, metadata)

    metadata.start_of_burst = False
    metadata.end_of_burst = True
    streamer.send(np.zeros(symbol_len, dtype=np.complex64), metadata)

def main():
    usrp = uhd.usrp.MultiUSRP()
    ofdm_symbol = generate_ofdm_symbol(fft_len, cp_len)
    print("OFDM Symbol generated.")

    print("Starting OFDM transmission...")
    transmit_ofdm(usrp, ofdm_symbol, rate, frequency, gain, duration)

    print("TX done, sleeping 2s before exit...")
    time.sleep(2)

if __name__ == "__main__":
    main()
