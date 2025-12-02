#!/usr/bin/env python3
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import uhd
import yaml
import zmq
import queue
import tools

meas_id = 0
exp_id = 0
results = []

SWITCH_LOOPBACK_MODE = 0x00000006
SWITCH_RESET_MODE = 0x00000000

# Initialize ZMQ (TX task doesn't send IQ data, but initialization is retained)
context = zmq.Context()
iq_socket = context.socket(zmq.PUB)
iq_socket.bind(f"tcp://*:{50002}")

HOSTNAME = socket.gethostname()[4:]
file_open = False

with open(
    os.path.join(os.path.dirname(__file__), "cal-settings.yml"), "r", encoding="utf-8"
) as file:
    vars = yaml.safe_load(file)
    globals().update(vars)  # update the global variables with the vars in yaml

# Setup logger with custom timestamp formatting
class LogFormatter(logging.Formatter):
    @staticmethod
    def pp_now():
        now = datetime.now()
        return "{:%H:%M}:{:05.2f}".format(now, now.second + now.microsecond / 1e6)

    def formatTime(self, record, datefmt=None):
        converter = self.converter(record.created)
        if datefmt:
            formatted_date = time.strftime(datefmt, converter)
        else:
            formatted_date = LogFormatter.pp_now()
        return formatted_date

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console = logging.StreamHandler()
formatter = LogFormatter(fmt="[%(asctime)s] [%(levelname)s] (%(threadName)-10s) %(message)s")
console.setFormatter(formatter)
logger.addHandler(console)

# -------------------------------
# Initialization and setup functions (inherited from original script)
# -------------------------------
def setup_clock(usrp, clock_src, num_mboards):
    usrp.set_clock_source(clock_src)
    logger.debug("Now confirming lock on clock signals...")
    end_time = datetime.now() + timedelta(milliseconds=CLOCK_TIMEOUT)
    for i in range(num_mboards):
        is_locked = usrp.get_mboard_sensor("ref_locked", i)
        while (not is_locked) and (datetime.now() < end_time):
            time.sleep(1e-3)
            is_locked = usrp.get_mboard_sensor("ref_locked", i)
        if not is_locked:
            logger.error("Unable to confirm clock signal locked on board %d", i)
            return False
        else:
            logger.debug("Clock signals are locked")
    return True

def setup_pps(usrp, pps):
    logger.debug("Setting PPS")
    usrp.set_time_source(pps)
    return True

def print_tune_result(tune_res):
    logger.debug(
        "Tune Result:\n    Target RF  Freq: %.6f (MHz)\n Actual RF  Freq: %.6f (MHz)\n Target DSP Freq: %.6f (MHz)\n Actual DSP Freq: %.6f (MHz)\n",
        (tune_res.target_rf_freq / 1e6),
        (tune_res.actual_rf_freq / 1e6),
        (tune_res.target_dsp_freq / 1e6),
        (tune_res.actual_dsp_freq / 1e6),
    )

def tune_usrp(usrp, freq, channels, at_time):
    treq = uhd.types.TuneRequest(freq)
    usrp.set_command_time(uhd.types.TimeSpec(at_time))
    treq.dsp_freq = 0.0
    treq.target_freq = freq
    treq.rf_freq = freq
    treq.rf_freq_policy = uhd.types.TuneRequestPolicy(ord("M"))
    treq.dsp_freq_policy = uhd.types.TuneRequestPolicy(ord("M"))
    args = uhd.types.DeviceAddr("mode_n=integer")
    treq.args = args
    rx_freq = freq - 1e3
    rreq = uhd.types.TuneRequest(rx_freq)
    rreq.rf_freq = rx_freq
    rreq.target_freq = rx_freq
    rreq.dsp_freq = 0.0
    rreq.rf_freq_policy = uhd.types.TuneRequestPolicy(ord("M"))
    rreq.dsp_freq_policy = uhd.types.TuneRequestPolicy(ord("M"))
    rreq.args = uhd.types.DeviceAddr("mode_n=fractional")
    for chan in channels:
        print_tune_result(usrp.set_rx_freq(rreq, chan))
        print_tune_result(usrp.set_tx_freq(treq, chan))
    while not usrp.get_rx_sensor("lo_locked").to_bool():
        print(".")
        time.sleep(0.01)
    logger.info("RX LO is locked")
    while not usrp.get_tx_sensor("lo_locked").to_bool():
        print(".")
        time.sleep(0.01)
    logger.info("TX LO is locked")

