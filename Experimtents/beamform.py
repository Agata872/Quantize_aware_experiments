import logging
import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta

import numpy as np
import tools
import uhd  # type: ignore
import yaml
import zmq


meas_id = 0
exp_id = 0
results = []

data_file = None
file_name = ""
file_name_state = ""

SWITCH_LOOPBACK_MODE = 0x00000006  # which is 110
SWITCH_RESET_MODE = 0x00000000

context = zmq.Context()

iq_socket = context.socket(zmq.PUB)

iq_socket.bind(f"tcp://*:{50002}")

HOSTNAME = socket.gethostname()[4:]
file_open = False

with open(
    os.path.join(os.path.dirname(__file__), "usrp-settings.yml"), "r", encoding="utf-8"
) as file:
    vars = yaml.safe_load(file)
    globals().update(vars)  # update the global variables with the vars in yaml


# Setup the logger with our custom timestamp formatting
class LogFormatter(logging.Formatter):
    """Log formatter which prints the timestamp with fractional seconds"""

    @staticmethod
    def pp_now():
        """Returns a formatted string containing the time of day"""
        now = datetime.now()
        return "{:%H:%M}:{:05.2f}".format(now, now.second + now.microsecond / 1e6)
        # return "{:%H:%M:%S}".format(now)

    def formatTime(self, record, datefmt=None):
        converter = self.converter(record.created)
        if datefmt:
            import time

            formatted_date = time.strftime(datefmt, converter)
        else:
            formatted_date = LogFormatter.pp_now()
        return formatted_date


connected_to_server = False
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
console = logging.StreamHandler()
logger.addHandler(console)
formatter = LogFormatter(
    fmt="[%(asctime)s] [%(levelname)s] (%(threadName)-10s) %(message)s"
)
console.setFormatter(formatter)
TOPIC_CH0 = b"CH0"
TOPIC_CH1 = b"CH1"

# File handler (overwrite mode)
file_handler = logging.FileHandler("log.txt", mode="w")  # Overwrite log.txt
logger.addHandler(file_handler)
file_handler.setFormatter(formatter)

# ZMQ handler sends only errors and above
from zmq_handler import ZMQHandler  # Make sure zmq_handler.py defines a class named ZMQHandler

zmq_handler = ZMQHandler(f"tcp://{SERVER_IP}:{ERROR_PORT}")
zmq_handler.setLevel(logging.ERROR)
zmq_handler.setFormatter(formatter)
logger.addHandler(zmq_handler)

