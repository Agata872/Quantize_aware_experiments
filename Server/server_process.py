#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Mod_Demod_Server
# Author: Tianzheng_Miao
# GNU Radio version: 3.10.10.0

from gnuradio import blocks
import numpy
from gnuradio import channels
from gnuradio.filter import firdes
from gnuradio import digital
from gnuradio import filter
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import gr, pdu
from gnuradio import zeromq
from gnuradio.filter import pfb




class server_process(gr.top_block):

    def __init__(self):
        gr.top_block.__init__(self, "Mod_Demod_Server", catch_exceptions=True)

        ##################################################
        # Variables
        ##################################################
        self.sps = sps = 4
        self.nfilts = nfilts = 32
        self.d = d = 1/(2**(1/2))
        self.taps_per_filt = taps_per_filt = int((11*sps*nfilts)/nfilts)
        self.qpsk_mg = qpsk_mg = digital.constellation_rect([d+d*1j, -d+d*1j,-d-d*1j, d-d*1j], [0, 1, 3, 2],
        4, 2, 2, 1, 1).base()
        self.excess_bw = excess_bw = 0.35
        self.eq_gain = eq_gain = 0.0001
        self.variable_adaptive_algorithm_0 = variable_adaptive_algorithm_0 = digital.adaptive_algorithm_cma( qpsk_mg, eq_gain, 1).base()
        self.timing_loop_bw = timing_loop_bw = 0.0314
        self.taps_1 = taps_1 = [1,0,0,0.5]
        self.taps = taps = [0.825,0,0,0,0.526]
        self.samp_rate = samp_rate = 1000000
        self.rrc_taps_tx = rrc_taps_tx = firdes.root_raised_cosine(nfilts, nfilts, 1.0, excess_bw, 11*sps*nfilts)
        self.rrc_taps = rrc_taps = firdes.root_raised_cosine(nfilts, nfilts, 1.0/float(sps), excess_bw, 11*sps*nfilts)
        self.phase_bw = phase_bw = 0.0314
        self.freq_offset = freq_offset = 0
        self.freq = freq = 920e6
        self.filt_delay = filt_delay = int(1+(taps_per_filt-1)//2)
        self.arity = arity = 4

        ##################################################
        # Blocks
        ##################################################

        self.zeromq_push_sink_0 = zeromq.push_sink(gr.sizeof_gr_complex, 1, 'tcp://*:6001', 100, False, (-1), True)
        self.zeromq_push_msg_sink_1_0 = zeromq.push_msg_sink('tcp://192.168.10.30:5556', 100, False)
        self.zeromq_push_msg_sink_1 = zeromq.push_msg_sink('tcp://192.168.10.30:5555', 100, False)
        self.zeromq_pull_source_0 = zeromq.pull_source(gr.sizeof_gr_complex, 1, 'tcp://*:6002', 100, False, (-1), True)
        self.pfb_arb_resampler_xxx_0 = pfb.arb_resampler_ccf(
            sps,
            taps=rrc_taps_tx,
            flt_size=nfilts,
            atten=100)
        self.pfb_arb_resampler_xxx_0.declare_sample_delay(0)
        self.pdu_tagged_stream_to_pdu_0_0 = pdu.tagged_stream_to_pdu(gr.types.byte_t, 'packet_len')
        self.pdu_tagged_stream_to_pdu_0 = pdu.tagged_stream_to_pdu(gr.types.byte_t, 'packet_len')
        self.digital_symbol_sync_xx_0 = digital.symbol_sync_cc(
            digital.TED_SIGNAL_TIMES_SLOPE_ML,
            sps,
            timing_loop_bw,
            1.0,
            1.0,
            1.5,
            2,
            digital.constellation_bpsk().base(),
            digital.IR_PFB_MF,
            nfilts,
            rrc_taps)
        self.digital_linear_equalizer_0 = digital.linear_equalizer(15, 2, variable_adaptive_algorithm_0, True, [ ], 'corr_est')
        self.digital_diff_encoder_bb_0 = digital.diff_encoder_bb(4, digital.DIFF_DIFFERENTIAL)
        self.digital_diff_decoder_bb_0_0 = digital.diff_decoder_bb(4, digital.DIFF_DIFFERENTIAL)
        self.digital_costas_loop_cc_0 = digital.costas_loop_cc(phase_bw, arity, False)
        self.digital_constellation_decoder_cb_0 = digital.constellation_decoder_cb(qpsk_mg.base())
        self.digital_chunks_to_symbols_xx_0 = digital.chunks_to_symbols_bc(qpsk_mg.points(), 1)
        self.channels_channel_model_0 = channels.channel_model(
            noise_voltage=0,
            frequency_offset=freq_offset,
            epsilon=1,
            taps=[1],
            noise_seed=0,
            block_tags=False)
        self.blocks_unpack_k_bits_bb_0 = blocks.unpack_k_bits_bb(2)
        self.blocks_throttle_0 = blocks.throttle(gr.sizeof_gr_complex*1, samp_rate,True)
        self.blocks_stream_to_tagged_stream_0_0 = blocks.stream_to_tagged_stream(gr.sizeof_char, 1, 1024, "packet_len")
        self.blocks_stream_to_tagged_stream_0 = blocks.stream_to_tagged_stream(gr.sizeof_char, 1, 1024, "packet_len")
        self.blocks_packed_to_unpacked_xx_0 = blocks.packed_to_unpacked_bb(2, gr.GR_MSB_FIRST)
        self.analog_random_source_x_0 = blocks.vector_source_b(list(map(int, numpy.random.randint(0, 256, 10000000))), True)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.pdu_tagged_stream_to_pdu_0, 'pdus'), (self.zeromq_push_msg_sink_1, 'in'))
        self.msg_connect((self.pdu_tagged_stream_to_pdu_0_0, 'pdus'), (self.zeromq_push_msg_sink_1_0, 'in'))
        self.connect((self.analog_random_source_x_0, 0), (self.blocks_packed_to_unpacked_xx_0, 0))
        self.connect((self.blocks_packed_to_unpacked_xx_0, 0), (self.blocks_stream_to_tagged_stream_0, 0))
        self.connect((self.blocks_packed_to_unpacked_xx_0, 0), (self.digital_diff_encoder_bb_0, 0))
        self.connect((self.blocks_stream_to_tagged_stream_0, 0), (self.pdu_tagged_stream_to_pdu_0, 0))
        self.connect((self.blocks_stream_to_tagged_stream_0_0, 0), (self.pdu_tagged_stream_to_pdu_0_0, 0))
        self.connect((self.blocks_throttle_0, 0), (self.channels_channel_model_0, 0))
        self.connect((self.blocks_unpack_k_bits_bb_0, 0), (self.blocks_stream_to_tagged_stream_0_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.zeromq_push_sink_0, 0))
        self.connect((self.digital_chunks_to_symbols_xx_0, 0), (self.pfb_arb_resampler_xxx_0, 0))
        self.connect((self.digital_constellation_decoder_cb_0, 0), (self.digital_diff_decoder_bb_0_0, 0))
        self.connect((self.digital_costas_loop_cc_0, 0), (self.digital_constellation_decoder_cb_0, 0))
        self.connect((self.digital_diff_decoder_bb_0_0, 0), (self.blocks_unpack_k_bits_bb_0, 0))
        self.connect((self.digital_diff_encoder_bb_0, 0), (self.digital_chunks_to_symbols_xx_0, 0))
        self.connect((self.digital_linear_equalizer_0, 0), (self.digital_costas_loop_cc_0, 0))
        self.connect((self.digital_symbol_sync_xx_0, 0), (self.digital_linear_equalizer_0, 0))
        self.connect((self.pfb_arb_resampler_xxx_0, 0), (self.blocks_throttle_0, 0))
        self.connect((self.zeromq_pull_source_0, 0), (self.digital_symbol_sync_xx_0, 0))


    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps
        self.set_rrc_taps(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0/float(self.sps), self.excess_bw, 11*self.sps*self.nfilts))
        self.set_rrc_taps_tx(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0, self.excess_bw, 11*self.sps*self.nfilts))
        self.set_taps_per_filt(int((11*self.sps*self.nfilts)/self.nfilts))
        self.digital_symbol_sync_xx_0.set_sps(self.sps)
        self.pfb_arb_resampler_xxx_0.set_rate(self.sps)

    def get_nfilts(self):
        return self.nfilts

    def set_nfilts(self, nfilts):
        self.nfilts = nfilts
        self.set_rrc_taps(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0/float(self.sps), self.excess_bw, 11*self.sps*self.nfilts))
        self.set_rrc_taps_tx(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0, self.excess_bw, 11*self.sps*self.nfilts))
        self.set_taps_per_filt(int((11*self.sps*self.nfilts)/self.nfilts))

    def get_d(self):
        return self.d

    def set_d(self, d):
        self.d = d

    def get_taps_per_filt(self):
        return self.taps_per_filt

    def set_taps_per_filt(self, taps_per_filt):
        self.taps_per_filt = taps_per_filt
        self.set_filt_delay(int(1+(self.taps_per_filt-1)//2))

    def get_qpsk_mg(self):
        return self.qpsk_mg

    def set_qpsk_mg(self, qpsk_mg):
        self.qpsk_mg = qpsk_mg

    def get_excess_bw(self):
        return self.excess_bw

    def set_excess_bw(self, excess_bw):
        self.excess_bw = excess_bw
        self.set_rrc_taps(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0/float(self.sps), self.excess_bw, 11*self.sps*self.nfilts))
        self.set_rrc_taps_tx(firdes.root_raised_cosine(self.nfilts, self.nfilts, 1.0, self.excess_bw, 11*self.sps*self.nfilts))

    def get_eq_gain(self):
        return self.eq_gain

    def set_eq_gain(self, eq_gain):
        self.eq_gain = eq_gain

    def get_variable_adaptive_algorithm_0(self):
        return self.variable_adaptive_algorithm_0

    def set_variable_adaptive_algorithm_0(self, variable_adaptive_algorithm_0):
        self.variable_adaptive_algorithm_0 = variable_adaptive_algorithm_0

    def get_timing_loop_bw(self):
        return self.timing_loop_bw

    def set_timing_loop_bw(self, timing_loop_bw):
        self.timing_loop_bw = timing_loop_bw
        self.digital_symbol_sync_xx_0.set_loop_bandwidth(self.timing_loop_bw)

    def get_taps_1(self):
        return self.taps_1

    def set_taps_1(self, taps_1):
        self.taps_1 = taps_1

    def get_taps(self):
        return self.taps

    def set_taps(self, taps):
        self.taps = taps

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.blocks_throttle_0.set_sample_rate(self.samp_rate)

    def get_rrc_taps_tx(self):
        return self.rrc_taps_tx

    def set_rrc_taps_tx(self, rrc_taps_tx):
        self.rrc_taps_tx = rrc_taps_tx
        self.pfb_arb_resampler_xxx_0.set_taps(self.rrc_taps_tx)

    def get_rrc_taps(self):
        return self.rrc_taps

    def set_rrc_taps(self, rrc_taps):
        self.rrc_taps = rrc_taps

    def get_phase_bw(self):
        return self.phase_bw

    def set_phase_bw(self, phase_bw):
        self.phase_bw = phase_bw
        self.digital_costas_loop_cc_0.set_loop_bandwidth(self.phase_bw)

    def get_freq_offset(self):
        return self.freq_offset

    def set_freq_offset(self, freq_offset):
        self.freq_offset = freq_offset
        self.channels_channel_model_0.set_frequency_offset(self.freq_offset)

    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq

    def get_filt_delay(self):
        return self.filt_delay

    def set_filt_delay(self, filt_delay):
        self.filt_delay = filt_delay

    def get_arity(self):
        return self.arity

    def set_arity(self, arity):
        self.arity = arity




def main(top_block_cls=server_process, options=None):
    tb = top_block_cls()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    tb.start()

    try:
        input('Press Enter to quit: ')
    except EOFError:
        pass
    tb.stop()
    tb.wait()


if __name__ == '__main__':
    main()
