"""
黑名单管理服务，提供黑名单添加、移除和查询功能。
"""
from typing import List, Optional, Tuple, Dict, Any
from tortoise.expressions import Q

from zhenxun.services.log import logger

from ..models import PixivContentManagement, KwType, ContentType, PixivGallery


class BlacklistService:
    """黑名单服务类"""

    @classmethod
    async def add_blacklist(
        cls,
        user_id: str,
        content: str,
        bl_type: KwType,
        reason: str = None
    ) -> str:
        """添加黑名单

        参数:
            user_id: 用户ID
            content: 内容
            bl_type: 黑名单类型
            reason: 原因

        返回:
            str: 操作结果消息
        """
        try:
            await PixivContentManagement.create(
                content=content,
                content_type=bl_type,
                is_blacklist=True,
                operator_id=user_id,
                reason=reason
            )
            return f"已将 {bl_type.value} 类型的 {content} 添加到黑名单"
        except Exception as e:
            if "unique constraint" in str(e).lower():
                return f"{bl_type.value} 类型的 {content} 已在黑名单中"
            logger.error(f"添加黑名单失败: {e}")
            return f"添加黑名单失败: {str(e)}"

    @classmethod
    async def remove_blacklist(cls, content: str, bl_type: Optional[KwType] = None) -> str:
        """移除黑名单

        参数:
            content: 内容
            bl_type: 黑名单类型，不指定则匹配所有类型

        返回:
            str: 操作结果消息
        """
        query = PixivContentManagement.filter(content=content, is_blacklist=True)
        if bl_type:
            query = query.filter(content_type=bl_type)

        count = await query.count()
        if count == 0:
            return f"未找到 {content} 的黑名单记录"

        await query.delete()
        return f"已从黑名单中移除 {content}，共 {count} 条记录"

    @classmethod
    async def remove_blacklist_by_id(cls, blacklist_id: int) -> str:
        """通过ID移除黑名单

        参数:
            blacklist_id: 黑名单记录ID

        返回:
            str: 操作结果消息
        """
        blacklist_item = await PixivContentManagement.filter(
            id=blacklist_id, is_blacklist=True
        ).first()

        if not blacklist_item:
            return f"未找到ID为 {blacklist_id} 的黑名单记录"

        content = blacklist_item.content
        content_type = blacklist_item.content_type.value
        await blacklist_item.delete()
        return f"已从黑名单中移除 {content_type} 类型的 {content} (ID: {blacklist_id})"

    @classmethod
    async def get_blacklist(cls, bl_type: Optional[KwType] = None) -> List[Dict[str, Any]]:
        """获取黑名单列表

        参数:
            bl_type: 黑名单类型，不指定则返回所有类型

        返回:
            List[Dict[str, Any]]: 黑名单列表
        """
        query = PixivContentManagement.filter(is_blacklist=True)
        if bl_type:
            query = query.filter(content_type=bl_type)

        result = await query.values("id", "content", "content_type", "reason", "operator_id", "create_time")
        return list(result)

    @classmethod
    async def apply_blacklist_filter(cls, query):
        """应用黑名单过滤

        参数:
            query: 查询对象

        返回:
            过滤后的查询对象
        """
        # 获取所有黑名单记录
        blacklist = await PixivContentManagement.filter(is_blacklist=True)

        # 按类型分组
        uid_blacklist = [bl.content for bl in blacklist if bl.content_type == KwType.UID]
        pid_blacklist = [bl.content for bl in blacklist if bl.content_type == KwType.PID]
        tag_blacklist = [bl.content for bl in blacklist if bl.content_type == KwType.KEYWORD]

        # 应用过滤条件
        if uid_blacklist:
            query = query.filter(~Q(uid__in=uid_blacklist))

        if pid_blacklist:
            query = query.filter(~Q(pid__in=pid_blacklist))

        for tag in tag_blacklist:
            query = query.filter(~Q(tags__contains=tag))

        return query


# 黑名单服务实例
blacklist_service = BlacklistService()