def rx_channels(usrp, rx_streamer, quit_event, duration, result_queue, start_time=None):
    """
    Receives and processes IQ samples from a USRP device for a specified duration, computes phase and amplitude statistics, and stores results.
    Parameters:
        usrp: uhd.usrp.MultiUSRP
            The USRP device object to receive samples from.
        rx_streamer: uhd.usrp.Streamer
            The RX streamer object used to receive samples.
        quit_event: threading.Event
            Event to signal early termination of the receive loop.
        duration: float
            Duration (in seconds) to receive samples.
        result_queue: queue.Queue
            Queue to store computed results (phase and amplitude statistics).
        start_time: uhd.types.TimeSpec, optional
            Optional start time for the stream command. If None, streaming starts after a short delay.
    Notes:
        - Stores received IQ samples to a file.
        - Computes phase difference, circular mean, mean, and average amplitude.
        - Logs gain, errors, and amplitude statistics.
        - Handles early termination via quit_event or KeyboardInterrupt.
    """
    # https://files.ettus.com/manual/page_sync.html#sync_phase_cordics
    # The CORDICs are reset at each start-of-burst command, so users should ensure that every start-of-burst also has a time spec set.
    logger.debug("RX GAIN IS CH0: %s CH1: %s", usrp.get_rx_gain(0), usrp.get_rx_gain(1))

    num_channels = rx_streamer.get_num_channels()
    max_samps_per_packet = rx_streamer.get_max_num_samps()
    buffer_length = int(duration * RATE * 2)
    iq_data = np.empty((num_channels, buffer_length), dtype=np.complex64)

    recv_buffer = np.zeros((num_channels, max_samps_per_packet), dtype=np.complex64)
    rx_md = uhd.types.RXMetadata()
    # Craft and send the Stream Command
    stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    # The stream now parameter controls when the stream begins. When true, the device will begin streaming ASAP. When false, the device will begin streaming at a time specified by time_spec.
    stream_cmd.stream_now = False
    timeout = 1.0
    if start_time is not None:
        stream_cmd.time_spec = start_time
        time_diff = start_time.get_real_secs() - usrp.get_time_now().get_real_secs()
        if time_diff > 0:
            timeout = 1.0 + time_diff
    else:
        stream_cmd.time_spec = uhd.types.TimeSpec(
            usrp.get_time_now().get_real_secs() + INIT_DELAY + 0.1
        )
    rx_streamer.issue_stream_cmd(stream_cmd)
    try:
        num_rx = 0
        while not quit_event.is_set():
            try:
                num_rx_i = rx_streamer.recv(recv_buffer, rx_md, timeout)
                if rx_md.error_code != uhd.types.RXMetadataErrorCode.none:
                    logger.error(rx_md.error_code)
                else:
                    if num_rx_i > 0:
                        # samples = recv_buffer[:,:num_rx_i]
                        # send_rx(samples)
                        samples = recv_buffer[:, :num_rx_i]
                        if num_rx + num_rx_i > buffer_length:
                            logger.error(
                                "more samples received than buffer long, not storing the data"
                            )
                        else:
                            iq_data[:, num_rx : num_rx + num_rx_i] = samples
                            # threading.Thread(target=send_rx,
                            #                  args=(samples,)).start()
                            num_rx += num_rx_i
            except RuntimeError as ex:
                logger.error("Runtime error in receive: %s", ex)
                return
    except KeyboardInterrupt:
        pass
    finally:
        logger.debug("CTRL+C is pressed or duration is reached, closing off ")
        rx_streamer.issue_stream_cmd(
            uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
        )
        iq_samples = iq_data[:, int(RATE // 10) : num_rx]

        # np.save(file_name_state, iq_samples)

        # Compute the phase difference for the file
        phase_ch0, slope_ch0 = tools.get_phases_and_remove_CFO(iq_data[0,:], RATE)
        phase_ch1, slope_ch1 = tools.get_phases_and_remove_CFO(iq_data[1, :], RATE)

        logger.debug(
            "slope_ch0 and slope_ch1: %.6f, %.6f [Hz]",
            slope_ch0 / (2 * np.pi),
            slope_ch1 / (2 * np.pi),
        )

        phase_diff = phase_ch0 - phase_ch1

        _circ_mean = tools.circmean(phase_diff, deg=False)
        _mean = np.mean(phase_diff)

        logger.debug("Circmean and mean: %.6f, %.6f", _circ_mean, _mean)

        avg_ampl = np.mean(np.abs(iq_samples), axis=1)

        result_queue.put(
            {
                "circ_mean": _circ_mean,
                "mean": _mean,
                "avg_ampl": avg_ampl.tolist()[PILOT_RX_CH], #TODO move this channel selection out of this function
            }
        )

        avg_ampl = np.mean(np.abs(iq_samples), axis=1)

        var_phase = np.var(np.angle(iq_data), axis=1)
        var_phase_diff = np.var(phase_diff)

        max_I = np.max(np.abs(np.real(iq_samples)), axis=1)
        max_Q = np.max(np.abs(np.imag(iq_samples)), axis=1)

        logger.debug(
            "MAX AMPL IQ CH0: I %.6f Q %.6f CH1:I %.6f Q %.6f",
            max_I[0],
            max_Q[0],
            max_I[1],
            max_Q[1],
        )

        if np.max(np.abs(np.real(iq_samples))) > 0.8:
            logger.error("IQ data to high")

        logger.debug(
            "AVG AMPL IQ CH0: %.6f CH1: %.6f",
            avg_ampl[0],
            avg_ampl[1],
        )

        logger.debug("VAR PHASE IQ CH0: %.6f CH1: %.6f CH1-CH0: %.6f", var_phase[0], var_phase[1], var_phase_diff)


def setup_clock(usrp, clock_src, num_mboards):
    """
    Configures the clock source for a USRP device and verifies that all motherboard clock signals are locked.

    Args:
        usrp: The USRP device object to configure.
        clock_src (str): The clock source to set (e.g., 'internal', 'external').
        num_mboards (int): The number of motherboards to check for clock lock.

    Returns:
        bool: True if all motherboards successfully lock onto the clock signal within the timeout period, False otherwise.

    Logs:
        - Debug messages for clock lock confirmation and status.
        - Error message if unable to confirm clock lock on any motherboard.
    """
    usrp.set_clock_source(clock_src)
    logger.debug("Now confirming lock on clock signals...")
    end_time = datetime.now() + timedelta(milliseconds=CLOCK_TIMEOUT)
    # Lock onto clock signals for all mboards
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
    """Setup the PPS source"""

    logger.debug("Setting PPS")
    usrp.set_time_source(pps)
    return True


def print_tune_result(tune_res):
    """
    Logs the results of a frequency tuning operation.

    Args:
        tune_res: An object containing the following attributes:
            - target_rf_freq (float): The target RF frequency in Hz.
            - actual_rf_freq (float): The actual RF frequency achieved in Hz.
            - target_dsp_freq (float): The target DSP frequency in Hz.
            - actual_dsp_freq (float): The actual DSP frequency achieved in Hz.

    The function logs these frequencies in MHz using the debug level of the logger.
    """
    logger.debug(
        "Tune Result:\n    Target RF  Freq: %.6f (MHz)\n Actual RF  Freq: %.6f (MHz)\n Target DSP Freq: %.6f "
        "(MHz)\n "
        "Actual DSP Freq: %.6f (MHz)\n",
        (tune_res.target_rf_freq / 1e6),
        (tune_res.actual_rf_freq / 1e6),
        (tune_res.target_dsp_freq / 1e6),
        (tune_res.actual_dsp_freq / 1e6),
    )


def tune_usrp(usrp, freq, channels, at_time):
    """Synchronously set the device's frequency.
    If a channel is using an internal LO it will be tuned first
    and every other channel will be manually tuned based on the response.
    This is to account for the internal LO channel having an offset in the actual DSP frequency.
    Then all channels are synchronously tuned."""
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


def wait_till_go_from_server(ip, _connect=True):

    global meas_id, file_open, data_file, file_name
    # Connect to the publisher's address
    logger.debug("Connecting to server %s.", ip)
    sync_socket = context.socket(zmq.SUB)

    alive_socket = context.socket(zmq.REQ)

    sync_socket.connect(f"tcp://{ip}:{5557}")
    alive_socket.connect(f"tcp://{ip}:{5558}")
    # Subscribe to topics
    sync_socket.subscribe("")

    logger.debug("Sending ALIVE")
    alive_socket.send_string(HOSTNAME)
    # Receives a string format message
    logger.debug("Waiting on SYNC from server %s.", ip)

    meas_id, unique_id = sync_socket.recv_string().split(" ")

    file_name = f"data_{HOSTNAME}_{unique_id}_{meas_id}"

    if not file_open:
        data_file = open(f"data_{HOSTNAME}_{unique_id}.txt", "a", encoding="utf-8")
        file_open = True

    logger.debug(meas_id)

    alive_socket.close()
    sync_socket.close()


def get_BF(ip, ampl, phase):
    import json

    logger.debug("Connecting to server %s.", ip)

    dealer_socket = context.socket(zmq.DEALER)

    # Give this DEALER a unique identity so the ROUTER can reply properly

    dealer_socket.setsockopt_string(zmq.IDENTITY, HOSTNAME)

    dealer_socket.connect(f"tcp://{SERVER_IP}:5559")

    logger.debug("Sending CSI")

    # Create a message dict with CSI (complex split into real and imag)
    msg = {"host": HOSTNAME, "csi_ampl": ampl, "csi_phase": phase}

    # Serialize to JSON and send
    dealer_socket.send(json.dumps(msg).encode())
    logger.debug("Message sent, waiting for response...")

    # Wait for response
    poller = zmq.Poller()
    poller.register(dealer_socket, zmq.POLLIN)
    socks = dict(poller.poll(30000))

    result = None

    if dealer_socket in socks and socks[dealer_socket] == zmq.POLLIN:
        reply = dealer_socket.recv()
        logger.debug(f"Raw reply: {reply!r}")
        response = json.loads(reply.decode())
        print(f"[{HOSTNAME}] Received: {response}")
        # Reconstruct complex number
        result = complex(response["real"], response["imag"])
        logger.debug("Received response: %s", result)
    else:
        print(f"[{HOSTNAME}] No reply from server, timed out.")

    dealer_socket.close()

    return result


def setup(usrp, server_ip, connect=True):
    rate = RATE
    mcr = 20e6
    assert (
        mcr / rate
    ).is_integer(), f"The masterclock rate {mcr} should be an integer multiple of the sampling rate {rate}"
    # Manual selection of master clock rate may also be required to synchronize multiple B200 units in time.
    usrp.set_master_clock_rate(mcr)
    channels = [0, 1]
    setup_clock(usrp, "external", usrp.get_num_mboards())
    setup_pps(usrp, "external")
    # smallest as possible (https://files.ettus.com/manual/page_usrp_b200.html#b200_fe_bw)
    # rx_bw = 200e3
    for chan in channels:
        usrp.set_rx_rate(rate, chan)
        usrp.set_tx_rate(rate, chan)
        # NOTE DC offset is enabled
        usrp.set_rx_dc_offset(False, chan)
        # usrp.set_rx_bandwidth(rx_bw, chan)
        usrp.set_rx_agc(False, chan)
    # specific settings from loopback/REF PLL

    usrp.set_tx_gain(LOOPBACK_TX_GAIN, LOOPBACK_TX_CH)
    usrp.set_tx_gain(FREE_TX_GAIN, FREE_TX_CH)

    usrp.set_rx_gain(LOOPBACK_RX_GAIN, LOOPBACK_RX_CH)
    usrp.set_rx_gain(REF_RX_GAIN, REF_RX_CH)
    # streaming arguments
    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = channels
    # streamers
    tx_streamer = usrp.get_tx_stream(st_args)
    rx_streamer = usrp.get_rx_stream(st_args)
    # Step1: wait for the last pps time to transition to catch the edge
    # Step2: set the time at the next pps (synchronous for all boards)
    # this is better than set_time_next_pps as we wait till the next PPS to transition and after that we set the time.
    # this ensures that the FPGA has enough time to clock in the new timespec (otherwise it could be too close to a PPS edge)
    wait_till_go_from_server(server_ip, connect)
    logger.info("Setting device timestamp to 0...")
    usrp.set_time_unknown_pps(uhd.types.TimeSpec(0.0))
    logger.debug("[SYNC] Resetting time.")
    # we wait 2 seconds to ensure a PPS rising edge occurs and latches the 0.000s value to both USRPs.
    time.sleep(2)
    logger.info("RX GAIN PROFILE CH0: %s", usrp.get_rx_gain_names(0))
    logger.info("RX GAIN PROFILE CH1: %s", usrp.get_rx_gain_names(1))

    tune_usrp(usrp, FREQ, channels, at_time=START_TUNE)
    logger.info(
        "USRP has been tuned and setup. (%.6f)", usrp.get_time_now().get_real_secs()
    )
    return tx_streamer, rx_streamer


def rx_thread(usrp, rx_streamer, quit_event, duration, res, start_time=None):
    _rx_thread = threading.Thread(
        target=rx_channels,
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


def delta(usrp, at_time):
    return at_time - usrp.get_time_now().get_real_secs()


def get_current_time(usrp):
    return usrp.get_time_now().get_real_secs()


def tx_thread(
    usrp, tx_streamer, quit_event, phase=None, amplitude=None, start_time=None
):
    if phase is None:
        phase = [0, 0]
    if amplitude is None:
        amplitude = [0.8, 0.8]
    tx_thr = threading.Thread(
        target=tx_ref,
        args=(usrp, tx_streamer, quit_event, phase, amplitude, start_time),
    )

    tx_thr.name = "TX_thread"
    tx_thr.start()

    return tx_thr


def tx_ref(usrp, tx_streamer, quit_event, phase, amplitude, start_time=None):
    logger.debug("TX GAIN IS CH0: %s CH1: %s", usrp.get_tx_gain(0), usrp.get_tx_gain(1))

    num_channels = tx_streamer.get_num_channels()

    max_samps_per_packet = tx_streamer.get_max_num_samps()

    amplitude = np.asarray(amplitude)

    phase = np.asarray(phase)

    sample = amplitude * np.exp(phase * 1j)

    transmit_buffer = np.ones(
        (num_channels, 1000 * max_samps_per_packet), dtype=np.complex64
    )

    transmit_buffer[0, :] *= sample[0]

    transmit_buffer[1, :] *= sample[1]

    tx_md = uhd.types.TXMetadata()

    if start_time is not None:
        tx_md.time_spec = start_time
    else:
        tx_md.time_spec = uhd.types.TimeSpec(
            usrp.get_time_now().get_real_secs() + INIT_DELAY
        )

    tx_md.has_time_spec = True

    try:

        while not quit_event.is_set():
            tx_streamer.send(transmit_buffer, tx_md)

    except KeyboardInterrupt:
        logger.debug("CTRL+C is pressed, closing off")

    finally:
        # Send a mini EOB packet

        tx_md.end_of_burst = True

        tx_streamer.send(np.zeros((num_channels, 0), dtype=np.complex64), tx_md)


def tx_meta_thread(tx_streamer, quit_event):
    tx_meta_thr = threading.Thread(target=tx_async_th, args=(tx_streamer, quit_event))

    tx_meta_thr.name = "TX_META_thread"
    tx_meta_thr.start()
    return tx_meta_thr


import queue


def starting_in(usrp, at_time):
    return f"Starting in {delta(usrp, at_time):.2f}s"


def stopping_in(usrp, at_time):
    return f"Stopping in {delta(usrp, at_time):.2f}s"


def measure_pilot(usrp, rx_streamer, quit_event, result_queue, at_time=None, stop_time=None):
    logger.debug("########### Measure PILOT ###########")

    start_time = uhd.types.TimeSpec(at_time)

    logger.debug(starting_in(usrp, at_time))
    logger.debug(stopping_in(usrp, stop_time))

    usrp.set_rx_antenna(PILOT_RX_ANT, PILOT_RX_CH)
    usrp.set_rx_antenna(REF_RX_ANT, REF_RX_CH)

    rx_thr = rx_thread(
        usrp,
        rx_streamer,
        quit_event,
        duration=CAPTURE_TIME,
        res=result_queue,
        start_time=start_time,
    )

    time.sleep(stop_time - get_current_time(usrp))

    quit_event.set()

    rx_thr.join()

    quit_event.clear()


def measure_loopback(
    usrp, tx_streamer, rx_streamer, quit_event, result_queue, at_time, stop_time):
    logger.debug("########### Measure LOOPBACK ###########")

    # DO NOT SET ANT/CH as this is done manually

    # TX
    amplitudes = [0.0, 0.0]
    amplitudes[LOOPBACK_TX_CH] = 0.8

    start_time = uhd.types.TimeSpec(at_time)

    logger.debug(starting_in(usrp, at_time))
    logger.debug(stopping_in(usrp, stop_time))

    user_settings = None
    try:
        user_settings = usrp.get_user_settings_iface(1)
        if user_settings:
            logger.debug(user_settings.peek32(0))
            user_settings.poke32(0, SWITCH_LOOPBACK_MODE)
            logger.debug(user_settings.peek32(0))
        else:
            logger.error(" Cannot write to user settings.")
    except (RuntimeError, OSError) as e:
        logger.error(e)

    tx_thr = tx_thread(
        usrp,
        tx_streamer,
        quit_event,
        amplitude=amplitudes,
        phase=[0.0, 0.0],
        start_time=start_time,
    )

    tx_meta_thr = tx_meta_thread(tx_streamer, quit_event)

    rx_thr = rx_thread(
        usrp,
        rx_streamer,
        quit_event,
        duration=CAPTURE_TIME,
        res=result_queue,
        start_time=start_time,
    )

    time.sleep(stop_time - get_current_time(usrp))

    quit_event.set()

    tx_thr.join()

    rx_thr.join()

    tx_meta_thr.join()

    # reset RF switches ctrl
    if user_settings:
        user_settings.poke32(0, SWITCH_RESET_MODE)

    quit_event.clear()


def tx_phase_coh(usrp, tx_streamer, quit_event, phase_corr, at_time, stop_time):
    """
    Transmit with adjusted phases using the USRP device.
    This function configures the transmission parameters (phases and amplitudes) for the USRP device,
    starts the transmission in a separate thread, and manages the transmission duration. It also handles
    thread synchronization and cleanup upon completion.
    Args:
        usrp: The USRP device object to be used for transmission.
        tx_streamer: The transmitter streamer object for sending data.
        quit_event: A threading event used to signal threads to stop.
        phase_corr (float): The phase correction value to be applied to the transmission channel.
        at_time (float): The absolute time (in seconds) at which to start transmission.
        long_time (bool, optional): If True, transmit for TX_TIME seconds; otherwise, transmit for 10 seconds. Defaults to True.
    Returns:
        tuple: A tuple containing the transmission thread (tx_thr) and the metadata thread (tx_meta_thr).
    """
    logger.debug("########### TX with adjusted phases ###########")

    phases = [0.0, 0.0]
    amplitudes = [0.0, 0.0]

    phases[BF_TX_CH] = phase_corr
    amplitudes[BF_TX_CH] = 0.8

    usrp.set_tx_antenna(BF_TX_ANT, BF_TX_CH)
    usrp.set_tx_gain(BF_TX_GAIN, BF_TX_CH)

    start_time = uhd.types.TimeSpec(at_time)

    logger.debug(starting_in(usrp, at_time))
    logger.debug(stopping_in(usrp, stop_time))

    tx_thr = tx_thread(
        usrp,
        tx_streamer,
        quit_event,
        amplitude=amplitudes,
        phase=phases,
        start_time=start_time,
    )

    tx_meta_thr = tx_meta_thread(tx_streamer, quit_event)

    time.sleep(stop_time - get_current_time(usrp))

    quit_event.set()

    tx_thr.join()

    tx_meta_thr.join()

    logger.debug("done TX")

    return tx_thr, tx_meta_thr


def parse_arguments():
    """
    Parses command-line arguments for measurement ID, gain values, and experiment ID.
    This function uses argparse to parse the following required arguments:
        --meas : int
            The measurement ID.
        --gain : int (one or two values)
            The gain value(s) in dB. If one value is provided, it is duplicated for both gains.
            If two values are provided, they are used as-is.
        --exp : str
            The experiment ID.
    Sets the global variables:
        meas_id : int
            The parsed measurement ID.
        gains_bash : list of int
            A list containing two gain values.
        exp_id : str
            The parsed experiment ID.
    Prints the gain values if two are provided. Prints an error if more than two gain values are given.
    """
    import argparse

    global meas_id, gains_bash, exp_id

    # Create the parser
    parser = argparse.ArgumentParser(description="Transmit with phase difference.")

    # Add the --phase argument
    parser.add_argument("--meas", type=int, help="measurement ID", required=True)

    parser.add_argument("--gain", type=int, nargs="+", help="gain_db", required=True)

    parser.add_argument("--exp", type=str, help="exp ID", required=True)

    # Parse the arguments
    args = parser.parse_args()

    # Set the global variable tx_phase to the value of --phase
    meas_id = args.meas

    # Access the gain values
    gains = args.gain

    # Handle cases where either one or two gain values are provided
    if len(gains) == 1:
        gains_bash = [gains[0], gains[0]]
    elif len(gains) == 2:
        gains_bash = gains
        print(f"Gain 1: {gains_bash[0]}, Gain 2: {gains_bash[1]}")
    else:
        print("Error: Too many gain values provided.")

    exp_id = args.exp

def main():
    import numpy as _np
    import numpy.lib.function_base as _lfb

    global file_name_state

    # unique_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    # unique_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    # # parse_arguments()

    # file_name = f"data_{HOSTNAME}_{unique_id}"

    try:
        # Initialize USRP device

        fpga_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usrp_b210_fpga_loopback_ctrl.bin")

        usrp = uhd.usrp.MultiUSRP(f"enable_user_regs, fpga={fpga_path}, mode_n=integer")
        logger.info("Using Device: %s", usrp.get_pp_string())

        # Hardware setup
        tx_streamer, rx_streamer = setup(usrp, SERVER_IP, connect=False)
        quit_event = threading.Event()
        # ==================================
        # =========== LOOPBACK =============
        # ==================================
        result_queue = queue.Queue()

        logger.info("Scheduled LOOPBACK start time: %.6f", START_LB)
        measure_loopback(
            usrp,
            tx_streamer,
            rx_streamer,
            quit_event,
            result_queue,
            at_time=START_LB,
            stop_time=STOP_LB,
        )

        metrics = result_queue.get()
        PHI_LR = metrics["circ_mean"]

        # ==================================
        # ========= PILOT 1 ================
        # ==================================

        logger.info("Scheduled P1 RX start time: %.6f", START_P1_RX)
        measure_pilot(usrp, rx_streamer, quit_event, result_queue, at_time=START_P1_RX, stop_time=STOP_P1_TX)
        # phi = result_queue.get()
        metrics = result_queue.get()
        circ_mean = metrics["circ_mean"]
        # mean_val = metrics["mean"]
        avg_ampl = metrics["avg_ampl"]  # [ch0, ch1]
        PHI_PR_1 = circ_mean
        AMP_PR_1 = avg_ampl
        logger.info("Measured pilot phase: %.6f", PHI_PR_1)

        # ==================================
        # ============ BF ==================
        # ==================================
        logger.info("Scheduled downlink start time: %.6f", START_BF)
        PHI_CABLE = 0

        with open(
            os.path.join(os.path.dirname(__file__), "config-phase-offsets.yml"),
            "r",
            encoding="utf-8",
        ) as phases_yaml:
            try:
                phases_dict = yaml.safe_load(phases_yaml)
                if HOSTNAME in phases_dict.keys():
                    PHI_CABLE = np.deg2rad(phases_dict[HOSTNAME])
                    logger.debug("Applying CABLE phase: %s", PHI_CABLE)
                else:
                    logger.error("Phase not found in config-phase-offsets.yml")
            except yaml.YAMLError as exc:
                print(exc)

        PHI_CSI = PHI_PR_1 + PHI_CABLE #+ PHI_CABLE
        bf = get_BF(SERVER_IP, AMP_PR_1 , PHI_CSI)
        print("np=", _np)
        print("type(_lfb._nx)=", type(getattr(_lfb, "_nx", None)))
        print("callable(np.arctan2)=", callable(_np.arctan2))
        print("callable(np.angle)=", callable(_np.angle))
        logger.debug("BF result: %s (expected) %s (from server)", -PHI_CSI, np.angle(bf))

        PHI_MRT = np.angle(bf) - (PHI_LR + PHI_CABLE) 

        # PHI_DLI =
        # benchmark without phased beamforming
        tx_phase_coh(
            usrp,
            tx_streamer,
            quit_event,
            phase_corr=PHI_MRT,
            at_time=START_BF,
            stop_time=STOP_BF,
        )

        print("DONE")
    except Exception as e:
        # Interrupt and join the threads
        logger.debug("Sending signal to stop!")
        print(traceback.format_exc())
        logger.error(e)
        quit_event.set()
    finally:
        # Cleanup
        zmq_handler.close()
        time.sleep(1)  # give it some time to close
        sys.exit(0)


if __name__ == "__main__":
    main()