def setup(usrp):
    rate = RATE
    mcr = 20e6
    assert (mcr / rate).is_integer(), f"The masterclock rate {mcr} should be an integer multiple of the sampling rate {rate}"
    usrp.set_master_clock_rate(mcr)
    channels = [0,1]
    setup_clock(usrp, "external", usrp.get_num_mboards())
    setup_pps(usrp, "external")
    rx_bw = 200e3
    for chan in channels:
        usrp.set_rx_rate(rate, chan)
        usrp.set_tx_rate(rate, chan)
        usrp.set_rx_dc_offset(True, chan)
        usrp.set_rx_bandwidth(rx_bw, chan)
        usrp.set_rx_agc(False, chan)
    # TX-side settings: use the specified TX channel (based on RX_TX_SAME_CHANNEL, signal is transmitted on FREE_TX_CH)
    usrp.set_tx_gain(
        PILOT_TX_GAIN, PILOT_TX_CH
    )

    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = channels
    tx_streamer = usrp.get_tx_stream(st_args)
    rx_streamer = usrp.get_rx_stream(st_args)
    tune_usrp(usrp, FREQ, channels, at_time=INIT_DELAY)
    logger.info(f"USRP tuned and setup. (Current time: {usrp.get_time_now().get_real_secs()})")
    return tx_streamer, rx_streamer

# -------------------------------
# Transmission-related functions: tx_ref, tx_thread, tx_meta_thread
# -------------------------------
# def tx_ref(usrp, tx_streamer, quit_event, phase, amplitude, start_time):
#     num_channels = tx_streamer.get_num_channels()
#     max_samps_per_packet = tx_streamer.get_max_num_samps()
#     amplitude = np.asarray(amplitude)
#     phase = np.asarray(phase)
#     sample = amplitude * np.exp(phase * 1j)
#     transmit_buffer = np.ones((num_channels, 1000 * max_samps_per_packet), dtype=np.complex64)

#     transmit_buffer[0, :] *= sample[0]
#     if num_channels > 1:
#         transmit_buffer[1, :] *= sample[1]
#     tx_md = uhd.types.TXMetadata()
#     if start_time is not None:
#         tx_md.time_spec = start_time
#     else:
#         tx_md.time_spec = uhd.types.TimeSpec(usrp.get_time_now().get_real_secs() + INIT_DELAY)
#     tx_md.has_time_spec = True
#     logger.info("TX will start at time: %.6f", tx_md.time_spec.get_real_secs())
#     try:
#         while not quit_event.is_set():
#             tx_streamer.send(transmit_buffer, tx_md)
#     except KeyboardInterrupt:
#         logger.debug("CTRL+C pressed in TX")
#     finally:
#         tx_md.end_of_burst = True
#         tx_streamer.send(np.zeros((num_channels, 0), dtype=np.complex64), tx_md)
#         logger.info("TX finished.")

