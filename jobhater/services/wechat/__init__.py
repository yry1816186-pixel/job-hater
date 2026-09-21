"""微信本地数据源：检测 → 密钥提取 → 解密 → 消息解析 → 招聘识别。

子模块：
- ``detect``：环境/账号/进程自动发现（只读）；
- ``decrypt``：SQLCipher4 变体解密 + 测试用加密器；
- ``keyring_scan``：微信进程内存密钥检索（Windows）；
- ``parser``：微信 4.x 消息库 → 结构化消息；
- ``recruit``：招聘信息识别引擎（纯规则、可解释）；
- ``service``：编排（扫描进度/结果缓存/导入岗位）。
"""
from jobhater.services.wechat.detect import WeChatAccount, WeChatEnv, detect
from jobhater.services.wechat.recruit import RecruitHit, analyze

__all__ = ["WeChatAccount", "WeChatEnv", "detect", "RecruitHit", "analyze"]
