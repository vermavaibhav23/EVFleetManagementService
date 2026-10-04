"""Small linear-expression builder; HiGHS owns the mixed-integer search."""

import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


class Expr(dict):
    def __add__(self, other):
        result = Expr(self)
        if not isinstance(other, dict):
            other = {None: other}
        for key, value in other.items():
            result[key] = result.get(key, 0) + value
        return result

    __radd__ = __add__

    def __mul__(self, value):
        return Expr({k: v * value for k, v in self.items()})

    __rmul__ = __mul__

    def __neg__(self):
        return self * -1

    def __sub__(self, other):
        return self + (-other)

    def __rsub__(self, other):
        return -self + other


def value(expression, solution):
    if isinstance(expression, (int, float)):
        return float(expression)
    return sum(v * (1 if k is None else solution[k]) for k, v in expression.items())


class Model:
    def __init__(self):
        self.lower, self.upper, self.integer = [], [], []
        self.rows, self.lo, self.hi = [], [], []

    def var(self, low=0, high=np.inf, integer=False):
        idx = len(self.lower)
        self.lower.append(low)
        self.upper.append(high)
        self.integer.append(int(integer))
        return Expr({idx: 1})

    def binary(self):
        return self.var(0, 1, True)

    def constraint(self, expr, low=-np.inf, high=np.inf):
        if not isinstance(expr, dict):
            expr = Expr({None: expr})
        constant = expr.get(None, 0)
        self.rows.append({k: v for k, v in expr.items() if k is not None and v})
        self.lo.append(low - constant)
        self.hi.append(high - constant)

    def eq(self, a, b=0):
        self.constraint(a - b, 0, 0)

    def le(self, a, b=0):
        self.constraint(a - b, high=0)

    def ge(self, a, b=0):
        self.constraint(a - b, low=0)

    def when_eq(self, y, a, b, big):
        self.le(a - b, big * (1 - y))
        self.ge(a - b, -big * (1 - y))

    def clip(self, x, low, width, domain_low, domain_high):
        """Exact clamp(x-low,0,width), with a three-segment disjunction."""
        if domain_high <= low:
            return Expr()
        if domain_low >= low + width:
            return Expr({None: width})
        out = self.var(0, width)
        segments = [
            (domain_low, min(low, domain_high), Expr()),
            (max(domain_low, low), min(domain_high, low + width), x - low),
            (max(domain_low, low + width), domain_high, Expr({None: width})),
        ]
        flags = []
        big = 2 * (abs(domain_low) + abs(domain_high) + abs(low) + width + 1)
        for start, end, formula in segments:
            if end < start:
                continue
            flag = self.binary()
            flags.append(flag)
            self.ge(x, start - big * (1 - flag))
            self.le(x, end + big * (1 - flag))
            self.when_eq(flag, out, formula, big)
        self.eq(sum(flags), 1)
        return out

    def pwl(self, x, points):
        if len(points) == 2:
            (a, fa), (b, fb) = points
            return fa + (x - a) * ((fb - fa) / (b - a))
        out = self.var(min(y for _, y in points), max(y for _, y in points))
        flags = []
        span = points[-1][0] - points[0][0]
        max_slope = max(
            abs((fb - fa) / (b - a)) for (a, fa), (b, fb) in zip(points, points[1:])
        )
        big = 2 * (max(abs(y) for _, y in points) + span * max_slope + 1)
        for (a, fa), (b, fb) in zip(points, points[1:]):
            flag = self.binary()
            flags.append(flag)
            self.ge(x, a - span * (1 - flag))
            self.le(x, b + span * (1 - flag))
            self.when_eq(flag, out, fa + (x - a) * ((fb - fa) / (b - a)), big)
        self.eq(sum(flags), 1)
        return out

    def solve(self, objective, seconds):
        rr, cc, vv = [], [], []
        for i, row in enumerate(self.rows):
            for j, coefficient in row.items():
                rr.append(i)
                cc.append(j)
                vv.append(coefficient)
        matrix = coo_matrix(
            (vv, (rr, cc)), shape=(len(self.rows), len(self.lower))
        ).tocsc()
        c = np.zeros(len(self.lower))
        for idx, coefficient in objective.items():
            if idx is not None:
                c[idx] = coefficient
        # HiGHS runs one CPU thread per isolated planning process.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Unrecognized options detected.*threads.*",
                category=RuntimeWarning,
            )
            return milp(
                c,
                integrality=self.integer,
                bounds=Bounds(self.lower, self.upper),
                constraints=LinearConstraint(matrix, self.lo, self.hi),
                options={
                    "time_limit": max(0.05, seconds),
                    "mip_rel_gap": 1e-7,
                    "threads": 1,
                },
            )
