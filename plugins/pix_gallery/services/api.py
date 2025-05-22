"""
API服务，提供与外部API的交互功能。
"""
import asyncio
import random
import time
from typing import Dict, Any, Optional, List

import aiohttp
from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from ..models import KwType
from ..utils import get_api
from ..config import base_config, get_api_settings


class ApiService:
    """API服务类"""
    
    # 从配置中获取API设置
    _api_settings = get_api_settings()
    _last_request_time = 0
    _request_interval = _api_settings["request_interval"]  # 请求间隔秒数
    _retry_count = _api_settings["retry_count"]  # 最大重试次数
    _retry_delay = _api_settings["retry_delay"]  # 重试等待时间（秒）
    
    # 处理控制标志
    is_processing = False    # 当前是否有处理队列在进行
    stop_requested = False   # 是否已请求停止处理
    task_id = None           # 当前任务ID
    
    @classmethod
    def start_processing(cls, task_id: str) -> bool:
        """开始处理任务
        
        参数:
            task_id: 任务ID
            
        返回:
            bool: 是否成功开始任务
        """
        if cls.is_processing:
            return False
        
        cls.is_processing = True
        cls.stop_requested = False
        cls.task_id = task_id
        return True
    
    @classmethod
    def stop_processing(cls) -> bool:
        """请求停止当前处理任务
        
        返回:
            bool: 是否有任务被请求停止
        """
        if not cls.is_processing:
            return False
        
        cls.stop_requested = True
        return True
    
    @classmethod
    def reset_processing_state(cls) -> None:
        """重置处理状态"""
        cls.is_processing = False
        cls.stop_requested = False
        cls.task_id = None
    
    @classmethod
    def get_processing_status(cls) -> Dict[str, Any]:
        """获取当前处理状态
        
        返回:
            Dict[str, Any]: 处理状态信息
        """
        return {
            "is_processing": cls.is_processing,
            "stop_requested": cls.stop_requested,
            "task_id": cls.task_id
        }
    
    @classmethod
    def _parse_next_url(cls, next_url: str) -> Dict[str, Any]:
        """解析下一页URL参数
        
        参数:
            next_url: 下一页URL
            
        返回:
            Dict[str, Any]: 解析后的参数
        """
        # 示例: https://app-api.pixiv.net/v1/user/illusts?user_id=12345&offset=30
        # 解析为: {'user_id': '12345', 'offset': '30'}
        try:
            if "?" not in next_url:
                return {}
            
            params_str = next_url.split("?")[1]
            params = {}
            
            for param in params_str.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    params[key] = value
            
            return params
        except Exception as e:
            logger.error(f"解析下一页URL参数失败: {e}")
            return {}

    @classmethod
    async def call_api(cls, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用API并处理速率限制和重试
        
        参数:
            endpoint: API端点
            params: 请求参数
            
        返回:
            Dict[str, Any]: API响应数据
        """
        retry_count = 0
        hibiapi = base_config.get("HIBIAPI_URL")
        url = f"{hibiapi}/api/pixiv/{endpoint}"
        
        while retry_count < cls._retry_count:
            # 速率限制: 确保请求间隔足够长
            current_time = time.time()
            time_since_last_request = current_time - cls._last_request_time
            
            if time_since_last_request < cls._request_interval:
                # 等待满足请求间隔
                await asyncio.sleep(cls._request_interval - time_since_last_request)
            
            try:
                # 更新上次请求时间
                cls._last_request_time = time.time()
                
                # 发送请求
                response = await AsyncHttpx.get(url, params=params)
                
                # 处理状态码
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:  # 请求过多
                    retry_count += 1
                    retry_delay = cls._retry_delay * (2 ** (retry_count - 1))  # 指数退避
                    logger.warning(f"API请求受到速率限制(429)，将在 {retry_delay} 秒后进行第 {retry_count} 次重试")
                    await asyncio.sleep(retry_delay)
                else:
                    # 其他错误，直接抛出
                    raise Exception(f"API请求失败: {response.status_code}")
            
            except Exception as e:
                retry_count += 1
                if retry_count >= cls._retry_count:
                    logger.error(f"API请求失败且已达到最大重试次数: {e}")
                    raise
                
                retry_delay = cls._retry_delay * (2 ** (retry_count - 1))
                logger.warning(f"API请求失败，将在 {retry_delay} 秒后进行第 {retry_count} 次重试: {e}")
                await asyncio.sleep(retry_delay)
        
        # 不应该执行到这里，因为循环内要么成功返回，要么达到最大重试次数后抛出异常
        raise Exception("API请求失败，超出最大重试次数")
    
    @classmethod
    async def fetch_pid(cls, pid: str) -> Dict[str, Any]:
        """获取PID数据
        
        参数:
            pid: 图片ID
            
        返回:
            Dict[str, Any]: 图片数据
        """
        api_endpoint = "illust"
        params = {"id": pid}
        
        try:
            # 使用call_api而不是直接调用AsyncHttpx
            data = await cls.call_api(api_endpoint, params)
            
            if data.get("error"):
                error_msg = data["error"].get("message") or data["error"].get("user_message") or str(data["error"])
                raise Exception(f"API返回错误: {error_msg}")
            
            return data
        except Exception as e:
            logger.error(f"获取PID {pid} 数据失败: {e}")
            raise
    
    @classmethod
    async def verify_uid_exists(cls, uid: str) -> bool:
        """仅验证UID是否存在，不获取作品列表
        
        参数:
            uid: 用户ID
            
        返回:
            bool: 用户是否存在
        """
        api_endpoint = "member_illust"
        params = {"id": uid, "type": "illust", "page": 1}
        
        try:
            data = await cls.call_api(api_endpoint, params)
            
            # 检查是否有错误
            if data.get("error"):
                return False
            
            # 检查用户信息是否存在
            return "user" in data and data["user"] is not None
        except Exception as e:
            logger.error(f"验证UID {uid} 失败: {e}")
            return False
    
    @classmethod
    async def fetch_uid(cls, uid: str, max_pages: int = 50, start_page: int = 1) -> Dict[str, Any]:
        """获取指定用户的所有作品
        
        参数:
            uid: 用户ID
            max_pages: 最大获取页数
            start_page: 起始页码
            
        返回:
            dict: 作品信息
        """
        logger.info(f"开始获取用户 {uid} 的作品，从第 {start_page} 页开始，最大页数 {max_pages}")
        
        illusts = []
        current_page = 0
        last_page = 0
        
        try:
            # 获取用户信息和作品 - 使用同一API端点和参数格式
            api_endpoint = "member_illust"
            
            # 获取作品
            for page in range(start_page, start_page + max_pages):
                current_page = page
                
                # 请求当前页
                params = {"id": uid, "type": "illust", "page": page}
                logger.debug(f"获取用户 {uid} 第 {page} 页作品")
                
                # 获取页面数据
                page_data = await cls.call_api(api_endpoint, params)
                
                # 提取插画数据
                new_illusts = page_data.get("illusts", [])
                
                # 第一页时获取用户信息
                if page == start_page:
                    user_info = page_data.get("user", {})
                    user_name = user_info.get("name", "")
                    profile_img = user_info.get("profile_image_urls", {}).get("medium", "")
                
                # 添加作品到列表
                illusts.extend(new_illusts)
                last_page = page
                
                # 如果当前页没有作品，说明已到达最后一页
                if not new_illusts:
                    logger.info(f"用户 {uid} 第 {page} 页没有更多作品，停止获取")
                    break
                
                # 记录进度
                if page % 5 == 0:
                    logger.info(f"已获取用户 {uid} 的 {page} 页作品，共 {len(illusts)} 个")
            
            logger.info(f"完成获取用户 {uid} 的作品，共 {len(illusts)} 个，获取 {current_page - start_page + 1} 页")
            
            # 返回结果
            return {
                "illusts": illusts,
                "user": {
                    "id": int(uid),
                    "name": user_name,
                    "account": uid,  # 添加必需的account字段
                    "profile_image_urls": {"medium": profile_img}  # 添加正确格式的profile_image_urls字段
                },
                "last_page": last_page  # 记录最后成功获取的页码
            }
            
        except Exception as e:
            logger.error(f"获取用户 {uid} 作品失败: {e}")
            raise
    
    @classmethod
    async def fetch_keyword(cls, keyword: str, page: int = 1) -> Dict[str, Any]:
        """获取关键词搜索数据
        
        参数:
            keyword: 关键词
            page: 页码
            
        返回:
            Dict[str, Any]: 搜索结果
        """
        api_endpoint = "search"
        params = {"word": keyword, "page": page}
        
        try:
            # 使用call_api而不是直接调用AsyncHttpx
            data = await cls.call_api(api_endpoint, params)
            
            if data.get("error"):
                error_msg = data["error"].get("message") or data["error"].get("user_message") or str(data["error"])
                raise Exception(f"API返回错误: {error_msg}")
            
            # 添加关键词字段
            data["keyword"] = keyword
            
            return data
        except Exception as e:
            logger.error(f"获取关键词 {keyword} 搜索数据失败: {e}")
            raise


# API服务实例
api_service = ApiService()