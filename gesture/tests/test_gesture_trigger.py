import math

import numpy as np
import pytest

from gesture_trigger import HoldTimer, is_thumbs_up

# Synthetic right hand in metres, palm facing the camera, fingers pointing +y. Axes follow
# the camera image (y points down), as MediaPipe's landmarks do.
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

# THUMB_OUT points +x; turning the hand -90 degrees makes the thumb point up (-y) with the
# curled fingers lying sideways: a thumbs-up.
def upright(p):
    return rotated(p, -90)

THUMBS_UP = upright(hand(THUMB_OUT, index="in"))
LEFT_THUMBS_UP = mirrored(THUMBS_UP)

@pytest.mark.parametrize("points", [
    THUMBS_UP,
    LEFT_THUMBS_UP,
    rotated(THUMBS_UP, 25),                                         # tilted either way
    rotated(THUMBS_UP, -25),
    rotated(LEFT_THUMBS_UP, 30),
])
def test_thumbs_up_detected(points):
    assert is_thumbs_up(points)

@pytest.mark.parametrize("points", [
    rotated(THUMBS_UP, 180),                                        # thumbs-down
    hand(THUMB_OUT, index="in"),                                    # thumb pointing sideways
    rotated(THUMBS_UP, 60),                                         # tilted too far
    upright(hand(THUMB_OUT)),                                       # index out too (L sign)
    upright(hand(THUMB_OUT, index="out", middle="out", ring="out", pinky="out")),  # open palm
    upright(hand(THUMB_TUCKED, index="in")),                        # fist, thumb tucked
    upright(hand(THUMB_ALONG_INDEX, index="in")),                   # thumb along the fist
])
def test_other_gestures_rejected(points):
    assert not is_thumbs_up(points)

def test_up_comes_from_the_image_points():
    # Same hand shape, but the camera image shows it upside down -> not a thumbs-up.
    flipped_image = rotated(THUMBS_UP, 180)[:, :2]
    assert is_thumbs_up(THUMBS_UP, THUMBS_UP[:, :2])
    assert not is_thumbs_up(THUMBS_UP, flipped_image)

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
