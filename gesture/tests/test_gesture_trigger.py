import math

import numpy as np
import pytest

from gesture_trigger import HoldTimer, is_l_sign

# Synthetic right hand in metres, palm facing the camera, fingers pointing +y.
MCP = {"index": (0.025, 0.085, 0.0), "middle": (0.0, 0.09, 0.0),
       "ring": (-0.02, 0.085, 0.0), "pinky": (-0.04, 0.075, 0.0)}

def _extended(mcp):
    m = np.array(mcp)
    return [m, m + (0, 0.04, 0), m + (0, 0.065, 0), m + (0, 0.085, 0)]

def _curled(mcp):
    m = np.array(mcp)
    pip = m + (0, 0.03, 0)
    dip = pip + (0, 0.0, -0.02)
    return [m, pip, dip, dip + (0, -0.02, -0.005)]

THUMB_OUT = [(0.02, 0.02, 0.0), (0.045, 0.035, 0.0), (0.075, 0.04, 0.0), (0.10, 0.042, 0.0)]
THUMB_TUCKED = [(0.02, 0.02, 0.0), (0.035, 0.04, 0.0), (0.02, 0.06, -0.02), (0.0, 0.07, -0.03)]
THUMB_ALONG_INDEX = [(0.02, 0.02, 0.0), (0.045, 0.035, 0.0), (0.05, 0.065, 0.0), (0.052, 0.09, 0.0)]

def hand(thumb, index="out", middle="in", ring="in", pinky="in"):
    pts = [(0.0, 0.0, 0.0)] + list(thumb)
    for name, state in (("index", index), ("middle", middle), ("ring", ring), ("pinky", pinky)):
        pts += (_extended if state == "out" else _curled)(MCP[name])
    return np.array(pts, dtype=float)

def mirrored(p):                    # a left hand
    return p * (-1, 1, 1)

def rotated(p, degrees):            # whole hand rotated in the image plane
    a = math.radians(degrees)
    r = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return p @ r.T

L_HAND = hand(THUMB_OUT)

@pytest.mark.parametrize("points", [
    L_HAND,
    mirrored(L_HAND),
    rotated(L_HAND, 35),
    rotated(mirrored(L_HAND), -60),
    rotated(L_HAND, 180),
])
def test_l_sign_detected(points):
    assert is_l_sign(points)

@pytest.mark.parametrize("points", [
    hand(THUMB_OUT, middle="out", ring="out", pinky="out"),         # open palm
    hand(THUMB_TUCKED, index="in"),                                 # fist
    hand(THUMB_TUCKED),                                             # pointing, thumb tucked
    hand(THUMB_ALONG_INDEX),                                        # thumb up beside index
    hand(THUMB_OUT, middle="out"),                                  # thumb + two fingers
    hand(THUMB_OUT, index="in"),                                    # thumbs-up-ish
])
def test_other_gestures_rejected(points):
    assert not is_l_sign(points)

FRAME = 1 / 30

def feed(timer, seen, start, seconds):
    """Feed `seconds` worth of 30 fps frames; returns (last progress, end time)."""
    progress, t = 0.0, start
    for i in range(round(seconds / FRAME)):
        t = start + i * FRAME
        progress = timer.update(seen, t)
    return progress, t

def test_hold_completes_after_hold_seconds():
    timer = HoldTimer(3.0)
    progress, _ = feed(timer, True, 10.0, 1.5)
    assert progress == pytest.approx(0.5, abs=0.02)
    progress, _ = feed(timer, True, 11.5, 1.6)
    assert progress == 1.0

def test_hold_survives_short_dropout():
    timer = HoldTimer(3.0, grace_seconds=0.35)
    _, t = feed(timer, True, 10.0, 1.0)
    _, t = feed(timer, False, t + FRAME, 0.2)                       # a few missed frames
    progress, _ = feed(timer, True, t + FRAME, 0.1)
    assert progress == pytest.approx(1.3 / 3.0, abs=0.03)

def test_hold_resets_after_long_dropout():
    timer = HoldTimer(3.0, grace_seconds=0.35)
    _, t = feed(timer, True, 10.0, 2.0)
    progress, t = feed(timer, False, t + FRAME, 0.5)
    assert progress == 0.0
    assert timer.update(True, t + FRAME) == 0.0                     # starts over