def tx_ref(usrp, tx_streamer, quit_event, phase, amplitude, start_time=None):
    """
    Transmit a continuous reference signal on all active channels.

    This function generates a complex baseband signal based on the provided
    amplitude and phase values, then continuously transmits it using the
    specified USRP transmit streamer until `quit_event` is set.

    Args:
        usrp: The USRP device instance.
        tx_streamer: UHD transmit streamer used for sending samples.
        quit_event: Threading event used to stop transmission when set.
        phase (list or np.ndarray): Phase offset for each channel (in radians).
        amplitude (list or np.ndarray): Amplitude for each channel.
        start_time (uhd.types.TimeSpec, optional): Scheduled start time.
            If None, transmission begins after `INIT_DELAY` seconds.

    Notes:
        - This function continuously sends the same buffer of complex samples.
        - It is typically used to generate a reference signal for phase calibration.
    """

    # Retrieve USRP transmission parameters
    num_channels = tx_streamer.get_num_channels()
    max_samps_per_packet = tx_streamer.get_max_num_samps()

    # Convert inputs to NumPy arrays for element-wise operations
    amplitude = np.asarray(amplitude)
    phase = np.asarray(phase)

    # Compute the complex signal for each channel: A * e^(j * phi)
    sample = amplitude * np.exp(1j * phase)

    # Initialize a large transmit buffer filled with the reference signal
    transmit_buffer = np.ones(
        (num_channels, 1000 * max_samps_per_packet), dtype=np.complex64
    )
    transmit_buffer[0, :] *= sample[0]
    transmit_buffer[1, :] *= sample[1]

    # Create UHD transmit metadata (for timed transmission)
    tx_md = uhd.types.TXMetadata()

    # Schedule the transmission start time
    if start_time is not None:
        if isinstance(start_time, (int, float)):
            start_time = uhd.types.TimeSpec(
            usrp.get_time_now().get_real_secs() + float(start_time)
        )
        tx_md.time_spec = start_time
    else:
        tx_md.time_spec = uhd.types.TimeSpec(
            usrp.get_time_now().get_real_secs() + INIT_DELAY
        )

    tx_md.has_time_spec = True

    try:
        # Continuously transmit the reference signal until quit_event is triggered
        while not quit_event.is_set():
            tx_streamer.send(transmit_buffer, tx_md)

    except KeyboardInterrupt:
        logger.debug("CTRL+C detected — stopping transmission")

    finally:
        # Send an end-of-burst (EOB) packet to properly terminate streaming
        tx_md.end_of_burst = True
        tx_streamer.send(np.zeros((num_channels, 0), dtype=np.complex64), tx_md)
def tx_thread(
    usrp, tx_streamer, quit_event, phase=[0, 0], amplitude=[0.8, 0.8], start_time=None
):
    tx_thr = threading.Thread(
        target=tx_ref,
        args=(usrp, tx_streamer, quit_event, phase, amplitude, start_time),
    )
    tx_thr.name = "TX_thread"
    tx_thr.start()
    return tx_thr

def tx_async_th(tx_streamer, quit_event):
    async_metadata = uhd.types.TXAsyncMetadata()
    try:
        while not quit_event.is_set():
            if not tx_streamer.recv_async_msg(async_metadata, 0.01):
                continue
            else:
                if async_metadata.event_code != uhd.types.TXMetadataEventCode.burst_ack:
                    logger.error(async_metadata.event_code)
    except KeyboardInterrupt:
        pass

def tx_meta_thread(tx_streamer, quit_event):
    tx_meta_thr = threading.Thread(target=tx_async_th, args=(tx_streamer, quit_event))
    tx_meta_thr.name = "TX_META_thread"
    tx_meta_thr.start()
    return tx_meta_thr

def delta(usrp, at_time):
    return at_time - usrp.get_time_now().get_real_secs()

def get_current_time(usrp):
    return usrp.get_time_now().get_real_secs()

def rx_thread(usrp, rx_streamer, quit_event, duration, res, start_time=None):
    _rx_thread = threading.Thread(
        target=rx_ref,
        args=(
            usrp,
            rx_streamer,
            quit_event,
            duration,
            res,
            start_time,
        ),
    )
    _rx_thread.name = "RX_thread"
    _rx_thread.start()
    return _rx_thread

