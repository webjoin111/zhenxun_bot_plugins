import random
import time
from typing import Dict, Any, Optional

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.log import logger
from .config import base_config
from .models import KwType, InfoModel


def debug_query(query, query_name="未命名查询"):
    """SQL 查询调试工具，输出 SQL 语句"""

    try:
        sql = query.sql()
        logger.debug(f"查询名称: {query_name}")
        logger.debug(f"SQL: {sql}")
        return sql
    except Exception as e:
        logger.error(f"无法获取 SQL: {e}")
        return None


def get_api(t: KwType) -> str:
    """返回接口 API 地址

    参数:
        t: 关键词类型

    返回:
        str: API 地址
    """
    hibiapi = base_config.get("HIBIAPI_URL")
    if t == KwType.PID:
        return f"{hibiapi}/api/pixiv/illust"
    elif t == KwType.UID:
        return f"{hibiapi}/api/pixiv/member_illust"
    return f"{hibiapi}/api/pixiv/search"


def get_temp_image_path(pid: str) -> str:
    """获取临时图片路径

    参数:
        pid: 图片ID

    返回:
        str: 临时文件路径
    """
    return str(TEMP_PATH / f"pix_{pid}_{random.randint(1, 1000)}.png")


def is_chinese(char: str) -> bool:
    """判断字符是否为中文

    参数:
        char: 需要判断的字符

    返回:
        bool: 是否为中文
    """
    return "\u4e00" <= char <= "\u9fff"


def is_japanese(char: str) -> bool:
    """判断字符是否为日文

    参数:
        char: 需要判断的字符

    返回:
        bool: 是否为日文
    """
    return any(0x3040 <= ord(c) <= 0x30FF for c in char)


def detect_language(text: str) -> str:
    """检测文本语言

    参数:
        text: 文本内容

    返回:
        str: 语言代码 ('cn', 'jp', 'en')
    """
    if any(is_chinese(c) for c in text):
        return "cn"
    elif any(is_japanese(c) for c in text):
        return "jp"
    return "en"


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


class InfoStorage:
    """消息信息存储类"""

    data: Dict[str, InfoModel] = {}

    @classmethod
    def add(cls, msg_id: str, pix: Any):
        """添加图片信息

        参数:
            msg_id: 消息id
            pix: 图片对象
        """
        cls.data[msg_id] = InfoModel(msg_id=msg_id, time=int(time.time()), info=pix)

    @classmethod
    def get(cls, msg_id: str) -> Optional[Any]:
        """获取图片信息

        参数:
            msg_id: 消息id

        返回:
            Optional[Any]: 图片信息对象
        """
        return info.info if (info := cls.data.get(msg_id)) else None

    @classmethod
    def remove_expired(cls, expire_time: int = 10800):
        """移除超时的图片数据

        参数:
            expire_time: 过期时间(秒)，默认3小时
        """
        now = time.time()
        keys_to_remove = []
        for key, value in cls.data.items():
            if now - value.time > expire_time:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            cls.data.pop(key)
