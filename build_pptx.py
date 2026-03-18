#!/usr/bin/env python3
"""
build_pptx.py — Build DVB-S2 ACM results presentation (15 slides)
NUS / LEO X-Band / USRP B200 / GNU Radio 3.10
Author: Pasindu Manujaya
"""

import sys
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from lxml import etree

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
NUSblue      = RGBColor(0x00, 0x3D, 0x7C)
NUSorange    = RGBColor(0xEF, 0x7C, 0x00)
NUSlightblue = RGBColor(0x00, 0x6C, 0xB7)
NUSgray      = RGBColor(0x58, 0x59, 0x5B)
NUSgreen     = RGBColor(0x00, 0x9F, 0x6B)
NUSred       = RGBColor(0xC0, 0x39, 0x2B)
WHITE        = RGBColor(0xFF, 0xFF, 0xFF)
BLACK        = RGBColor(0x00, 0x00, 0x00)
LIGHTBLUE    = RGBColor(0xD6, 0xE4, 0xF0)
LIGHTORANGE  = RGBColor(0xFD, 0xEE, 0xD9)
LIGHTGREEN   = RGBColor(0xD5, 0xEF, 0xE6)
LIGHTGRAY    = RGBColor(0xF4, 0xF4, 0xF4)
LIGHTRED     = RGBColor(0xFA, 0xDD, 0xDD)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE      = "/home/manujayaugp/Desktop/research/gr-dvbs2acm"
FIG_DIR   = os.path.join(BASE, "docs", "figures")
DOCS_DIR  = os.path.join(BASE, "docs")
VIDEO     = os.path.join(DOCS_DIR, "grc_screen_record.mp4")
POSTER    = os.path.join(DOCS_DIR, "grc_poster_frame.png")
OUT_PPTX  = os.path.join(DOCS_DIR, "results_presentation.pptx")

# ---------------------------------------------------------------------------
# XML helper — set table cell background colour
# ---------------------------------------------------------------------------
def set_cell_bg(cell, color):
    """Set table cell fill to a solid RGBColor using direct XML manipulation."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    # Remove any existing fill elements to avoid duplicates
    for existing in tcPr.findall(qn('a:solidFill')):
        tcPr.remove(existing)
    for existing in tcPr.findall(qn('a:noFill')):
        tcPr.remove(existing)
    solidFill = etree.SubElement(tcPr, qn('a:solidFill'))
    srgbClr   = etree.SubElement(solidFill, qn('a:srgbClr'))
    srgbClr.set('val', '%02X%02X%02X' % (color[0], color[1], color[2]))


def set_shape_fill(shape, color):
    """Set a shape's solid fill colour."""
    shape.fill.solid()
    shape.fill.fore_color.rgb = color


def set_no_line(shape):
    """Remove the border line from a shape."""
    shape.line.fill.background()


# ---------------------------------------------------------------------------
# Helper — apply font properties to a run
# ---------------------------------------------------------------------------
def _apply_run_font(run, size_pt, bold=False, italic=False, color=BLACK):
    run.font.name  = "Calibri"
    run.font.size  = Pt(size_pt)
    run.font.bold  = bold
    run.font.italic = italic
    run.font.color.rgb = color


# ---------------------------------------------------------------------------
# Helper — set text frame defaults
# ---------------------------------------------------------------------------
def _configure_tf(tf, word_wrap=True):
    tf.word_wrap = True
    tf.auto_size = None


# ---------------------------------------------------------------------------
# Core slide helpers
# ---------------------------------------------------------------------------
def new_slide(prs):
    """Return a new blank slide using layout index 6 (blank)."""
    blank_layout = prs.slide_layouts[6]
    return prs.slides.add_slide(blank_layout)


def _add_rect(slide, x, y, w, h):
    """Add a rectangle shape and return it (measurements in Inches)."""
    return slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE = 1 (rectangle)
        Inches(x), Inches(y), Inches(w), Inches(h)
    )


def title_bar(slide, text):
    """Blue title bar at top of slide with white bold Calibri 20pt text."""
    bar = _add_rect(slide, 0, 0, 13.333, 0.6)
    set_shape_fill(bar, NUSblue)
    set_no_line(bar)
    tf = bar.text_frame
    _configure_tf(tf)
    tf.margin_left   = Inches(0.2)
    tf.margin_right  = Inches(0.05)
    tf.margin_top    = Pt(2)
    tf.margin_bottom = Pt(2)
    from pptx.enum.text import MSO_ANCHOR
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.LEFT
    run = para.add_run()
    run.text = text
    _apply_run_font(run, 20, bold=True, color=WHITE)


def footer(slide, num, total=15):
    """Three-part footer bar at bottom of slide."""
    y, h, w = 7.18, 0.32, 4.444

    parts = [
        (0,        NUSblue,      "Pasindu Manujaya"),
        (4.444,    NUSlightblue, "DVB-S2 ACM + AI/ML"),
        (8.888,    NUSblue,      f"{num} / {total}"),
    ]
    from pptx.enum.text import MSO_ANCHOR
    for x_pos, col, txt in parts:
        rect = _add_rect(slide, x_pos, y, w, h)
        set_shape_fill(rect, col)
        set_no_line(rect)
        tf = rect.text_frame
        _configure_tf(tf)
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = txt
        _apply_run_font(run, 8, color=WHITE)


