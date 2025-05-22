"""
内容过滤服务，包括规则管理和过滤逻辑。
"""
import json
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from pathlib import Path
from datetime import datetime
from tortoise.expressions import Q

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from ..models import PixivGallery, PixivContentManagement, ContentType, FilterAction

# 创建过滤规则存储目录
FILTER_DATA_PATH = DATA_PATH / "pix" / "filters"
FILTER_DATA_PATH.mkdir(parents=True, exist_ok=True)


class ContentFilter:
    """内容过滤服务类"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ContentFilter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # 缓存已编译的正则表达式
        self.compiled_regex: Dict[int, Optional[re.Pattern]] = {}
        self._initialized = True

    def _get_regex(self, rule: PixivContentManagement) -> Optional[re.Pattern]:
        """获取编译好的正则表达式对象

        参数:
            rule: 过滤规则

        返回:
            Optional[re.Pattern]: 正则表达式对象
        """
        if rule.id not in self.compiled_regex:
            try:
                self.compiled_regex[rule.id] = re.compile(rule.pattern, re.IGNORECASE)
            except Exception as e:
                logger.error(f"过滤规则 {rule.name} 正则编译失败: {e}")
                self.compiled_regex[rule.id] = None

        return self.compiled_regex[rule.id]

    async def add_rule(
        self,
        name: str,
        pattern: str,
        action: FilterAction,
        description: str = "",
        fields: List[str] = None,
        priority: int = 0,
        enabled: bool = True,
        creator_id: str = "",
        group_id: str = None,
    ) -> Optional[PixivContentManagement]:
        """添加过滤规则

        参数:
            name: 规则名称
            pattern: 正则表达式
            action: 过滤动作
            description: 规则描述
            fields: 应用字段
            priority: 优先级
            enabled: 是否启用
            creator_id: 创建者ID
            group_id: 群组ID

        返回:
            Optional[PixivContentManagement]: 创建的规则
        """
        # 验证正则表达式
        try:
            re.compile(pattern)
        except Exception as e:
            logger.error(f"过滤规则正则表达式无效: {e}")
            return None

        # 创建规则
        rule = await PixivContentManagement.create(
            content=name,
            content_type=ContentType.FILTER_RULE,
            is_blacklist=False,
            pattern=pattern,
            action=action,
            reason=description or "",
            fields=fields or ["tags", "title", "author"],
            priority=priority,
            enabled=enabled,
            operator_id=creator_id,
            group_id=group_id,
        )

        # 预编译正则表达式
        self._get_regex(rule)
        return rule

    async def update_rule(self, rule_id: int, **kwargs) -> Optional[PixivContentManagement]:
        """更新过滤规则

        参数:
            rule_id: 规则ID
            **kwargs: 更新字段

        返回:
            Optional[PixivContentManagement]: 更新后的规则
        """
        rule = await PixivContentManagement.get_or_none(id=rule_id, content_type=ContentType.FILTER_RULE)
        if not rule:
            return None

        # 验证正则表达式
        if "pattern" in kwargs:
            try:
                re.compile(kwargs["pattern"])
            except Exception as e:
                logger.error(f"过滤规则正则表达式无效: {e}")
                return None

        # 字段映射
        field_mapping = {
            "name": "content",
            "description": "reason",
            "creator_id": "operator_id"
        }

        # 更新字段
        for key, value in kwargs.items():
            # 使用映射转换字段名
            actual_key = field_mapping.get(key, key)
            setattr(rule, actual_key, value)

        await rule.save()

        # 更新缓存的正则表达式
        if "pattern" in kwargs:
            if rule.id in self.compiled_regex:
                del self.compiled_regex[rule.id]
            self._get_regex(rule)

        return rule

    async def delete_rule(self, rule_id: int) -> bool:
        """删除过滤规则

        参数:
            rule_id: 规则ID

        返回:
            bool: 是否成功
        """
        rule = await PixivContentManagement.get_or_none(id=rule_id, content_type=ContentType.FILTER_RULE)
        if not rule:
            return False

        await rule.delete()

        # 清除缓存
        if rule.id in self.compiled_regex:
            del self.compiled_regex[rule.id]

        return True

    async def get_rule(self, rule_id: int) -> Optional[PixivContentManagement]:
        """获取过滤规则

        参数:
            rule_id: 规则ID

        返回:
            Optional[PixivContentManagement]: 过滤规则
        """
        return await PixivContentManagement.get_or_none(id=rule_id, content_type=ContentType.FILTER_RULE)

    async def get_all_rules(
        self,
        group_id: str = None,
        creator_id: str = None,
        enabled_only: bool = False
    ) -> List[PixivContentManagement]:
        """获取所有过滤规则

        参数:
            group_id: 群组ID
            creator_id: 创建者ID
            enabled_only: 是否只返回启用的规则

        返回:
            List[PixivContentManagement]: 规则列表
        """
        query = PixivContentManagement.filter(content_type=ContentType.FILTER_RULE)

        if enabled_only:
            query = query.filter(enabled=True)

        if group_id is not None:
            query = query.filter(Q(group_id=None) | Q(group_id=group_id))

        if creator_id is not None:
            query = query.filter(operator_id=creator_id)

        # 按优先级排序
        result = await query.order_by("-priority", "id")
        return result

    async def filter_content(
        self,
        content: Dict[str, Any],
        group_id: str = None
    ) -> Tuple[bool, FilterAction, List[PixivContentManagement]]:
        """过滤内容

        参数:
            content: 内容字典
            group_id: 群组ID

        返回:
            Tuple[bool, FilterAction, List[PixivContentManagement]]: (是否通过, 动作, 匹配规则列表)
        """
        # 获取适用的规则
        rules = await self.get_all_rules(group_id=group_id, enabled_only=True)

        matched_rules = []
        for rule in rules:
            regex = self._get_regex(rule)
            if not regex:
                continue

            for field in rule.fields:
                if field in content and isinstance(content[field], (str, int, float, bool)):
                    field_value = str(content[field])
                    if regex.search(field_value):
                        matched_rules.append(rule)
                        break

        # 如果没有匹配规则，默认允许
        if not matched_rules:
            return True, FilterAction.ALLOW, []

        # 按优先级排序，取最高优先级规则的动作
        matched_rules.sort(key=lambda r: (-r.priority, r.id))
        highest_action = matched_rules[0].action

        # 如果动作是阻止，则不通过
        if highest_action == FilterAction.BLOCK:
            return False, highest_action, matched_rules

        # 其他动作都通过，但返回具体动作便于处理
        return True, highest_action, matched_rules

    async def apply_filter_to_query(self, query, group_id: str = None):
        """应用过滤规则到查询

        参数:
            query: 数据库查询对象
            group_id: 群组ID

        返回:
            修改后的查询对象
        """
        # 获取所有BLOCK规则
        rules = await self.get_all_rules(group_id=group_id, enabled_only=True)
        block_rules = [r for r in rules if r.action == FilterAction.BLOCK]

        for rule in block_rules:
            regex = self._get_regex(rule)
            if not regex:
                continue

            for field in rule.fields:
                if field in ["tags", "title", "author"]:
                    # 使用正则过滤
                    query = query.filter(~Q(**{f"{field}__iregex": rule.pattern}))

        return query


# 全局过滤器实例
content_filter = ContentFilter()