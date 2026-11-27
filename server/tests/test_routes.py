"""接口层：前端交回的图片地址带 /img 前缀，落到磁盘前必须还原成相对路径。"""

from server.api import routes


def test_image_url_maps_back_to_disk_relative_path():
    assert routes._rel("/img/crops/layer1-1.jpg") == "crops/layer1-1.jpg"


def test_bare_relative_path_passes_through():
    assert routes._rel("crops/layer1-1.jpg") == "crops/layer1-1.jpg"


def test_missing_url_stays_empty():
    assert routes._rel(None) == ""
    assert routes._rel("") == ""
