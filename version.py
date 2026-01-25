"""
版本管理模块
统一管理应用版本信息
"""

# 应用版本信息
VERSION = "3.4.3"
VERSION_NAME = "Bangumi-Syncer"
VERSION_DESCRIPTION = "自动同步Bangumi观看记录"

# 版本详细信息
VERSION_INFO = {
    "version": VERSION,
    "name": VERSION_NAME,
    "description": VERSION_DESCRIPTION,
    "full_name": f"{VERSION_NAME} v{VERSION}",
}


def get_version() -> str:
    """获取版本号"""
    return VERSION


def get_version_name() -> str:
    """获取应用名称"""
    return VERSION_NAME


def get_full_name() -> str:
    """获取完整版本名称"""
    return VERSION_INFO["full_name"]


def get_version_info() -> dict:
    """获取完整版本信息"""
    return VERSION_INFO.copy()
