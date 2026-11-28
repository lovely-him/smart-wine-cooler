"""图片地址换算：界面交回的是 /img 前缀的地址，落到磁盘前要还原成相对路径。"""

from server.platform.views import rel


def test_image_url_maps_back_to_disk_relative_path():
    assert rel("/img/crops/layer1-1.jpg") == "crops/layer1-1.jpg"


def test_bare_relative_path_passes_through():
    assert rel("crops/layer1-1.jpg") == "crops/layer1-1.jpg"


def test_missing_url_stays_empty():
    assert rel(None) == ""
    assert rel("") == ""