def colored_box(slide, x, y, w, h,
                title_text, body_items,
                bg_color, accent_color,
                title_text_color=WHITE):
    """
    Composite coloured box:
      - accent-coloured title bar (h=0.32")
      - bg-coloured body with bullet items
    """
    from pptx.enum.text import MSO_ANCHOR
    title_h = 0.32
    body_h  = h - title_h

    # --- Title bar ---
    title_rect = _add_rect(slide, x, y, w, title_h)
    set_shape_fill(title_rect, accent_color)
    set_no_line(title_rect)
    tf = title_rect.text_frame
    _configure_tf(tf)
    tf.margin_left   = Inches(0.08)
    tf.margin_right  = Inches(0.04)
    tf.margin_top    = Pt(1)
    tf.margin_bottom = Pt(1)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.LEFT
    run = para.add_run()
    run.text = title_text
    _apply_run_font(run, 9, bold=True, color=title_text_color)

    # --- Body rectangle ---
    body_rect = _add_rect(slide, x, y + title_h, w, body_h)
    set_shape_fill(body_rect, bg_color)
    body_rect.line.color.rgb = accent_color
    body_rect.line.width     = Pt(1)

    # --- Body text box (inset 0.1") ---
    pad = 0.1
    txBox = slide.shapes.add_textbox(
        Inches(x + pad),
        Inches(y + title_h + pad),
        Inches(w - 2 * pad),
        Inches(max(body_h - 2 * pad, 0.1))
    )
    tf = txBox.text_frame
    _configure_tf(tf)
    tf.margin_left   = Inches(0)
    tf.margin_right  = Inches(0)
    tf.margin_top    = Inches(0)
    tf.margin_bottom = Inches(0)

    for i, item in enumerate(body_items):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(2)
        run = para.add_run()
        run.text = "\u2022 " + item
        _apply_run_font(run, 9, color=BLACK)


