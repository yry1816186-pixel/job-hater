"""QQ 招聘雷达（v3.5）：QQ NT 数据的本地解析能力层。

实况边界（2026-11-20 LIVE 探明，如实声明）：
- **消息库解密**：decrypt/keyring 能力完整（p25c 实战配方产品化），但本机
  QQ 已卸载、密钥缓存已清——存量 nt_msg.db（2026-10-14 冻结）当前不可解。
  重装 QQ 并登录后，新数据的解密链路即可用（detect→keyring→decrypt）。
- **图片 OCR**：nt_data 下仅表情/头像，无聊天图，通道暂无数据可产。
- 识别引擎复用微信雷达 recruit.analyze（零 AI 纯规则），无重复实现。
"""
from jobhater.services.qq.decrypt import (
    QQDecryptError,
    check_key,
    decrypt_database,
    derive_key,
    encrypt_database_for_test,
    looks_like_qq_db,
    salt_candidates,
)
from jobhater.services.qq.detect import QQEnv
from jobhater.services.qq.detect import detect as detect_qq

# 注意：不导出名为 `detect` 的符号——那会遮蔽子模块属性
# （`import jobhater.services.qq.detect as m` 会拿到函数而非模块，实测踩坑）。
__all__ = [
    "QQDecryptError", "check_key", "decrypt_database", "derive_key",
    "encrypt_database_for_test", "looks_like_qq_db", "salt_candidates",
    "QQEnv", "detect_qq",
]
