"""所有测试共用的设置。"""

import pytest

from ournotes_auto.charts import bdon

REAL_JACKETS, REAL_BUNDLE = bdon.JACKETS, bdon.BUNDLE


@pytest.fixture(autouse=True)
def no_bundled_data(monkeypatch, tmp_path_factory):
    """随包附带的封面、谱面换成空的：下载、认歌的测试不受包里现在有什么影响（要看包里那份的测试用下面两个）。"""
    empty = tmp_path_factory.getbasetemp() / "no-bundled-data"
    monkeypatch.setattr(bdon, "JACKETS", empty / "jackets")
    monkeypatch.setattr(bdon, "BUNDLE", empty / "bundle")


@pytest.fixture
def real_jackets():
    """包里真的那份封面的目录。"""
    return REAL_JACKETS


@pytest.fixture
def real_bundle():
    """包里真的那份谱面的目录。"""
    return REAL_BUNDLE