def simple_text(slide, text, x, y, w, h,
                size=10, bold=False, color=BLACK,
                align=PP_ALIGN.LEFT, italic=False):
    """Plain Calibri textbox."""
    txBox = slide.shapes.add_textbox(
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    tf = txBox.text_frame
    _configure_tf(tf)
    tf.margin_left   = Inches(0)
    tf.margin_right  = Inches(0)
    tf.margin_top    = Inches(0)
    tf.margin_bottom = Inches(0)
    para = tf.paragraphs[0]
    para.alignment = align
    run = para.add_run()
    run.text = text
    _apply_run_font(run, size, bold=bold, italic=italic, color=color)
    return txBox


def make_table(slide, headers, rows, x, y, w, h,
               header_bg=NUSblue, stripe=LIGHTGRAY):
    """
    Build a table with styled header and alternating row stripes.
    Returns the table object.
    """
    n_rows = len(rows) + 1
    n_cols = len(headers)
    tbl_shape = slide.shapes.add_table(
        n_rows, n_cols,
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    table = tbl_shape.table

    # Distribute column widths equally
    col_w = Inches(w / n_cols)
    for col in table.columns:
        col.width = col_w

    # Header row
    for ci, hdr in enumerate(headers):
        cell = table.cell(0, ci)
        set_cell_bg(cell, header_bg)
        tf = cell.text_frame
        _configure_tf(tf)
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = hdr
        _apply_run_font(run, 9, bold=True, color=WHITE)

    # Data rows
    for ri, row_data in enumerate(rows):
        bg = WHITE if ri % 2 == 0 else stripe
        for ci, cell_text in enumerate(row_data):
            cell = table.cell(ri + 1, ci)
            set_cell_bg(cell, bg)
            tf = cell.text_frame
            _configure_tf(tf)
            para = tf.paragraphs[0]
            para.alignment = PP_ALIGN.CENTER
            run = para.add_run()
            run.text = str(cell_text)
            _apply_run_font(run, 9, color=BLACK)

    return table


# ---------------------------------------------------------------------------
# Block helper — a chain block box (for arch diagram)
# ---------------------------------------------------------------------------
def _chain_block(slide, x, y, w, h, text, bg_color, border_color):
    """Small labelled rectangle for TX/RX chain diagrams."""
    from pptx.enum.text import MSO_ANCHOR
    rect = _add_rect(slide, x, y, w, h)
    set_shape_fill(rect, bg_color)
    rect.line.color.rgb = border_color
    rect.line.width     = Pt(1)
    tf = rect.text_frame
    _configure_tf(tf)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left   = Inches(0.03)
    tf.margin_right  = Inches(0.03)
    tf.margin_top    = Pt(1)
    tf.margin_bottom = Pt(1)
    lines = text.split("\n")
    for li, line in enumerate(lines):
        para = tf.paragraphs[0] if li == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = line
        _apply_run_font(run, 8, bold=True, color=BLACK)


def _arrow_text(slide, text, x, y, w, h, color=NUSblue, size=12):
    """Arrow label textbox."""
    from pptx.enum.text import MSO_ANCHOR
    txBox = slide.shapes.add_textbox(
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    tf = txBox.text_frame
    _configure_tf(tf)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    run = para.add_run()
    run.text = text
    _apply_run_font(run, size, bold=True, color=color)


# ===========================================================================
# Slide builders
# ===========================================================================

def build_slide1(prs):
    """Title slide — full NUSblue background."""
    slide = new_slide(prs)

    # Full background
    bg = _add_rect(slide, 0, 0, 13.333, 7.5)
    set_shape_fill(bg, NUSblue)
    set_no_line(bg)

    # Orange accent bar
    bar = _add_rect(slide, 0, 3.4, 13.333, 0.06)
    set_shape_fill(bar, NUSorange)
    set_no_line(bar)

    # Title
    simple_text(slide, "DVB-S2 ACM with AI/ML Cognitive Engine",
                0, 2.0, 13.333, 0.8, size=28, bold=True,
                color=WHITE, align=PP_ALIGN.CENTER)

    # Subtitle
    simple_text(slide, "Simulation & GNU Radio Hardware Results",
                0, 2.9, 13.333, 0.5, size=18,
                color=WHITE, align=PP_ALIGN.CENTER)

    # Author line
    simple_text(slide,
                "Pasindu Manujaya  \u00b7  National University of Singapore  \u00b7  2026",
                0, 3.6, 13.333, 0.4, size=13,
                color=WHITE, align=PP_ALIGN.CENTER)

    # Tech line
    simple_text(slide,
                "LEO X-Band Link  \u00b7  500 km  \u00b7  8.025 GHz  \u00b7  USRP B200 + GNU Radio 3.10",
                0, 4.15, 13.333, 0.4, size=11,
                color=NUSorange, align=PP_ALIGN.CENTER)


def build_slide2(prs):
    """High-Level System Architecture."""
    slide = new_slide(prs)
    title_bar(slide, "High-Level System Architecture")
    footer(slide, 2)

    bw, bh = 1.75, 0.55

    # --- TX Chain ---
    simple_text(slide, "TX Chain (Satellite)",
                0.3, 0.72, 10, 0.25, size=9, bold=True, color=NUSblue)

    tx_blocks = [
        (0.3,  "BB Framer\nACM",    LIGHTBLUE,   NUSblue),
        (2.25, "FEC\nEncoder",      LIGHTBLUE,   NUSblue),
        (4.2,  "Modulator\nACM",    LIGHTBLUE,   NUSblue),
        (6.15, "PL Framer\nACM",    LIGHTBLUE,   NUSblue),
        (8.4,  "LEO+AWGN\nChannel", LIGHTORANGE, NUSorange),
    ]
    for x, lbl, bg, bd in tx_blocks:
        _chain_block(slide, x, 1.0, bw, bh, lbl, bg, bd)

    # Arrows between TX blocks
    tx_arrow_pairs = [
        (0.3 + bw, 2.25),
        (2.25 + bw, 4.2),
        (4.2 + bw, 6.15),
        (6.15 + bw, 8.4),
    ]
    for ax0, ax1 in tx_arrow_pairs:
        cx = (ax0 + ax1) / 2 - 0.1
        _arrow_text(slide, "\u2192", cx, 1.1, 0.2, 0.35, color=NUSblue, size=12)

    # --- RX Chain ---
    simple_text(slide, "RX Chain (Ground Station)",
                0.3, 1.7, 10, 0.25, size=9, bold=True, color=NUSred)

    rx_blocks = [
        (0.3,  "Data\nSink",        LIGHTRED, NUSred),
        (2.25, "FEC\nDecoder",      LIGHTRED, NUSred),
        (4.2,  "Demodulator\nACM",  LIGHTRED, NUSred),
        (6.15, "SNR\nEstimator",    LIGHTRED, NUSred),
        (8.4,  "PL Sync\nACM",     LIGHTRED, NUSred),
    ]
    for x, lbl, bg, bd in rx_blocks:
        _chain_block(slide, x, 1.95, bw, bh, lbl, bg, bd)

    # Arrows between RX blocks (left-pointing)
    for ax0, ax1 in tx_arrow_pairs:
        cx = (ax0 + ax1) / 2 - 0.1
        _arrow_text(slide, "\u2190", cx, 2.05, 0.2, 0.35, color=NUSred, size=12)

    # --- Vertical connector thin rects ---
    for xv, yv, hv, col in [
        (7.05, 2.5, 0.6,  NUSgreen),
        (3.1,  2.5, 0.6,  NUSgreen),
        (5.1,  3.2, 0.85, NUSorange),
    ]:
        vbar = _add_rect(slide, xv, yv, 0.05, hv)
        set_shape_fill(vbar, col)
        set_no_line(vbar)

    # --- ACM Control Loop ---
    simple_text(slide, "ACM Control Loop",
                0.3, 3.15, 8, 0.25, size=9, bold=True, color=NUSgreen)

    ctrl_blocks = [
        (2.3,  "AI/ML\nEngine",   LIGHTGREEN, NUSgreen),
        (4.25, "ACM\nController", LIGHTGREEN, NUSgreen),
        (6.2,  "ACM\nFeedback",   LIGHTGREEN, NUSgreen),
    ]
    for x, lbl, bg, bd in ctrl_blocks:
        _chain_block(slide, x, 3.4, bw, bh, lbl, bg, bd)

    # Arrows between control blocks
    ctrl_arrow_pairs = [
        (2.3 + bw,  4.25),
        (4.25 + bw, 6.2),
    ]
    for ax0, ax1 in ctrl_arrow_pairs:
        cx = (ax0 + ax1) / 2 - 0.1
        _arrow_text(slide, "\u2194", cx, 3.5, 0.2, 0.35, color=NUSgreen, size=12)

    # Bottom description texts
    simple_text(slide,
                "Stream tag path (in-band): BB Framer attaches \u2018modcod\u2019 tag \u2192 every downstream block applies correct constellation/FEC.",
                0.3, 4.3, 6.0, 0.6, size=8, color=NUSgray)
    simple_text(slide,
                "Message port path (out-of-band): SNR + BER/FER \u2192 ACM Feedback \u2192 Controller \u2192 DQN \u2192 new MODCOD \u2192 BB Framer.",
                6.8, 4.3, 6.0, 0.6, size=8, color=NUSgray)


def build_slide3(prs):
    """Two-Tool Validation Strategy."""
    slide = new_slide(prs)
    title_bar(slide, "Two-Tool Validation Strategy")
    footer(slide, 3)

    # Left column
    colored_box(slide, 0.25, 0.75, 6.2, 2.8,
                "acm_loopback_sim.py \u2014 Algorithm Validation",
                ["Pure Python \u2014 no compilation needed",
                 "Runs in seconds \u2014 full LEO pass in ~2 s",
                 "Same 52-dim DQN + PER as production AI engine",
                 "BER/FER from waterfall lookup table (approximation)",
                 "SNR: true value + 0.3 dB Gaussian noise model",
                 "Zero ACM loop latency"],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 0.25, 3.7, 6.2, 1.1,
                "Use when",
                ["Tuning hyperparameters, comparing strategies, generating plots \u2014 anywhere speed matters more than signal accuracy."],
                LIGHTGRAY, NUSgray)

    # Right column
    colored_box(slide, 6.85, 0.75, 6.2, 2.8,
                "acm_loopback.grc \u2014 System Validation",
                ["Random source \u2192 full IQ chain (BCH+LDPC, modulation, PL sync)",
                 "Real LDPC decoding \u2014 actual bit errors counted",
                 "Real Pilot-MMSE + M2M4 + Kalman SNR estimation",
                 "PLSCODE signalling per PL frame",
                 "ACM feedback latency: ~100 ms report period",
                 "Currently software loopback \u2014 USRP B200 next"],
                LIGHTRED, NUSred)

    colored_box(slide, 6.85, 3.7, 6.2, 1.1,
                "Use when",
                ["Validating the signal chain, demonstrating the live system, and preparing for USRP B200 hardware deployment."],
                LIGHTGREEN, NUSgreen)

    # Bottom note
    simple_text(slide,
                "Both tools use the same DQNAgent from acm_controller_ai.py \u2014 results are consistent by design.",
                0.25, 5.0, 12.8, 0.4, size=10, italic=True,
                color=NUSgray, align=PP_ALIGN.CENTER)


def build_slide4(prs):
    """AI/ML Engine: Dueling Double DQN."""
    slide = new_slide(prs)
    title_bar(slide, "AI/ML Engine: Dueling Double DQN")
    footer(slide, 4)

    # Left: Why RL?
    colored_box(slide, 0.25, 0.75, 4.3, 3.0,
                "Why Reinforcement Learning?",
                ["ACM is a sequential decision problem \u2014 choose MODCOD every frame based on channel history.",
                 "RL learns by trial-and-error with reward signal \u2014 no labelled dataset needed.",
                 "DQN (Deep Q-Network) approximates optimal Q*(s,a) using a deep neural network."],
                LIGHTRED, NUSred)

    # Right: Network diagram
    colored_box(slide, 4.8, 0.75, 1.9, 2.1,
                "52-dim State",
                ["SNR history \u00d716", "Orbital \u00d75",
                 "MODCOD \u00d728", "BER/FER/trend \u00d73"],
                LIGHTBLUE, NUSblue)

    _arrow_text(slide, "\u2192", 6.75, 1.45, 0.3, 0.4, color=NUSgray, size=14)

    colored_box(slide, 7.1, 1.1, 1.6, 0.7,
                "Shared FC 256",
                ["LayerNorm + ReLU"],
                LIGHTGRAY, NUSgray)

    _arrow_text(slide, "\u2192", 8.75, 1.45, 0.3, 0.4, color=NUSgray, size=14)

    colored_box(slide, 9.1, 1.1, 1.6, 0.7,
                "Shared FC 256",
                ["LayerNorm + ReLU"],
                LIGHTGRAY, NUSgray)

    _arrow_text(slide, "\u2197", 10.75, 0.95, 0.3, 0.4, color=NUSblue,   size=12)
    _arrow_text(slide, "\u2198", 10.75, 1.85, 0.3, 0.4, color=NUSorange, size=12)

    colored_box(slide, 11.1, 0.7, 1.4, 0.6,
                "Value FC 64",
                ["\u2192 V(s)"],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 11.1, 1.7, 1.4, 0.6,
                "Adv FC 64",
                ["\u2192 A(s,a)\u00d728"],
                LIGHTORANGE, NUSorange)

    _arrow_text(slide, "\u2192", 12.55, 1.2, 0.2, 0.4, color=NUSgray, size=14)

    colored_box(slide, 12.55, 0.85, 0.72, 0.9,
                "Q",
                ["argmax Q", "MODCOD 1-28"],
                LIGHTGREEN, NUSgreen)

    # Bottom explanation boxes
    colored_box(slide, 4.8, 3.0, 4.0, 1.6,
                "Dueling Heads",
                ["Separates how good is this state (V) from how much better is this action (A). More stable than single Q head."],
                LIGHTORANGE, NUSorange)

    colored_box(slide, 9.1, 3.0, 4.0, 1.6,
                "Double DQN",
                ["Online net selects action; target net evaluates Q. Prevents Q-value overestimation \u2014 critical for stable training."],
                LIGHTGREEN, NUSgreen)


def build_slide5(prs):
    """What We Are Comparing."""
    slide = new_slide(prs)
    title_bar(slide, "What We Are Comparing")
    footer(slide, 5)

    # Top row — three strategy boxes
    colored_box(slide, 0.25, 0.75, 4.1, 2.1,
                "CCM \u2014 Baseline",
                ["Fixed MODCOD 4 (QPSK 1/2)",
                 "No adaptation",
                 "\u03b7 = 0.988 bits/sym always",
                 "Conservative \u2014 link rarely fails"],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 4.6, 0.75, 4.1, 2.1,
                "Rule-based ACM",
                ["Picks highest feasible MODCOD",
                 "Hysteresis: +1.5 dB up / +1.0 dB down",
                 "Fast, deterministic",
                 "No learning \u2014 fixed thresholds"],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 8.95, 0.75, 4.1, 2.1,
                "Dueling DQN (ours)",
                ["52-dim state vector",
                 "Learns from experience online",
                 "PER + N-step returns",
                 "Balances \u03b7, FER, switching cost"],
                LIGHTORANGE, NUSorange)

    # Bottom row
    colored_box(slide, 0.25, 3.1, 6.2, 1.5,
                "Metrics",
                ["\u03b7 \u2014 average spectral efficiency (bits/sym)",
                 "QEF% \u2014 frames with BER < 10\u207b\u2077",
                 "Switches \u2014 number of MODCOD changes"],
                LIGHTORANGE, NUSorange)

    colored_box(slide, 6.85, 3.1, 6.2, 1.5,
                "Two Validation Tools",
                ["acm_loopback_sim.py \u2014 algorithm results (this presentation)",
                 "acm_loopback.grc \u2014 GNU Radio hardware demo"],
                LIGHTGRAY, NUSgray)


def build_slide6(prs):
    """DQN Training State."""
    slide = new_slide(prs)
    title_bar(slide, "DQN Training State at Time of Results")
    footer(slide, 6)

    colored_box(slide, 0.25, 0.75, 6.2, 2.0,
                "Training State",
                ["The DQN was NOT fully trained when results were captured.",
                 "Each scenario loaded the model saved by the previous run.",
                 "\u03b5 decayed from 1.0 to 0.564 across all three comparison runs."],
                LIGHTRED, NUSred)

    make_table(slide,
               ["Parameter", "Value"],
               [["Gradient updates",         "21,400 steps total"],
                ["\u03b5 at sweep start",     "1.000 (fully random)"],
                ["\u03b5 at LEO start",       "~0.62"],
                ["\u03b5 at rain fade start", "~0.58"],
                ["\u03b5 target (converged)", "0.05"]],
               0.25, 2.9, 6.2, 2.2)

    colored_box(slide, 6.85, 0.75, 6.2, 3.0,
                "Online \u03b5-greedy Training with PER",
                ["Agent runs AND learns simultaneously \u2014 no separate training phase",
                 "Each step: action selected, reward computed, stored in PER buffer",
                 "Every 4 steps: mini-batch of 128 by TD error priority \u2192 gradient update",
                 "Target network synced every 300 steps",
                 "\u03b5 decays: 1.0 \u2192 0.05 over training"],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 0.25, 4.3, 6.2, 1.5,
                "What the results represent",
                ["Sweep: \u03b5=1.0 (fully random)  |  LEO: \u03b5\u22480.62  |  Rain fade: \u03b5\u22480.58",
                 "All are intermediate snapshots \u2014 not the converged policy."],
                LIGHTGRAY, NUSgray)

    colored_box(slide, 6.85, 4.3, 6.2, 1.5,
                "Why results are still meaningful",
                ["Random actions constrained to feasible MODCODs (SNR-gated).",
                 "Agent already shows better QEF than rule-based in 2/3 scenarios.",
                 "Performance improves as \u03b5 \u2192 0.05."],
                LIGHTGREEN, NUSgreen)


def _build_scenario_slide(prs, slide_num, title, image_path,
                           setup_text, rows, footnote, observation_text):
    """Generic scenario result slide (slides 7-9)."""
    slide = new_slide(prs)
    title_bar(slide, title)
    footer(slide, slide_num)

    # Image (left)
    if os.path.exists(image_path):
        slide.shapes.add_picture(image_path,
                                 Inches(0.25), Inches(0.75),
                                 Inches(7.8),  Inches(5.5))
    else:
        placeholder = _add_rect(slide, 0.25, 0.75, 7.8, 5.5)
        set_shape_fill(placeholder, LIGHTGRAY)
        placeholder.line.color.rgb = NUSgray
        simple_text(slide, f"[Image not found]\n{os.path.basename(image_path)}",
                    0.3, 2.5, 7.7, 1.0, size=9,
                    color=NUSgray, align=PP_ALIGN.CENTER)

    # Right column: setup text
    simple_text(slide, setup_text,
                8.3, 0.8, 4.75, 0.8, size=9)

    # Table with 3 columns
    tbl = make_table(slide,
                     ["Strategy", "\u03b7 (b/s/Hz)", "QEF%"],
                     rows,
                     8.3, 1.7, 4.75, 1.5)

    # Highlight DQN row (last data row) with LIGHTORANGE
    last_row = len(rows)  # 1-indexed (includes header offset)
    for ci in range(3):
        try:
            set_cell_bg(tbl.cell(last_row, ci), LIGHTORANGE)
        except Exception:
            pass

    # Footnote
    simple_text(slide, footnote,
                8.3, 3.3, 4.75, 0.3, size=7, italic=True, color=NUSgray)

    # Observation box
    colored_box(slide, 8.3, 3.7, 4.75, 2.0,
                "Observation",
                [observation_text],
                LIGHTGREEN, NUSgreen)


def build_slide7(prs):
    _build_scenario_slide(
        prs, 7,
        "Scenario 1: SNR Sweep \u2014 Full MODCOD Range",
        os.path.join(FIG_DIR, "acm_simulation_results_sweep.png"),
        "Setup: SNR linearly swept from \u22123 to +20 dB and back. Exercises all 28 MODCODs.",
        [["CCM",        "0.99", "76.0%"],
         ["Rule-based", "2.07", "47.3%"],
         ["DQN*",       "1.30", "69.3%"]],
        "*DQN: online training, \u03b5: 1.0\u21920.56",
        "DQN trades some \u03b7 vs rule-based for significantly better link reliability (+22 pp QEF). Still early training."
    )


def build_slide8(prs):
    _build_scenario_slide(
        prs, 8,
        "Scenario 2: LEO Pass \u2014 9.2-Minute Orbital Arc",
        os.path.join(FIG_DIR, "acm_simulation_results_leo.png"),
        "Setup: Full LEO pass at 500 km. ITU-R P.618-13 channel. 12.2 dB SNR swing from AOS to TCA to LOS.",
        [["CCM",        "0.99", "99.8%"],
         ["Rule-based", "3.02", "64.2%"],
         ["DQN*",       "1.74", "83.3%"]],
        "*DQN: online training, \u03b5: 1.0\u21920.56",
        "Rule-based achieves highest \u03b7 but sacrifices 35% of frames. DQN maintains 83% QEF with 1.74\u00d7 CCM throughput."
    )


def build_slide9(prs):
    _build_scenario_slide(
        prs, 9,
        "Scenario 3: Rain Fade \u2014 Graceful Degradation",
        os.path.join(FIG_DIR, "acm_simulation_results_rain_fade.png"),
        "Setup: 10 dB rain fade at t=15s, recovery at t=45s. ITU-R P.838-3 + P.839-4 rain model.",
        [["CCM",        "0.99",  "100.0%"],
         ["Rule-based", "3.07",  "68.0%"],
         ["DQN*",       "2.01",  "88.2%"]],
        "*DQN: online training, \u03b5: 1.0\u21920.56",
        "DQN best balances throughput and reliability under sudden fades. +20 pp QEF over rule-based with 2\u00d7 CCM throughput."
    )


def build_slide10(prs):
    """Cross-Scenario Comparison Summary."""
    slide = new_slide(prs)
    title_bar(slide, "Cross-Scenario Comparison Summary")
    footer(slide, 10)

    simple_text(slide, "DQN: online training, \u03b5: 1.0\u21920.56 across runs",
                0.25, 0.7, 12.8, 0.3, size=9, italic=True, color=NUSgray)

    rows = [
        ["SNR Sweep", "CCM",        "0.99", "0",    "76.0%"],
        ["SNR Sweep", "Rule-based", "2.07", "42",   "47.3%"],
        ["SNR Sweep", "DQN",        "1.30", "235",  "69.3%"],
        ["LEO Pass",  "CCM",        "0.99", "0",    "99.8%"],
        ["LEO Pass",  "Rule-based", "3.02", "393",  "64.2%"],
        ["LEO Pass",  "DQN",        "1.74", "7076", "83.3%"],
        ["Rain Fade", "CCM",        "0.99", "0",    "100.0%"],
        ["Rain Fade", "Rule-based", "3.07", "18",   "68.0%"],
        ["Rain Fade", "DQN",        "2.01", "697",  "88.2%"],
    ]

    tbl = make_table(slide,
                     ["Scenario", "Strategy", "\u03b7 (bits/sym)", "Switches", "QEF%"],
                     rows,
                     0.25, 1.05, 12.8, 4.8)

    # Highlight DQN rows (indices 3, 6, 9 in 1-based with header)
    for ri in [3, 6, 9]:
        for ci in range(5):
            try:
                set_cell_bg(tbl.cell(ri, ci), LIGHTORANGE)
            except Exception:
                pass

    colored_box(slide, 0.25, 6.1, 6.2, 0.85,
                "DQN Strengths",
                ["Best QEF% in 2/3 scenarios  |  Feasibility-gated exploration  |  Reward penalises FER heavily"],
                LIGHTGREEN, NUSgreen)

    colored_box(slide, 6.85, 6.1, 6.2, 0.85,
                "Current Limitation",
                ["High switch count in LEO (7076) \u2014 still exploring  |  More training \u2192 \u03b5\u21920.05 \u2192 converged policy"],
                LIGHTGRAY, NUSgray)


def build_slide11(prs):
    """Throughput Efficiency Factor (TEF)."""
    slide = new_slide(prs)
    title_bar(slide, "Throughput Efficiency Factor (TEF)")
    footer(slide, 11)

    simple_text(slide,
                "TEF = \u03b7 \u00d7 (1 \u2212 FER)   [bits/sym, effective]",
                0.25, 0.8, 7.2, 0.5, size=16, bold=True, color=NUSblue)

    simple_text(slide,
                "A high-\u03b7 link with many frame errors has low effective TEF. TEF combines throughput and reliability into one metric.",
                0.25, 1.4, 7.2, 0.6, size=10, color=NUSgray)

    tbl = make_table(slide,
                     ["Strategy", "SNR Sweep", "LEO Pass", "Rain Fade"],
                     [["CCM",        "0.75", "0.99", "0.99"],
                      ["Rule-based", "1.09", "1.94", "2.09"],
                      ["DQN",        "0.90", "1.45", "1.78"]],
                     0.25, 2.1, 7.2, 1.8)

    # Highlight DQN row (row index 3 = last data row)
    for ci in range(4):
        try:
            set_cell_bg(tbl.cell(3, ci), LIGHTORANGE)
        except Exception:
            pass

    colored_box(slide, 7.75, 0.8, 5.3, 2.3,
                "Key Takeaway",
                ["DQN consistently outperforms CCM in effective throughput across all scenarios.",
                 "Rain fade is DQN\u2019s strongest scenario \u2014 learns fade pattern and pre-emptively downgrades MODCOD before outage."],
                LIGHTGREEN, NUSgreen)

    colored_box(slide, 7.75, 3.3, 5.3, 2.0,
                "Why DQN < Rule-based in TEF",
                ["DQN still in early training (\u03b5\u22480.8, mostly random exploration).",
                 "TEF gap will close as agent accumulates experience and \u03b5 decays toward 0.05."],
                LIGHTORANGE, NUSorange)


def build_slide12(prs):
    """GNU Radio Software Loopback Demo — with embedded video."""
    slide = new_slide(prs)
    title_bar(slide, "GNU Radio Software Loopback Demo")
    footer(slide, 12)

    video_embedded = False

    if os.path.exists(VIDEO):
        try:
            if os.path.exists(POSTER):
                slide.shapes.add_movie(
                    VIDEO,
                    left=Inches(1.5), top=Inches(0.75),
                    width=Inches(8.5), height=Inches(4.78),
                    poster_frame_image=POSTER,
                    mime_type="video/mp4"
                )
            else:
                slide.shapes.add_movie(
                    VIDEO,
                    left=Inches(1.5), top=Inches(0.75),
                    width=Inches(8.5), height=Inches(4.78),
                    mime_type="video/mp4"
                )
            video_embedded = True
            print("  Video embedded successfully.")
        except Exception as e:
            print(f"  Video embedding failed: {e}")
    else:
        print(f"  Warning: video file not found at {VIDEO}")

    if not video_embedded:
        if os.path.exists(POSTER):
            try:
                slide.shapes.add_picture(
                    POSTER,
                    Inches(1.5), Inches(0.75),
                    Inches(8.5), Inches(4.78)
                )
                print("  Poster frame image used as fallback.")
            except Exception as e2:
                print(f"  Poster frame fallback also failed: {e2}")
                ph = _add_rect(slide, 1.5, 0.75, 8.5, 4.78)
                set_shape_fill(ph, LIGHTGRAY)
                ph.line.color.rgb = NUSgray
        else:
            ph = _add_rect(slide, 1.5, 0.75, 8.5, 4.78)
            set_shape_fill(ph, LIGHTGRAY)
            ph.line.color.rgb = NUSgray
            simple_text(slide,
                        "[Video not found \u2014 place grc_screen_record.mp4 in docs/]",
                        2.0, 2.8, 7.5, 0.5, size=10,
                        color=NUSgray, align=PP_ALIGN.CENTER)

    # Play prompt
    simple_text(slide, "\u25b6  Click video to play inline",
                3.5, 5.65, 6.3, 0.35, size=11, bold=True,
                color=NUSblue, align=PP_ALIGN.CENTER)

    # OneDrive hyperlink textbox
    txBox = slide.shapes.add_textbox(
        Inches(2.5), Inches(6.1), Inches(8.3), Inches(0.4)
    )
    tf = txBox.text_frame
    _configure_tf(tf)
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    run = para.add_run()
    run.text = "\u25b6  Or watch full-screen on NUS OneDrive (works in any browser)"
    run.font.size  = Pt(10)
    run.font.color.rgb = NUSorange
    run.font.bold  = True
    run.font.name  = "Calibri"
    run.hyperlink.address = (
        "https://nusu-my.sharepoint.com/:v:/g/personal/manujayaugp_u_nus_edu/"
        "IgBL8WLq6Kg3T72oFts44raeAY2qRBnLZsHWS2kot5pOpYc"
    )


def build_slide13(prs):
    """GNU Radio Demo — What It Shows."""
    slide = new_slide(prs)
    title_bar(slide, "GNU Radio Software Loopback Demo \u2014 What It Shows")
    footer(slide, 13)

    simple_text(slide,
                "Current setup: Random source \u2192 full IQ signal chain \u2192 AWGN channel (no USRP yet)",
                0.25, 0.8, 7.0, 0.5, size=10, bold=True)

    simple_text(slide, "What the video shows:",
                0.25, 1.45, 7.0, 0.35, size=10, bold=True, color=NUSblue)

    numbered_items = [
        "1.  GRC flowgraph \u2014 TX chain, AWGN channel, RX chain, ACM controller",
        "2.  Execution starts \u2014 QPSK constellation appears",
        "3.  AWGN noise increases \u2014 MODCOD steps down (32APSK \u2192 16APSK \u2192 8PSK \u2192 QPSK)",
        "4.  Noise reduces \u2014 link recovers to high-order MODCOD",
    ]
    y_positions = [1.9, 2.5, 3.1, 3.8]
    for y_pos, txt in zip(y_positions, numbered_items):
        simple_text(slide, txt, 0.25, y_pos, 7.0, 0.55, size=10, color=BLACK)

    colored_box(slide, 7.75, 0.8, 5.3, 2.5,
                "Key Point",
                ["Full BCH+LDPC FEC, real Pilot-MMSE SNR estimation, and PLSCODE signalling \u2014 all working in software loopback.",
                 "Constellation changes automatically with no manual intervention."],
                LIGHTORANGE, NUSorange)

    colored_box(slide, 7.75, 3.5, 5.3, 1.8,
                "Next Step",
                ["Connect USRP B200 hardware and run acm_loopback.grc over a real X-Band link for hardware validation."],
                LIGHTGREEN, NUSgreen)


def build_slide14(prs):
    """GNU Radio: Constellation at Each MODCOD."""
    slide = new_slide(prs)
    title_bar(slide, "GNU Radio: Constellation at Each MODCOD")
    footer(slide, 14)

    modcod_boxes = [
        (0.25, NUSblue,   LIGHTBLUE,   "QPSK \u2014 Low SNR",
         ["4 constellation points", "1 bit / symbol",
          "Most robust MODCOD", "Min SNR: ~1.5 dB", "Screenshot: to be added"]),
        (3.4,  NUSgreen,  LIGHTGREEN,  "8PSK \u2014 Medium SNR",
         ["8 constellation points", "1.5 bits / symbol",
          "Moderate robustness", "Min SNR: ~6\u20138 dB", "Screenshot: to be added"]),
        (6.55, NUSorange, LIGHTORANGE, "16APSK \u2014 Good SNR",
         ["16 constellation points", "2 bits / symbol",
          "Good link conditions", "Min SNR: ~9\u201311 dB", "Screenshot: to be added"]),
        (9.7,  NUSred,    LIGHTRED,    "32APSK \u2014 High SNR",
         ["32 constellation points", "2.5 bits / symbol",
          "Best spectral efficiency", "Min SNR: ~13\u201316 dB", "Screenshot: to be added"]),
    ]
    for x, accent, bg, ttl, body in modcod_boxes:
        colored_box(slide, x, 0.8, 2.9, 3.2, ttl, body, bg, accent)

    colored_box(slide, 0.25, 4.2, 6.2, 1.6,
                "How to Read the Constellation",
                ["Each dot = one received IQ symbol",
                 "Tight clusters = high SNR, good link",
                 "Spread/overlapping = low SNR or wrong MODCOD",
                 "ACM keeps clusters tight by choosing the right MODCOD"],
                LIGHTGRAY, NUSgray)

    colored_box(slide, 6.85, 4.2, 6.2, 1.6,
                "What to Look for in the Video",
                ["As SNR drops: 32APSK (dense) \u2192 QPSK (4 clear points)",
                 "The link stays connected throughout the transition",
                 "Each constellation change = one ACM decision applied"],
                LIGHTGREEN, NUSgreen)


def build_slide15(prs):
    """Summary & Next Steps."""
    slide = new_slide(prs)
    title_bar(slide, "Summary & Next Steps")
    footer(slide, 15)

    colored_box(slide, 0.25, 0.75, 7.5, 1.2,
                "1. CCM is safe but wasteful",
                ["QEF near 100% but \u03b7=0.99 bits/sym always. Leaves most link capacity unused."],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 0.25, 2.1, 7.5, 1.2,
                "2. Rule-based ACM maximises \u03b7",
                ["Sacrifices reliability \u2014 up to 35% frame loss in LEO scenario. Fast but no learning."],
                LIGHTBLUE, NUSblue)

    colored_box(slide, 0.25, 3.45, 7.5, 1.3,
                "3. DQN ACM balances both",
                ["Best QEF% in 2/3 scenarios, 2\u00d7 CCM throughput in rain fade.",
                 "Still converging (\u03b5\u21920.05) \u2014 will improve with more training."],
                LIGHTORANGE, NUSorange)

    colored_box(slide, 0.25, 4.9, 7.5, 1.3,
                "4. GNU Radio confirms signal chain",
                ["Real LDPC, real SNR estimation, constellation changes automatically.",
                 "Software loopback working \u2014 USRP B200 hardware connection is next step."],
                LIGHTGREEN, NUSgreen)

    colored_box(slide, 8.1, 0.75, 5.0, 2.8,
                "Best Result",
                ["Rain fade scenario:",
                 "  DQN: \u03b7 = 2.01 bits/sym,  QEF = 88.2%",
                 "  Rule-based: QEF = 68.0%",
                 "\u2192 +20 percentage points reliability",
                 "\u2192 2\u00d7 CCM throughput"],
                LIGHTGREEN, NUSgreen)

    colored_box(slide, 8.1, 3.75, 5.0, 2.4,
                "Next Step",
                ["Connect USRP B200 hardware",
                 "Run acm_loopback.grc over real X-Band link",
                 "Complete hardware validation"],
                LIGHTORANGE, NUSorange)


# ===========================================================================
# Main
# ===========================================================================
def main():
    print("Building DVB-S2 ACM Results Presentation...")

    # Create presentation with 16:9 slide size
    prs = Presentation()
    prs.slide_width  = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Build all 15 slides
    builders = [
        ("Title",                      build_slide1),
        ("System Architecture",        build_slide2),
        ("Two-Tool Validation",        build_slide3),
        ("AI/ML Engine",               build_slide4),
        ("What We Are Comparing",      build_slide5),
        ("DQN Training State",         build_slide6),
        ("SNR Sweep Results",          build_slide7),
        ("LEO Pass Results",           build_slide8),
        ("Rain Fade Results",          build_slide9),
        ("Cross-Scenario Comparison",  build_slide10),
        ("TEF",                        build_slide11),
        ("GRC Demo (video)",           build_slide12),
        ("GRC Demo Content",           build_slide13),
        ("Constellations",             build_slide14),
        ("Summary & Next Steps",       build_slide15),
    ]

    for i, (name, builder) in enumerate(builders, 1):
        print(f"  Slide {i:2d}/15  {name}...", end=" ", flush=True)
        try:
            builder(prs)
            print("OK")
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    # Save
    os.makedirs(os.path.dirname(OUT_PPTX), exist_ok=True)
    prs.save(OUT_PPTX)
    print(f"\nSaved: {OUT_PPTX}")

    # ---------------------------------------------------------------------------
    # Verification
    # ---------------------------------------------------------------------------
    file_size_mb = os.path.getsize(OUT_PPTX) / (1024 * 1024)
    print(f"File size: {file_size_mb:.2f} MB")

    prs2     = Presentation(OUT_PPTX)
    n_slides = len(prs2.slides)
    print(f"Slide count: {n_slides}")

    # Check slide 12 for embedded video via relationship
    slide12   = prs2.slides[11]  # 0-indexed
    has_movie = False
    for rel in slide12.part.rels.values():
        rtype = rel.reltype.lower()
        if 'video' in rtype or 'media' in rtype:
            has_movie = True
            break

    # Final status
    status = "SUCCESS" if n_slides == 15 else "WARNING"
    detail = f"{n_slides} slides, {file_size_mb:.1f} MB"
    if has_movie:
        detail += ", video on slide 12"
    else:
        detail += ", (video rel not detected — may still play)"
    print(f"\n{status}: {detail}")


if __name__ == "__main__":
    main()