def measure_loopback(
    usrp, tx_streamer, rx_streamer, quit_event, result_queue,
    start_lb, stop_lb,
):
    # ------------------------------------------------------------
    # Function: measure_loopback
    # Purpose:
    #   This function performs a loopback measurement using a USRP device.
    #   It transmits a known signal on one channel and simultaneously
    #   receives it on another channel (loopback). The result is captured,
    #   stored, and processed later.
    # ------------------------------------------------------------

    logger.debug("########### Measure LOOPBACK ###########")

    # ------------------------------------------------------------
    # 0. Check loopback timing
    # ------------------------------------------------------------
    if stop_lb <= start_lb:
        raise ValueError(f"stop_lb ({stop_lb}) must be > start_lb ({start_lb})")

    # ------------------------------------------------------------
    # 1. Configure transmit signal amplitudes
    # ------------------------------------------------------------
    amplitudes = [0.0, 0.0]              # Initialize amplitude array for both channels
    amplitudes[LOOPBACK_TX_CH] = 0.8     # Enable TX on the selected loopback channel

    # ------------------------------------------------------------
    # 2. Set the transmission start time (START_LB)
    # ------------------------------------------------------------
    start_time = uhd.types.TimeSpec(start_lb)
    logger.debug(starting_in(usrp, start_lb))

    # ------------------------------------------------------------
    # 3. (Legacy) Access user settings interface for low-level FPGA control
    #    Used to switch the USRP into "loopback mode" by writing to
    #    a register in the user settings interface.
    #    NOTE: This interface is no longer available in UHD 4.x.
    # ------------------------------------------------------------
    user_settings = None
    try:
        user_settings = usrp.get_user_settings_iface(1)
        if user_settings:
            # Read current register value (for debug)
            logger.debug(user_settings.peek32(0))
            # Write a value to activate loopback mode
            user_settings.poke32(0, SWITCH_LOOPBACK_MODE)
            # Read again to verify the register value was updated
            logger.debug(user_settings.peek32(0))
        else:
            logger.error("Cannot write to user settings.")
    except Exception as e:
        logger.error(e)

    # ------------------------------------------------------------
    # 4. Start transmit (TX), metadata, and receive (RX) threads
    # ------------------------------------------------------------
    tx_thr = tx_thread(
        usrp,
        tx_streamer,
        quit_event,
        amplitude=amplitudes,
        phase=[0.0, 0.0],
        start_time=start_time,
    )

    # Thread responsible for handling TX metadata (timestamps, etc.)
    tx_meta_thr = tx_meta_thread(tx_streamer, quit_event)

    # Thread that captures received samples during loopback
    rx_thr = rx_thread(
        usrp,
        rx_streamer,
        quit_event,
        duration=stop_lb - start_lb,
        res=result_queue,
        start_time=start_time,
    )

    # 5. Wait until STOP_LB (from arguments) plus some safety margin (delta)
    wait_time = (stop_lb - start_lb) + delta(usrp, start_lb)
    if wait_time > 0:
        time.sleep(wait_time)

    # ------------------------------------------------------------
    # 6. Signal all threads to stop and wait for them to finish
    # ------------------------------------------------------------
    quit_event.set()   # Triggers thread termination
    tx_thr.join()
    rx_thr.join()
    tx_meta_thr.join()

    # ------------------------------------------------------------
    # 7. Reset the RF switch control (disable loopback mode)
    # ------------------------------------------------------------
    if user_settings:
        user_settings.poke32(0, SWITCH_RESET_MODE)

    # ------------------------------------------------------------
    # 8. Clear the quit event flag to prepare for the next measurement
    # ------------------------------------------------------------
    quit_event.clear()

