"""直线标定：能不能从几条平行线把已知参数解回来。"""

import pytest

from server.hardware import calibrate

WIDTH, HEIGHT = 1280, 720
XS = (-450.0, -300.0, -150.0, 0.0, 150.0, 300.0, 450.0)


def make_lines(true_fov, true_k1, seen_fov, seen_k1, offsets=(-220.0, 0.0, 220.0)):
    """造一组平行线：理想图上的直线 → 原图 → 用「当前参数」看到的画面。"""
    lines = []
    for offset in offsets:
        view = []
        for u, v in ((x, offset) for x in XS):
            x, y = calibrate.to_source(u, v, WIDTH, HEIGHT, true_fov, true_k1)
            view.append(calibrate.to_screen(x, y, WIDTH, HEIGHT, seen_fov, seen_k1))
        lines.append(view)
    return lines


def test_point_mapping_round_trip():
    for u, v in ((0.0, 0.0), (300.0, 150.0), (-420.0, -260.0)):
        x, y = calibrate.to_source(u, v, WIDTH, HEIGHT, 160.0, -0.05)
        back_u, back_v = calibrate.to_screen(x, y, WIDTH, HEIGHT, 160.0, -0.05)
        assert back_u == pytest.approx(u, abs=0.5)
        assert back_v == pytest.approx(v, abs=0.5)


def test_solve_recovers_known_parameters():
    lines = make_lines(160.0, -0.05, seen_fov=180.0, seen_k1=0.0)
    result = calibrate.solve(lines, WIDTH, HEIGHT, 180.0, 0.0)
    assert result["fov"] == pytest.approx(160.0, abs=2.5)
    assert result["k1"] == pytest.approx(-0.05, abs=0.06)
    assert result["residual"] < 2.0


def test_solve_survives_a_wrong_starting_point():
    lines = make_lines(158.0, 0.0, seen_fov=120.0, seen_k1=0.2)
    result = calibrate.solve(lines, WIDTH, HEIGHT, 120.0, 0.2)
    assert result["fov"] == pytest.approx(158.0, abs=3.0)


def test_single_line_only_solves_fov():
    # 线必须偏离画面中心：穿过中心的直线在径向畸变下始终是直的，定不了参数
    lines = make_lines(150.0, 0.0, seen_fov=180.0, seen_k1=0.0, offsets=(220.0,))
    result = calibrate.solve(lines, WIDTH, HEIGHT, 180.0, 0.0)
    assert result["fov"] == pytest.approx(150.0, abs=4.0)
    assert result["k1"] == 0.0


def test_too_few_points_rejected():
    with pytest.raises(ValueError):
        calibrate.solve([[(0.0, 0.0), (1.0, 0.0)]], WIDTH, HEIGHT, 160.0)
