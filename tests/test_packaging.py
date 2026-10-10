"""打包冒烟测试（ARCH-005/LOG-006）。

构建真实 wheel 并解包断言非 .py 运行资产在场：
- jobhater/db/migrations/*.sql —— 启动即迁移依赖，缺失则全新环境起不来；
- jobhater/services/wechat/ocr_worker.ps1 —— Windows OCR 增强通道，
  此前未列入 package-data（LOG-006），wheel 安装后静默丢失。
同时校验 trove classifiers 全部为 PyPI 合法分类器（ARCH-005：曾出现
非法的 ``Integrity :: 中文``，pip 上传/校验链路会拒收）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import zipfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: PyPI 官方 trove classifiers 清单中本仓声明的子集快照（ARCH-005 修正后的期望值）
_EXPECTED_CLASSIFIER_FRAGMENTS = (
    "License :: OSI Approved :: MIT License",
    "Natural Language :: Chinese",
    "Programming Language :: Python :: 3.12",
)


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> pathlib.Path:
    """模块级共享：三个用例复用同一次真实构建（pip wheel，PEP 517 隔离构建环境）。"""
    out = tmp_path_factory.mktemp("wheels")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"pip wheel 失败:\n{proc.stdout}\n{proc.stderr}"
    wheels = list(out.glob("jobhater-*.whl"))
    assert len(wheels) == 1, f"应恰好产出一个 wheel: {wheels}"
    return wheels[0]


def test_wheel_carries_sql_migrations_and_ocr_script(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    assert "jobhater/services/wechat/ocr_worker.ps1" in names, "LOG-006: ocr_worker.ps1 未入 wheel"
    migrations = [n for n in names if n.startswith("jobhater/db/migrations/") and n.endswith(".sql")]
    assert migrations, "迁移 SQL 未入 wheel"
    assert any(n.endswith("0001_init.sql") for n in migrations)


def test_wheel_metadata_classifiers_are_valid_trove_entries(built_wheel):
    """声明分类器必须是 PyPI 合法值：非法 classifier 会被 twine/pypi 上传链路拒收。"""
    wheel = built_wheel
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(meta_name).decode("utf-8")
    classifier_lines = [line.removeprefix("Classifier: ") for line in metadata.splitlines()
                        if line.startswith("Classifier: ")]
    assert classifier_lines, "wheel METADATA 未携带 classifiers"
    for fragment in _EXPECTED_CLASSIFIER_FRAGMENTS:
        assert fragment in classifier_lines, f"缺少合法分类器 {fragment}: {classifier_lines}"
    assert not any("Integrity ::" in line for line in classifier_lines), "非法 trove 分类器残留"
    assert not any("中文" in line for line in classifier_lines)


def test_segment_extra_removed_from_metadata(built_wheel):
    """ARCH-005：jieba 已是核心依赖，[segment] extra 冗余声明必须清除（防依赖漂移口径分裂）。"""
    wheel = built_wheel
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(meta_name).decode("utf-8")
    provides_extra = [line.removeprefix("Provides-Extra: ") for line in metadata.splitlines()
                      if line.startswith("Provides-Extra: ")]
    assert "segment" not in provides_extra, f"[segment] extra 应已删除: {provides_extra}"
    requires = [line.removeprefix("Requires-Dist: ") for line in metadata.splitlines()
                if line.startswith("Requires-Dist: ")]
    core_jieba = [r for r in requires if r.startswith("jieba") and "extra ==" not in r]
    assert core_jieba, "jieba 必须保留为核心依赖"


def test_manifest_in_lists_data_assets():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include jobhater *.sql" in manifest
    assert "recursive-include jobhater *.ps1" in manifest


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("Integrity :: 中文", False),
        ("Natural Language :: Chinese", True),
    ],
)
def test_pyproject_classifiers_source_of_truth(fragment, expected):
    """源文件层断言（wheel 构建前的快速失败信号）。"""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (f'"{fragment}"' in pyproject) is expected
