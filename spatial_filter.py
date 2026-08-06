"""
spatial_filter.py — smoothing for hand-tracked input.

A webcam hand estimate is noisy at rest and laggy in motion, and those two
faults pull in opposite directions: smooth enough to hold a cursor still on a
small target and it drags behind a fast reach; smooth little enough to keep up
and it shimmers while you aim.

The 1 Euro filter (Casiez, Roussel & Vogel, CHI 2012) resolves that by adapting
its cutoff to speed — heavy smoothing when slow, light when fast. Two
parameters, both with a physical meaning:

    min_cutoff  the floor. Lower = steadier at rest, more lag overall.
    beta        how fast smoothing relaxes as you speed up. Raise it if the
                cursor lags a quick movement; lower it if fast moves overshoot.

Why not the alternatives:
  - A Kalman filter needs a motion model plus process and measurement noise
    covariances. A hand is not a linear system with stationary noise, so those
    are fitted by hand anyway, and the result is what this matches in an
    afternoon.
  - Double exponential smoothing extrapolates through direction reversals, so
    the cursor overshoots and slides past whatever you were aiming at — the
    exact failure this is meant to fix.
  - A fixed lerp, which is what the workspace used, is a speed-adaptive filter
    with the intent but none of the theory: it shimmers at rest AND lags in
    motion, because one coefficient cannot do both jobs.
"""

from __future__ import annotations

import math


class LowPass:
    """First-order exponential low pass with an externally supplied alpha."""

    __slots__ = ("y",)

    def __init__(self):
        self.y = None

    def __call__(self, x: float, alpha: float) -> float:
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y

    def reset(self):
        self.y = None


class OneEuro:
    """One scalar signal. Use one instance per axis — they must not share
    state, or x jitter would widen the cutoff applied to y."""

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "_x", "_dx", "_prev")

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = LowPass()
        self._dx = LowPass()
        self._prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, dt: float) -> float:
        # dt comes from a real frame clock, which stalls; a zero or negative
        # step would divide by zero and a huge one would snap the filter open
        dt = min(max(dt, 1e-3), 0.1)
        dx = 0.0 if self._prev is None else (x - self._prev) / dt
        self._prev = x
        dx_hat = self._dx(dx, self._alpha(self.d_cutoff, dt))
        # the whole idea: cutoff rises with speed, so smoothing gets out of the
        # way exactly when lag would be visible
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        return self._x(x, self._alpha(cutoff, dt))

    @property
    def speed(self) -> float:
        """Smoothed rate of change, in units per second. Reused for velocity
        lookahead and for hit-box widening, so nothing has to differentiate the
        already-filtered output and reintroduce noise."""
        return abs(self._dx.y or 0.0)

    @property
    def velocity(self) -> float:
        """Signed smoothed rate of change."""
        return self._dx.y or 0.0

    def reset(self):
        self._x.reset()
        self._dx.reset()
        self._prev = None


class OneEuro2D:
    """Two axes, filtered independently, with a shared speed readout."""

    __slots__ = ("fx", "fy")

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007):
        self.fx = OneEuro(min_cutoff, beta)
        self.fy = OneEuro(min_cutoff, beta)

    def __call__(self, x: float, y: float, dt: float):
        return self.fx(x, dt), self.fy(y, dt)

    @property
    def velocity(self):
        return (self.fx.velocity, self.fy.velocity)

    @property
    def speed(self) -> float:
        vx, vy = self.velocity
        return math.hypot(vx, vy)

    def reset(self):
        self.fx.reset()
        self.fy.reset()


# Tuned per signal. A cursor wants stability because aiming dominates; the
# rendered hand wants responsiveness because a visibly late hand reads worse
# than a slightly noisy one; depth is slow and very noisy, so it is smoothed
# hard and never needs to be quick.
PRESETS = {
    "cursor": (1.0, 0.007),
    "hand": (1.6, 0.010),
    "pinch": (2.5, 0.004),
    "depth": (0.6, 0.002),
}


def make(kind: str) -> OneEuro2D:
    return OneEuro2D(*PRESETS.get(kind, PRESETS["cursor"]))


def make1(kind: str) -> OneEuro:
    return OneEuro(*PRESETS.get(kind, PRESETS["cursor"]))
