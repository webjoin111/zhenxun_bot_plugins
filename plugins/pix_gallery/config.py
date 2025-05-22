from typing import Any
from enum import Enum

from zhenxun.configs.config import Config

# 配置级别枚举（保留作为参考常量）
class ConfigLevel(str, Enum):
    """配置级别"""
    GLOBAL = "global"
    GROUP = "group"
    USER = "user"

# 工具函数：值类型转换
def parse_value_type(value: str, target_type: Any) -> Any:
    """按目标类型解析值
    
    参数:
        value: 字符串值
        target_type: 目标类型
        
    返回:
        Any: 转换后的值
    """
    import ast
    
    if target_type == bool:
        return value.lower() in ("true", "yes", "1", "on")
    elif target_type == int:
        return int(value)
    elif target_type == float:
        return float(value)
    elif target_type == tuple or target_type == list:
        try:
            parsed = ast.literal_eval(value)
            if target_type == tuple and not isinstance(parsed, tuple):
                return tuple(parsed)
            if target_type == list and not isinstance(parsed, list):
                return list(parsed)
            return parsed
        except:
            return value
    return value

# 获取配置组实例，用于统一访问配置
base_config = Config.get("pixiv")

# API请求相关默认配置
API_REQUEST_INTERVAL = 1.5  # API请求间隔（秒）
API_RETRY_COUNT = 3      # API请求重试次数
API_RETRY_DELAY = 5      # API请求重试等待时间（秒）

# 从配置文件中获取API设置，如果不存在则使用默认值
def get_api_settings():
    """获取API设置"""
    return {
        "request_interval": base_config.get("API_REQUEST_INTERVAL", API_REQUEST_INTERVAL),
        "retry_count": base_config.get("API_RETRY_COUNT", API_RETRY_COUNT),
        "retry_delay": base_config.get("API_RETRY_DELAY", API_RETRY_DELAY)
    }