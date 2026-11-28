"""直线标定：从用户点的几条平行线反解镜头参数。

真实世界里共线、且互相平行的东西，去畸变之后必须还是共线、且平行。
这两条约束都免疫「摄像头没正对柜内」带来的透视——平行的直线在透视下仍然平行，
只有畸变才会让它们各自弯曲。用矩形反而解不出来：单张图的四边形里，
畸变和透视混在一起分不开。

点是在「矫正后画面」上点的，所以先用当前参数反算回原图坐标，
再拿候选参数去优化。参数只有一到两个，网格搜索足够，不需要额外的优化库。
"""

import math

CANVAS_SCALE = 4.0
PARALLEL_WEIGHT = 1000.0
MAX_SAMPLES = 12


def focal(width, height, fov):
    return (math.hypot(width, height) / 2.0) / (math.radians(fov) / 2.0)


def solve_theta(theta_d, k1):
    """由等距投影的角解出真实入射角；k1=0 时两者相等。"""
    if not k1:
        return theta_d
    theta = theta_d
    for _ in range(8):
        t2 = theta * theta
        value = theta * (1.0 + k1 * t2) - theta_d
        slope = 1.0 + 3.0 * k1 * t2
        if abs(slope) < 1e-9:
            break
        theta -= value / slope
    return theta


def to_source(u, v, width, height, fov, k1):
    """矫正画面坐标（相对画面中心）→ 原图坐标。"""
    f = focal(width, height, fov)
    cx, cy = width / 2.0, height / 2.0
    r = math.hypot(u, v) / f
    if r < 1e-9:
        return cx, cy
    theta = math.atan(r)
    theta_d = theta * (1.0 + k1 * theta * theta)
    scale = theta_d / r
    return cx + u * scale, cy + v * scale


def to_screen(x, y, width, height, fov, k1):
    """原图坐标 → 矫正画面坐标（相对画面中心）。"""
    f = focal(width, height, fov)
    cx, cy = width / 2.0, height / 2.0
    dx, dy = x - cx, y - cy
    theta_d = math.hypot(dx, dy) / f
    if theta_d < 1e-9:
        return 0.0, 0.0
    theta = solve_theta(theta_d, k1)
    r = math.tan(theta)
    if not math.isfinite(r) or r <= 0:
        return 0.0, 0.0
    scale = r / theta_d
    return dx * scale, dy * scale


def fit_line(points):
    """最小二乘主方向，返回 (方向角, 平均残差)。"""
    count = len(points)
    mx = sum(p[0] for p in points) / count
    my = sum(p[1] for p in points) / count
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    nx, ny = -math.sin(angle), math.cos(angle)
    err = sum(((p[0] - mx) * nx + (p[1] - my) * ny) ** 2 for p in points)
    return angle, err / count


def align_angle(angle):
    """把方向角折算到 [-π/2, π/2)，免得同一条线因为首尾顺序不同被判成反向。"""
    while angle >= math.pi / 2:
        angle -= math.pi
    while angle < -math.pi / 2:
        angle += math.pi
    return angle


def _score(sources, width, height, fov, k1):
    angles = []
    total = 0.0
    for points in sources:
        mapped = [to_screen(x, y, width, height, fov, k1) for x, y in points]
        angle, err = fit_line(mapped)
        angles.append(align_angle(angle))
        total += err
    if len(angles) > 1:
        mean = sum(angles) / len(angles)
        total += PARALLEL_WEIGHT * sum((a - mean) ** 2 for a in angles)
    return total


def _range(start, stop, step):
    count = int(round((stop - start) / step))
    return [start + index * step for index in range(count + 1)]


def _downsample(points):
    if len(points) <= MAX_SAMPLES:
        return points
    stride = len(points) / MAX_SAMPLES
    return [points[int(index * stride)] for index in range(MAX_SAMPLES)]


def solve(lines, width, height, current_fov, current_k1=0.0):
    """lines 是矫正画面上的点（像素，原点在画面中心）。

    返回 (fov, k1, residual, per_line)。
    """
    usable = [_downsample(line) for line in lines if len(line) >= 4]
    if not usable:
        raise ValueError("至少要有一条线，且每条线至少 4 个点")

    base_fov = float(current_fov) if current_fov and current_fov > 0 else 180.0
    sources = []
    for line in usable:
        converted = [
            to_source(u, v, width, height, base_fov, float(current_k1) or 0.0) for u, v in line
        ]
        sources.append(converted)

    single = len(sources) < 2
    best = None
    for fov in _range(100.0, 180.0, 1.0):
        for k1 in [0.0] if single else _range(-0.5, 0.5, 0.05):
            score = _score(sources, width, height, fov, k1)
            if best is None or score < best[0]:
                best = (score, fov, k1)

    _, coarse_fov, coarse_k1 = best
    best = None
    for fov in _range(coarse_fov - 2.0, coarse_fov + 2.0, 0.1):
        for k1 in [0.0] if single else _range(coarse_k1 - 0.1, coarse_k1 + 0.1, 0.005):
            score = _score(sources, width, height, fov, k1)
            if best is None or score < best[0]:
                best = (score, fov, k1)

    _, final_fov, final_k1 = best
    per_line = []
    for points in sources:
        mapped = [to_screen(x, y, width, height, final_fov, final_k1) for x, y in points]
        per_line.append(round(math.sqrt(fit_line(mapped)[1]), 3))

    residual = sum(per_line) / len(per_line)
    return {
        "fov": round(final_fov, 1),
        "k1": round(final_k1, 3),
        "residual": round(residual, 2),
        "perLine": per_line,
    }
