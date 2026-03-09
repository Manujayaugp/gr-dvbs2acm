#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: DVB-S2 ACM Loopback — LEO Satellite Channel
# Author: Research - DVB-S2 ACM
# Copyright: GPLv3
# Description: DVB-S2 ACM full loopback with physics-based LEO satellite channel
# GNU Radio version: 3.10.9.2

from PyQt5 import Qt
from gnuradio import qtgui
from PyQt5 import QtCore
from gnuradio import blocks
import numpy
from gnuradio import blocks, gr
from gnuradio import channels
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import dvbs2acm
import sip



class acm_loopback(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "DVB-S2 ACM Loopback — LEO Satellite Channel", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("DVB-S2 ACM Loopback — LEO Satellite Channel")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "acm_loopback")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variables
        ##################################################
        self.snr_db = snr_db = 30
        self.samp_rate = samp_rate = 2000000
        self.rain_rate = rain_rate = 5.0
        self.noise_amp = noise_amp = 10**(-snr_db/20.0)
        self.altitude_km = altitude_km = 500.0

        ##################################################
        # Blocks
        ##################################################

        self._snr_db_range = qtgui.Range(20, 40, 1, 30, 200)
        self._snr_db_win = qtgui.RangeWidget(self._snr_db_range, self.set_snr_db, "AWGN Noise Floor (dB) — LEO fading handled by channel block", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._snr_db_win, 0, 0, 1, 3)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 3):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_c(
            1024, #size
            samp_rate, #samp_rate
            "TX IQ Waveform", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-2, 2)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(True)
        self.qtgui_time_sink_x_0.enable_grid(True)
        self.qtgui_time_sink_x_0.enable_axis_labels(True)
        self.qtgui_time_sink_x_0.enable_control_panel(False)
        self.qtgui_time_sink_x_0.enable_stem_plot(False)


        labels = ['Real', 'Imag', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['blue', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 0.6, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(2):
            if len(labels[i]) == 0:
                if (i % 2 == 0):
                    self.qtgui_time_sink_x_0.set_line_label(i, "Re{{Data {0}}}".format(i/2))
                else:
                    self.qtgui_time_sink_x_0.set_line_label(i, "Im{{Data {0}}}".format(i/2))
            else:
                self.qtgui_time_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_time_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_time_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_time_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_x_0_win = sip.wrapinstance(self.qtgui_time_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 1, 2, 1, 1)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(2, 3):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            1024, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate, #bw
            "DVB-S2 Spectrum", #name
            2,
            None # parent
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis((-140), 10)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(True)
        self.qtgui_freq_sink_x_0.set_fft_average(0.1)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)
        self.qtgui_freq_sink_x_0.set_fft_window_normalized(False)



        labels = ['TX Spectrum', 'RX Spectrum', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(2):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 1, 0, 1, 1)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 1):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_const_sink_x_1 = qtgui.const_sink_c(
            1024, #size
            "", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_const_sink_x_1.set_update_time(0.10)
        self.qtgui_const_sink_x_1.set_y_axis((-2), 2)
        self.qtgui_const_sink_x_1.set_x_axis((-2), 2)
        self.qtgui_const_sink_x_1.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, "")
        self.qtgui_const_sink_x_1.enable_autoscale(False)
        self.qtgui_const_sink_x_1.enable_grid(False)
        self.qtgui_const_sink_x_1.enable_axis_labels(True)


        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        styles = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        markers = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_const_sink_x_1.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_const_sink_x_1.set_line_label(i, labels[i])
            self.qtgui_const_sink_x_1.set_line_width(i, widths[i])
            self.qtgui_const_sink_x_1.set_line_color(i, colors[i])
            self.qtgui_const_sink_x_1.set_line_style(i, styles[i])
            self.qtgui_const_sink_x_1.set_line_marker(i, markers[i])
            self.qtgui_const_sink_x_1.set_line_alpha(i, alphas[i])

        self._qtgui_const_sink_x_1_win = sip.wrapinstance(self.qtgui_const_sink_x_1.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_const_sink_x_1_win)
        self.qtgui_const_sink_x_0 = qtgui.const_sink_c(
            2048, #size
            "RX Constellation", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_const_sink_x_0.set_update_time(0.10)
        self.qtgui_const_sink_x_0.set_y_axis((-2), 2)
        self.qtgui_const_sink_x_0.set_x_axis((-2), 2)
        self.qtgui_const_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, "")
        self.qtgui_const_sink_x_0.enable_autoscale(True)
        self.qtgui_const_sink_x_0.enable_grid(True)
        self.qtgui_const_sink_x_0.enable_axis_labels(True)


        labels = ['Received IQ', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        styles = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        markers = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_const_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_const_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_const_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_const_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_const_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_const_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_const_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_const_sink_x_0_win = sip.wrapinstance(self.qtgui_const_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_const_sink_x_0_win, 1, 1, 1, 1)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(1, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.dvbs2acm_snr_estimator_0 = dvbs2acm.snr_estimator(estimator_type=dvbs2acm.SnrEstimatorType.HYBRID, frame_size=dvbs2acm.FrameSize.NORMAL, pilots=True, avg_frames=4, report_period=8, kalman_filter=True)
        self.dvbs2acm_pl_sync_acm_0 = dvbs2acm.pl_sync_acm(threshold=0.7, avg_frames=4)
        self.dvbs2acm_pl_framer_acm_0 = dvbs2acm.pl_framer_acm(initial_modcod=4, pilots=True, rolloff=0.20)
        self.dvbs2acm_modulator_acm_0 = dvbs2acm.modulator_acm(initial_modcod=4, rolloff=0.20)
        self.dvbs2acm_leo_channel_0 = dvbs2acm.leo_channel(
            sample_rate=samp_rate,
            altitude_km=altitude_km,
            freq_hz=8.025e9,
            tx_power_dbw=20.0,
            tx_gain_dbi=35.0,
            rx_gain_dbi=40.0,
            noise_temp_k=150.0,
            rain_rate_mm_hr=rain_rate,
            min_elevation_deg=5.0,
            update_period_ms=100.0)
        self.dvbs2acm_fec_encoder_acm_0 = dvbs2acm.fec_encoder_acm(frame_size=dvbs2acm.FrameSize.NORMAL, ldpc_algorithm=dvbs2acm.LdpcAlgorithm.ONESIDE, initial_modcod=4)
        self.dvbs2acm_fec_decoder_acm_0 = dvbs2acm.fec_decoder_acm(frame_size=dvbs2acm.FrameSize.NORMAL, ldpc_algorithm=dvbs2acm.LdpcAlgorithm.ONESIDE, max_iter=20, initial_modcod=4)
        self.dvbs2acm_demodulator_acm_0 = dvbs2acm.demodulator_acm(initial_modcod=4, noise_var=0.1)
        self.dvbs2acm_bb_framer_acm_0 = dvbs2acm.bb_framer_acm(frame_size=dvbs2acm.FrameSize.NORMAL, stream_type=dvbs2acm.StreamType.TRANSPORT, pilots=True, initial_modcod=4)
        self.dvbs2acm_acm_feedback_0 = dvbs2acm.acm_feedback(report_period_ms=100.0, snr_alpha=0.1, ber_alpha=0.05)
        self.dvbs2acm_acm_controller_0 = dvbs2acm.acm_controller(acm_mode=dvbs2acm.AcmMode.ACM, initial_modcod=4, target_ber=(1e-7), snr_margin_db=1.0, hysteresis_db=0.3, history_len=16, use_ai=True, ai_socket="tcp://localhost:5557", frame_size=dvbs2acm.FrameSize.NORMAL)
        self.channels_channel_model_0 = channels.channel_model(
            noise_voltage=noise_amp,
            frequency_offset=0.0,
            epsilon=1.0,
            taps=[1.0+0j],
            noise_seed=0,
            block_tags=False)
        self.blocks_throttle2_0 = blocks.throttle( gr.sizeof_gr_complex*1, samp_rate, True, 0 if "auto" == "auto" else max( int(float(0.1) * samp_rate) if "auto" == "time" else int(0.1), 1) )
        self.blocks_null_sink_0 = blocks.null_sink(gr.sizeof_char*1)
        self.blocks_message_debug_1 = blocks.message_debug(True, gr.log_levels.info)
        self.blocks_message_debug_0 = blocks.message_debug(True, gr.log_levels.info)
        self.analog_random_source_x_0 = blocks.vector_source_b(list(map(int, numpy.random.randint(0, 255, 188000))), True)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.dvbs2acm_acm_controller_0, 'modcod_out'), (self.blocks_message_debug_0, 'print'))
        self.msg_connect((self.dvbs2acm_acm_controller_0, 'stats_out'), (self.blocks_message_debug_0, 'store'))
        self.msg_connect((self.dvbs2acm_acm_controller_0, 'modcod_out'), (self.dvbs2acm_bb_framer_acm_0, 'modcod_in'))
        self.msg_connect((self.dvbs2acm_acm_feedback_0, 'feedback_out'), (self.dvbs2acm_acm_controller_0, 'snr_in'))
        self.msg_connect((self.dvbs2acm_fec_decoder_acm_0, 'ber_out'), (self.dvbs2acm_acm_feedback_0, 'ber_in'))
        self.msg_connect((self.dvbs2acm_snr_estimator_0, 'snr_out'), (self.dvbs2acm_acm_feedback_0, 'snr_in'))
        self.connect((self.analog_random_source_x_0, 0), (self.dvbs2acm_bb_framer_acm_0, 0))
        self.connect((self.blocks_throttle2_0, 0), (self.dvbs2acm_leo_channel_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.dvbs2acm_pl_sync_acm_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.dvbs2acm_snr_estimator_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.qtgui_const_sink_x_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.dvbs2acm_bb_framer_acm_0, 0), (self.dvbs2acm_fec_encoder_acm_0, 0))
        self.connect((self.dvbs2acm_demodulator_acm_0, 0), (self.dvbs2acm_fec_decoder_acm_0, 0))
        self.connect((self.dvbs2acm_fec_decoder_acm_0, 0), (self.blocks_null_sink_0, 0))
        self.connect((self.dvbs2acm_fec_encoder_acm_0, 0), (self.dvbs2acm_modulator_acm_0, 0))
        self.connect((self.dvbs2acm_leo_channel_0, 0), (self.channels_channel_model_0, 0))
        self.connect((self.dvbs2acm_modulator_acm_0, 0), (self.dvbs2acm_pl_framer_acm_0, 0))
        self.connect((self.dvbs2acm_modulator_acm_0, 0), (self.qtgui_const_sink_x_1, 0))
        self.connect((self.dvbs2acm_pl_framer_acm_0, 0), (self.blocks_throttle2_0, 0))
        self.connect((self.dvbs2acm_pl_sync_acm_0, 0), (self.dvbs2acm_demodulator_acm_0, 0))
        self.connect((self.dvbs2acm_pl_sync_acm_0, 0), (self.qtgui_freq_sink_x_0, 1))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "acm_loopback")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_snr_db(self):
        return self.snr_db

    def set_snr_db(self, snr_db):
        self.snr_db = snr_db
        self.set_noise_amp(10**(-self.snr_db/20.0))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.blocks_throttle2_0.set_sample_rate(self.samp_rate)
        self.qtgui_freq_sink_x_0.set_frequency_range(0, self.samp_rate)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)

    def get_rain_rate(self):
        return self.rain_rate

    def set_rain_rate(self, rain_rate):
        self.rain_rate = rain_rate

    def get_noise_amp(self):
        return self.noise_amp

    def set_noise_amp(self, noise_amp):
        self.noise_amp = noise_amp
        self.channels_channel_model_0.set_noise_voltage(self.noise_amp)

    def get_altitude_km(self):
        return self.altitude_km

    def set_altitude_km(self, altitude_km):
        self.altitude_km = altitude_km




def main(top_block_cls=acm_loopback, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