# -------------------------------
# Main function: run transmission task (after synchronization control)
# -------------------------------
def main():
    try:
        # Initialize USRP device and load FPGA image
        usrp = uhd.usrp.MultiUSRP("enable_user_regs, fpga=usrp_b210_fpga_loopback_ctrl.bin, mode_n=integer")
        logger.info("Using Device: %s", usrp.get_pp_string())

        # =========================
        # New: Communicate with sync server
        # =========================
        # Please modify the IP below to match your actual sync server IP
        sync_context = zmq.Context()
        # Create REQ socket to communicate with server's "alive" port (5558)
        alive_client = sync_context.socket(zmq.REQ)
        alive_client.connect(f"tcp://{server_ip}:5558")
        alive_message = f"{HOSTNAME} TX alive"
        logger.info("Sending alive message to sync server: %s", alive_message)
        alive_client.send_string(alive_message)
        reply = alive_client.recv_string()
        logger.info("Received alive reply from sync server: %s", reply)

        # Create SUB socket to listen to sync messages (port 5557)
        sync_subscriber = sync_context.socket(zmq.SUB)
        sync_subscriber.connect(f"tcp://{server_ip}:5557")
        sync_subscriber.setsockopt_string(zmq.SUBSCRIBE, "")
        logger.info("Waiting for SYNC message from sync server...")
        sync_msg = sync_subscriber.recv_string()
        logger.info("Received SYNC message: %s", sync_msg)

        logger.info("Setting device timestamp to 0...")
        usrp.set_time_unknown_pps(uhd.types.TimeSpec(0.0))
        logger.debug("[SYNC] Resetting time.")
        logger.info(f"RX GAIN PROFILE CH0: {usrp.get_rx_gain_names(0)}")
        logger.info(f"RX GAIN PROFILE CH1: {usrp.get_rx_gain_names(1)}")
        time.sleep(2)  # Wait for PPS rising edge

        # Complete hardware setup, synchronization, tuning, and get TX and RX streamers
        tx_streamer, _ = setup(usrp)
        quit_event = threading.Event()

        # -------------------------------------------------------------------------
        # STEP 1: Perform internal loopback measurement with reference signal
        # -------------------------------------------------------------------------
        logger.info("Scheduled LOOPBACK start time: %.6f", START_LB)
        measure_loopback(
            usrp,
            tx_streamer,
            rx_streamer,
            quit_event,
            result_queue,
            START_LB,
            STOP_LB,
        )

        # Retrieve loopback phase result
        phi_LB = result_queue.get()

        # Print loopback phase
        logger.info("Phase pilot reference signal in rad: %s", phi_LB)
        logger.info("Phase pilot reference signal in degrees: %s", np.rad2deg(phi_LB))

        # =========================

        # After synchronization, schedule TX based on current time

        # A short delay (e.g., 0.2s) can be added to ensure TX starts after config
        start_time_spec = uhd.types.TimeSpec(START_Pilot)
        logger.info("Scheduled TX start time: %.6f", START_Pilot)
        # Start TX thread with amplitude=1.0, phase=0.0 (both channels)

        usrp.set_tx_antenna(PILOT_TX_ANT, PILOT_TX_CH)

        amplitudes = [0.0,0.0] 
        amplitudes[PILOT_TX_CH] = 0.8

        tx_thr = tx_thread(
            usrp,
            tx_streamer,
            quit_event,
            phase_corr=phi_LB,
            phase=[0.0, 0.0],
            amplitude=amplitudes,
            start_time=start_time_spec,
        )
        # Also start TX async metadata monitor thread
        tx_meta_thr = tx_meta_thread(tx_streamer, quit_event)

        # Stop transmission after a certain duration
        time.sleep(STOPT_Pilot_Tx - get_current_time(usrp))
        quit_event.set()
        tx_thr.join()
        tx_meta_thr.join()

        logger.info("TX script finished successfully.")
    except Exception as e:
        logger.error("Error encountered in TX script: %s", e)
        sys.exit(1)
    finally:
        time.sleep(START_BF + 10 - get_current_time(usrp))
        sys.exit(0)

if __name__ == "__main__":
    main()
