#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: DVB-S2 ACM Loopback Simulation
# Author: Pasindu Manujaya
# Description: DVB-S2 ACM Closed-Loop Software Simulation. Full TX -> LEO Channel -> AWGN -> RX -> ACM Controller -> feedback loop entirely in software. Architecture: Random Source -> BB Framer -> FEC Encoder -> Modulator -> PL Framer -> Throttle -> LEO Channel -> AWGN Channel -> PL Sync -> SNR Estimator -> Demodulator -> FEC Decoder -> ACM Feedback -> ACM Controller -> BB Framer (loopback).
# GNU Radio version: 3.10.9.2

from PyQt5 import Qt
from gnuradio import qtgui
from PyQt5 import QtCore
from gnuradio import blocks
import numpy
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
        gr.top_block.__init__(self, "DVB-S2 ACM Loopback Simulation", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("DVB-S2 ACM Loopback Simulation")
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
        self.symbol_rate = symbol_rate = 5e6
        self.snr_db = snr_db = 33
        self.samp_rate = samp_rate = symbol_rate
        self.rain_rate = rain_rate = 5
        self.initial_modcod = initial_modcod = 4

        ##################################################
        # Blocks
        ##################################################

        self.tab = Qt.QTabWidget()
        self.tab_widget_0 = Qt.QWidget()
        self.tab_layout_0 = Qt.QBoxLayout(Qt.QBoxLayout.TopToBottom, self.tab_widget_0)
        self.tab_grid_layout_0 = Qt.QGridLayout()
        self.tab_layout_0.addLayout(self.tab_grid_layout_0)
        self.tab.addTab(self.tab_widget_0, 'Control')
        self.tab_widget_1 = Qt.QWidget()
        self.tab_layout_1 = Qt.QBoxLayout(Qt.QBoxLayout.TopToBottom, self.tab_widget_1)
        self.tab_grid_layout_1 = Qt.QGridLayout()
        self.tab_layout_1.addLayout(self.tab_grid_layout_1)
        self.tab.addTab(self.tab_widget_1, 'Constellations')
        self.tab_widget_2 = Qt.QWidget()
        self.tab_layout_2 = Qt.QBoxLayout(Qt.QBoxLayout.TopToBottom, self.tab_widget_2)
        self.tab_grid_layout_2 = Qt.QGridLayout()
        self.tab_layout_2.addLayout(self.tab_grid_layout_2)
        self.tab.addTab(self.tab_widget_2, 'Spectrum')
        self.tab_widget_3 = Qt.QWidget()
        self.tab_layout_3 = Qt.QBoxLayout(Qt.QBoxLayout.TopToBottom, self.tab_widget_3)
        self.tab_grid_layout_3 = Qt.QGridLayout()
        self.tab_layout_3.addLayout(self.tab_grid_layout_3)
        self.tab.addTab(self.tab_widget_3, 'Time / Waterfall')
        self.top_layout.addWidget(self.tab)
        self._rain_rate_range = qtgui.Range(0, 50, 1, 5, 200)
        self._rain_rate_win = qtgui.RangeWidget(self._rain_rate_range, self.set_rain_rate, "Rain Rate (mm/hr)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.tab_grid_layout_0.addWidget(self._rain_rate_win, 1, 0, 1, 2)
        for r in range(1, 2):
            self.tab_grid_layout_0.setRowStretch(r, 1)
        for c in range(0, 2):
            self.tab_grid_layout_0.setColumnStretch(c, 1)
        self.tx_constellation_sink = qtgui.const_sink_c(
            1024, #size
            "TX Signal (before channel)", #name
            1, #number of inputs
            None # parent
        )
        self.tx_constellation_sink.set_update_time(0.10)
        self.tx_constellation_sink.set_y_axis((-2), 2)
        self.tx_constellation_sink.set_x_axis((-2), 2)
        self.tx_constellation_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, '')
        self.tx_constellation_sink.enable_autoscale(False)
        self.tx_constellation_sink.enable_grid(False)
        self.tx_constellation_sink.enable_axis_labels(True)


        labels = ['TX Constellation (Modulator out)', '', '', '', '',
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
                self.tx_constellation_sink.set_line_label(i, "Data {0}".format(i))
            else:
                self.tx_constellation_sink.set_line_label(i, labels[i])
            self.tx_constellation_sink.set_line_width(i, widths[i])
            self.tx_constellation_sink.set_line_color(i, colors[i])
            self.tx_constellation_sink.set_line_style(i, styles[i])
            self.tx_constellation_sink.set_line_marker(i, markers[i])
            self.tx_constellation_sink.set_line_alpha(i, alphas[i])

        self._tx_constellation_sink_win = sip.wrapinstance(self.tx_constellation_sink.qwidget(), Qt.QWidget)
        self.tab_grid_layout_1.addWidget(self._tx_constellation_sink_win, 0, 0, 1, 1)
        for r in range(0, 1):
            self.tab_grid_layout_1.setRowStretch(r, 1)
        for c in range(0, 1):
            self.tab_grid_layout_1.setColumnStretch(c, 1)
        self.throttle = blocks.throttle(gr.sizeof_gr_complex*1, samp_rate,True)
        self.snr_estimator = dvbs2acm.snr_estimator(estimator_type=dvbs2acm.SnrEstimatorType.HYBRID, frame_size=dvbs2acm.FrameSize.NORMAL, pilots=True, avg_frames=4, report_period=1, kalman_filter=True)
        self._snr_db_range = qtgui.Range(20, 50, 0.5, 33, 200)
        self._snr_db_win = qtgui.RangeWidget(self._snr_db_range, self.set_snr_db, "SNR (dB)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.tab_grid_layout_0.addWidget(self._snr_db_win, 0, 0, 1, 2)
        for r in range(0, 1):
            self.tab_grid_layout_0.setRowStretch(r, 1)
        for c in range(0, 2):
            self.tab_grid_layout_0.setColumnStretch(c, 1)
        self.rx_constellation_sink = qtgui.const_sink_c(
            1024, #size
            "RX Signal (after PL Sync)", #name
            1, #number of inputs
            None # parent
        )
        self.rx_constellation_sink.set_update_time(0.10)
        self.rx_constellation_sink.set_y_axis((-2), 2)
        self.rx_constellation_sink.set_x_axis((-2), 2)
        self.rx_constellation_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, '')
        self.rx_constellation_sink.enable_autoscale(False)
        self.rx_constellation_sink.enable_grid(False)
        self.rx_constellation_sink.enable_axis_labels(True)


        labels = ['RX Constellation (PL Sync out)', '', '', '', '',
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
                self.rx_constellation_sink.set_line_label(i, "Data {0}".format(i))
            else:
                self.rx_constellation_sink.set_line_label(i, labels[i])
            self.rx_constellation_sink.set_line_width(i, widths[i])
            self.rx_constellation_sink.set_line_color(i, colors[i])
            self.rx_constellation_sink.set_line_style(i, styles[i])
            self.rx_constellation_sink.set_line_marker(i, markers[i])
            self.rx_constellation_sink.set_line_alpha(i, alphas[i])

        self._rx_constellation_sink_win = sip.wrapinstance(self.rx_constellation_sink.qwidget(), Qt.QWidget)
        self.tab_grid_layout_1.addWidget(self._rx_constellation_sink_win, 0, 1, 1, 1)
        for r in range(0, 1):
            self.tab_grid_layout_1.setRowStretch(r, 1)
        for c in range(1, 2):
            self.tab_grid_layout_1.setColumnStretch(c, 1)
        self.qtgui_waterfall_sink = qtgui.waterfall_sink_c(
            1024, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate, #bw
            "Waterfall (time-frequency)", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_waterfall_sink.set_update_time(0.10)
        self.qtgui_waterfall_sink.enable_grid(False)
        self.qtgui_waterfall_sink.enable_axis_labels(True)



        labels = ['', '', '', '', '',
                  '', '', '', '', '']
        colors = [0, 0, 0, 0, 0,
                  0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
                  1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_waterfall_sink.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_waterfall_sink.set_line_label(i, labels[i])
            self.qtgui_waterfall_sink.set_color_map(i, colors[i])
            self.qtgui_waterfall_sink.set_line_alpha(i, alphas[i])

        self.qtgui_waterfall_sink.set_intensity_range(-140, 10)

        self._qtgui_waterfall_sink_win = sip.wrapinstance(self.qtgui_waterfall_sink.qwidget(), Qt.QWidget)

        self.tab_grid_layout_3.addWidget(self._qtgui_waterfall_sink_win, 0, 1, 1, 1)
        for r in range(0, 1):
            self.tab_grid_layout_3.setRowStretch(r, 1)
        for c in range(1, 2):
            self.tab_grid_layout_3.setColumnStretch(c, 1)
        self.qtgui_time_sink = qtgui.time_sink_c(
            1024, #size
            samp_rate, #samp_rate
            "Received Signal Envelope", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink.set_update_time(0.10)
        self.qtgui_time_sink.set_y_axis(-2, 2)

        self.qtgui_time_sink.set_y_label('Amplitude', "")

        self.qtgui_time_sink.enable_tags(True)
        self.qtgui_time_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, '')
        self.qtgui_time_sink.enable_autoscale(False)
        self.qtgui_time_sink.enable_grid(False)
        self.qtgui_time_sink.enable_axis_labels(True)
        self.qtgui_time_sink.enable_control_panel(False)
        self.qtgui_time_sink.enable_stem_plot(False)


        labels = ['Signal Amplitude', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['blue', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(2):
            if len(labels[i]) == 0:
                if (i % 2 == 0):
                    self.qtgui_time_sink.set_line_label(i, "Re{{Data {0}}}".format(i/2))
                else:
                    self.qtgui_time_sink.set_line_label(i, "Im{{Data {0}}}".format(i/2))
            else:
                self.qtgui_time_sink.set_line_label(i, labels[i])
            self.qtgui_time_sink.set_line_width(i, widths[i])
            self.qtgui_time_sink.set_line_color(i, colors[i])
            self.qtgui_time_sink.set_line_style(i, styles[i])
            self.qtgui_time_sink.set_line_marker(i, markers[i])
            self.qtgui_time_sink.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_win = sip.wrapinstance(self.qtgui_time_sink.qwidget(), Qt.QWidget)
        self.tab_grid_layout_3.addWidget(self._qtgui_time_sink_win, 0, 0, 1, 1)
        for r in range(0, 1):
            self.tab_grid_layout_3.setRowStretch(r, 1)
        for c in range(0, 1):
            self.tab_grid_layout_3.setColumnStretch(c, 1)
        self.qtgui_freq_sink = qtgui.freq_sink_c(
            1024, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate, #bw
            "DVB-S2 Spectrum (pre- vs post-PL Sync)", #name
            2,
            None # parent
        )
        self.qtgui_freq_sink.set_update_time(0.10)
        self.qtgui_freq_sink.set_y_axis((-140), 10)
        self.qtgui_freq_sink.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, '')
        self.qtgui_freq_sink.enable_autoscale(False)
        self.qtgui_freq_sink.enable_grid(False)
        self.qtgui_freq_sink.set_fft_average(0.2)
        self.qtgui_freq_sink.enable_axis_labels(True)
        self.qtgui_freq_sink.enable_control_panel(False)
        self.qtgui_freq_sink.set_fft_window_normalized(False)



        labels = ['Pre-Sync (AWGN out)', 'Post-Sync (PL Sync out)', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(2):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink.set_line_label(i, labels[i])
            self.qtgui_freq_sink.set_line_width(i, widths[i])
            self.qtgui_freq_sink.set_line_color(i, colors[i])
            self.qtgui_freq_sink.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_win = sip.wrapinstance(self.qtgui_freq_sink.qwidget(), Qt.QWidget)
        self.tab_grid_layout_2.addWidget(self._qtgui_freq_sink_win, 0, 0, 1, 2)
        for r in range(0, 1):
            self.tab_grid_layout_2.setRowStretch(r, 1)
        for c in range(0, 2):
            self.tab_grid_layout_2.setColumnStretch(c, 1)
        self.pl_sync = dvbs2acm.pl_sync_acm(threshold=0.7, avg_frames=4)
        self.pl_framer = dvbs2acm.pl_framer_acm(initial_modcod=initial_modcod, pilots=True, rolloff=0.20)
        self.orbit_visualizer = dvbs2acm.orbit_visualizer(
            title="DVB-S2 ACM — LEO Pass Visualizer",
            pass_inclination_deg=0.0,
            gs_lat_deg=0.0,
            gs_lon_deg=0.0,
            altitude_km=500.0)
        self.null_sink = blocks.null_sink(gr.sizeof_char*1)
        self.modulator = dvbs2acm.modulator_acm(initial_modcod=initial_modcod, rolloff=0.2)
        self.leo_channel = dvbs2acm.leo_channel(
            sample_rate=samp_rate,
            altitude_km=500.0,
            freq_hz=8.025e9,
            tx_power_dbw=0.0,
            tx_gain_dbi=10.0,
            rx_gain_dbi=37.0,
            noise_temp_k=150.0,
            rain_rate_mm_hr=rain_rate,
            min_elevation_deg=5.0,
            update_period_ms=100.0,
            snr_offset_db=0.0,
            time_acceleration=10.0,
            fading_coherence_s=30.0)
        self.fec_encoder = dvbs2acm.fec_encoder_acm(frame_size=dvbs2acm.FrameSize.NORMAL, ldpc_algorithm=dvbs2acm.LdpcAlgorithm.ONESIDE, initial_modcod=initial_modcod)
        self.fec_decoder = dvbs2acm.fec_decoder_acm(frame_size=dvbs2acm.FrameSize.NORMAL, ldpc_algorithm=dvbs2acm.LdpcAlgorithm.ONESIDE, max_iter=20, initial_modcod=initial_modcod)
        self.demodulator = dvbs2acm.demodulator_acm(initial_modcod=initial_modcod, noise_var=0.1)
        self.bb_framer = dvbs2acm.bb_framer_acm(frame_size=dvbs2acm.FrameSize.NORMAL, stream_type=dvbs2acm.StreamType.TRANSPORT, pilots=True, initial_modcod=initial_modcod)
        self.awgn_channel = channels.channel_model(
            noise_voltage=0,
            frequency_offset=0.0,
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False)
        self.analog_random_source = blocks.vector_source_b(list(map(int, numpy.random.randint(0, 256, 1000))), True)
        self.acm_feedback = dvbs2acm.acm_feedback(report_period_ms=100.0, snr_alpha=0.1, ber_alpha=0.05)
        self.acm_controller = dvbs2acm.acm_controller(acm_mode=dvbs2acm.AcmMode.ACM, initial_modcod=initial_modcod, target_ber=(1e-7), snr_margin_db=1.0, hysteresis_db=0.5, history_len=16, use_ai=True, ai_socket="tcp://localhost:5557", frame_size=dvbs2acm.FrameSize.NORMAL)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.acm_controller, 'modcod_out'), (self.bb_framer, 'modcod_in'))
        self.msg_connect((self.acm_controller, 'modcod_out'), (self.orbit_visualizer, 'modcod_in'))
        self.msg_connect((self.acm_feedback, 'feedback_out'), (self.acm_controller, 'snr_in'))
        self.msg_connect((self.fec_decoder, 'ber_out'), (self.acm_feedback, 'ber_in'))
        self.msg_connect((self.leo_channel, 'channel_state'), (self.acm_controller, 'channel_state_in'))
        self.msg_connect((self.leo_channel, 'channel_state'), (self.orbit_visualizer, 'channel_state_in'))
        self.msg_connect((self.snr_estimator, 'snr_out'), (self.acm_feedback, 'snr_in'))
        self.connect((self.analog_random_source, 0), (self.bb_framer, 0))
        self.connect((self.awgn_channel, 0), (self.pl_sync, 0))
        self.connect((self.awgn_channel, 0), (self.qtgui_freq_sink, 0))
        self.connect((self.awgn_channel, 0), (self.qtgui_time_sink, 0))
        self.connect((self.awgn_channel, 0), (self.qtgui_waterfall_sink, 0))
        self.connect((self.awgn_channel, 0), (self.snr_estimator, 0))
        self.connect((self.bb_framer, 0), (self.fec_encoder, 0))
        self.connect((self.demodulator, 0), (self.fec_decoder, 0))
        self.connect((self.fec_decoder, 0), (self.null_sink, 0))
        self.connect((self.fec_encoder, 0), (self.modulator, 0))
        self.connect((self.leo_channel, 0), (self.awgn_channel, 0))
        self.connect((self.modulator, 0), (self.pl_framer, 0))
        self.connect((self.modulator, 0), (self.tx_constellation_sink, 0))
        self.connect((self.pl_framer, 0), (self.throttle, 0))
        self.connect((self.pl_sync, 0), (self.demodulator, 0))
        self.connect((self.pl_sync, 0), (self.qtgui_freq_sink, 1))
        self.connect((self.pl_sync, 0), (self.rx_constellation_sink, 0))
        self.connect((self.throttle, 0), (self.leo_channel, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "acm_loopback")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_symbol_rate(self):
        return self.symbol_rate

    def set_symbol_rate(self, symbol_rate):
        self.symbol_rate = symbol_rate
        self.set_samp_rate(self.symbol_rate)

    def get_snr_db(self):
        return self.snr_db

    def set_snr_db(self, snr_db):
        self.snr_db = snr_db

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.qtgui_freq_sink.set_frequency_range(0, self.samp_rate)
        self.qtgui_time_sink.set_samp_rate(self.samp_rate)
        self.qtgui_waterfall_sink.set_frequency_range(0, self.samp_rate)
        self.throttle.set_sample_rate(self.samp_rate)

    def get_rain_rate(self):
        return self.rain_rate

    def set_rain_rate(self, rain_rate):
        self.rain_rate = rain_rate

    def get_initial_modcod(self):
        return self.initial_modcod

    def set_initial_modcod(self, initial_modcod):
        self.initial_modcod = initial_modcod
        self.acm_controller.force_modcod(self.initial_modcod)




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
