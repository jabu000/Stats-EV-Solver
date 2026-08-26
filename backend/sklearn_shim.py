"""Centered isotonic regression, so calibration does not pull in scikit-learn.

Pool-adjacent-violators is the whole of isotonic regression and is about thirty lines;
adding a large dependency for it would be the wrong trade in a project whose other
numerical needs are already covered by numpy and scipy.

The refinement that matters here is the **centered** part. Plain PAV on binary outcomes
produces a step function with long flat runs -- with a few hundred graded picks, a whole
band of probabilities from 0.71 to 0.74 can collapse onto a single value. That is fatal
for this application, because the board is *ranked* by the calibrated number: flattening
it throws away the model's ordering, which is the part most likely to be right.

Centered isotonic regression (Oron & Flournoy) fixes this by treating each pooled block
as a single point located at the block's centroid, then interpolating linearly between
those points. The result is still monotone, still fits the data, and is strictly
increasing between knots -- so ties inside a block are broken by the model's own
ordering instead of being erased.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


#: Pseudo-count controlling how hard a pooled block is shrunk toward the model's own
#: prediction. A block of five picks that happened to go 4-1 must not be allowed to
#: restate a 0.80 projection as 0.91; a block of two hundred should be trusted.
BLOCK_PRIOR_WEIGHT = 25.0


@dataclass
class IsotonicFit:
    """A fitted monotone function, evaluated by interpolation between block centroids."""

    #: Knot locations: the mean predicted probability of each pooled block.
    knots_x: np.ndarray
    #: Knot values: the observed frequency in each pooled block, shrunk toward the
    #: prediction by how many observations the block actually contains.
    knots_y: np.ndarray
    #: The fitted domain. Outside it we have no evidence and fall back to the identity.
    lo: float
    hi: float

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray) -> "IsotonicFit":
        order = np.argsort(x)
        xs = np.asarray(x, dtype=float)[order]
        ys = np.asarray(y, dtype=float)[order]
        if xs.size == 0:
            return cls(np.array([]), np.array([]), 0.0, 1.0)

        # Pool adjacent violators. Each block tracks its summed x and weight so the
        # centroid is available afterwards.
        values: list[float] = list(ys)
        weights: list[float] = [1.0] * len(ys)
        x_sums: list[float] = list(xs)

        index = 0
        while index < len(values) - 1:
            if values[index] <= values[index + 1] + 1e-12:
                index += 1
                continue
            total = weights[index] + weights[index + 1]
            pooled = (
                values[index] * weights[index] + values[index + 1] * weights[index + 1]
            ) / total
            values[index : index + 2] = [pooled]
            weights[index : index + 2] = [total]
            x_sums[index : index + 2] = [x_sums[index] + x_sums[index + 1]]
            if index > 0:
                index -= 1

        knots_x = np.array(
            [total_x / weight for total_x, weight in zip(x_sums, weights)], dtype=float
        )
        knots_y = np.array(values, dtype=float)
        block_weights = np.array(weights, dtype=float)

        # Shrink each block's observed frequency toward the model's own prediction in
        # proportion to how much evidence the block carries. Without this, a handful of
        # picks in a sparsely-populated band -- almost always the extremes, where the
        # interesting bets live -- can swing the correction wildly and even reverse it.
        trust = block_weights / (block_weights + BLOCK_PRIOR_WEIGHT)
        knots_y = trust * knots_y + (1.0 - trust) * knots_x

        # Shrinking toward x can break monotonicity at the joins; restore it cheaply.
        knots_y = np.maximum.accumulate(knots_y)

        # np.interp needs strictly increasing x; collapse any duplicate centroids.
        unique_x, inverse = np.unique(knots_x, return_inverse=True)
        if unique_x.size != knots_x.size:
            merged = np.zeros(unique_x.size)
            counts = np.zeros(unique_x.size)
            np.add.at(merged, inverse, knots_y)
            np.add.at(counts, inverse, 1.0)
            knots_x, knots_y = unique_x, merged / np.maximum(counts, 1.0)

        return cls(
            knots_x=knots_x, knots_y=knots_y, lo=float(xs[0]), hi=float(xs[-1])
        )

    def predict(self, probability: float) -> float:
        """Corrected probability, or the input unchanged where we have no evidence.

        Outside the fitted domain, `np.interp` would clamp to an endpoint -- mapping
        every unseen probability onto one value and erasing the difference between a
        30% bet and a 45% one. We have learned nothing out there, so the honest answer
        is the identity.
        """
        if self.knots_x.size == 0:
            return probability
        if probability < self.lo or probability > self.hi:
            return float(min(max(probability, 1e-6), 1 - 1e-6))
        value = float(np.interp(probability, self.knots_x, self.knots_y))
        return float(min(max(value, 1e-6), 1 - 1e-6))
