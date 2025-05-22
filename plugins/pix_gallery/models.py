from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List

from pydantic import BaseModel
from tortoise import fields as tortoise_fields

from zhenxun.services.db_context import Model as ZModel

class KwType(str, Enum):
    """关键词类型"""
    KEYWORD = "KEYWORD"
    UID = "UID"
    PID = "PID"


class KwHandleType(str, Enum):
    """关键词处理类型"""
    PASS = "PASS"
    IGNORE = "IGNORE"
    FAIL = "FAIL"
    BLACK = "BLACK"


class FilterAction(str, Enum):
    """过滤动作"""
    BLOCK = "block"
    WARN = "warn"
    ALLOW = "allow"
    NSFW = "nsfw"


class ContentType(str, Enum):
    """内容类型"""
    KEYWORD = "KEYWORD"
    UID = "UID"
    PID = "PID"
    FILTER_RULE = "FILTER_RULE"


class PixivGallery(ZModel):
    """图库数据模型"""
    id = tortoise_fields.IntField(pk=True, generated=True, auto_increment=True)
    pid = tortoise_fields.CharField(255, index=True)
    uid = tortoise_fields.CharField(255, index=True)
    author = tortoise_fields.CharField(255, index=True)
    title = tortoise_fields.CharField(255, index=True)
    width = tortoise_fields.IntField()
    height = tortoise_fields.IntField()
    sanity_level = tortoise_fields.IntField()
    x_restrict = tortoise_fields.IntField()
    total_view = tortoise_fields.IntField()
    total_bookmarks = tortoise_fields.IntField(index=True)
    illust_ai_type = tortoise_fields.IntField()
    tags = tortoise_fields.TextField()
    image_urls = tortoise_fields.JSONField()
    img_p = tortoise_fields.CharField(255)
    nsfw_tag = tortoise_fields.IntField(index=True)
    is_ai = tortoise_fields.BooleanField(default=False, index=True)
    is_multiple = tortoise_fields.BooleanField(default=False)
    block_level = tortoise_fields.IntField(null=True, index=True)
    star = tortoise_fields.IntField(default=0)
    ratio = tortoise_fields.FloatField(default=0)
    create_time = tortoise_fields.DatetimeField(auto_now_add=True, index=True)
    class Meta:
            table = "pixiv_gallery"
            table_description = "pixiv图库数据表"
            unique_together = ("pid", "img_p")

            aerich_safe = True

            indexes = []


class PixivContentManagement(ZModel):
    """内容管理数据模型（合并关键词、黑名单和过滤规则）"""
    id = tortoise_fields.IntField(pk=True, generated=True, auto_increment=True)
    content = tortoise_fields.CharField(255, index=True)
    content_type = tortoise_fields.CharEnumField(ContentType, description="内容类型")
    is_blacklist = tortoise_fields.BooleanField(default=False, description="是否为黑名单")
    handle_type = tortoise_fields.CharEnumField(KwHandleType, null=True, description="处理类型")

    pattern = tortoise_fields.TextField(null=True, description="正则表达式")
    action = tortoise_fields.CharEnumField(FilterAction, null=True, description="过滤动作")
    fields = tortoise_fields.JSONField(null=True, description="应用字段")
    priority = tortoise_fields.IntField(default=0, description="优先级")
    enabled = tortoise_fields.BooleanField(default=True, description="是否启用")

    operator_id = tortoise_fields.CharField(255, null=True, description="操作人ID")
    group_id = tortoise_fields.CharField(255, null=True, description="群组ID")
    reason = tortoise_fields.CharField(255, null=True, description="原因/描述")
    seek_count = tortoise_fields.IntField(default=0, description="搜索次数")
    last_page_collected = tortoise_fields.IntField(default=0, description="上次收录的页码")
    is_available = tortoise_fields.BooleanField(default=True, description="是否可用")
    create_time = tortoise_fields.DatetimeField(auto_now_add=True)
    update_time = tortoise_fields.DatetimeField(null=True)

    class Meta:
        table = "pixiv_content_management"
        table_description = "pixiv内容管理表（关键词、黑名单和过滤规则）"
        unique_together = ("content", "content_type", "is_blacklist")


class User(BaseModel):
    """用户模型"""
    id: int
    name: str
    account: str
    profile_image_urls: Dict[str, str]
    is_followed: Optional[bool] = None


class Tag(BaseModel):
    """标签模型"""
    name: str
    translated_name: Optional[str] = None


class PidModel(BaseModel):
    """PID数据模型"""
    id: int
    title: str
    type: str
    image_urls: Dict[str, str]
    user: User
    tags: List[Tag]
    create_date: str
    page_count: int
    width: int
    height: int
    sanity_level: int
    x_restrict: int
    meta_single_page: Dict[str, str]
    meta_pages: Optional[List[Dict[str, Dict[str, str]]]] = None
    total_view: int
    total_bookmarks: int
    is_bookmarked: bool
    visible: bool
    is_muted: bool
    total_comments: int = 0
    illust_ai_type: int
    illust_book_style: int
    comment_access_control: Optional[int] = None

    @property
    def tags_text(self) -> str:
        """获取标签文本"""
        tags = []
        if self.tags:
            for tag in self.tags:
                tags.append(tag.name)
                if tag.translated_name:
                    tags.append(tag.translated_name)
        return ",".join(tags)


class UidModel(BaseModel):
    """UID数据模型"""
    user: User
    illusts: List[PidModel]
    next_url: Optional[str] = None


class KeywordModel(BaseModel):
    """关键词模型"""
    keyword: str
    illusts: List[PidModel]
    next_url: Optional[str] = None
    search_span_limit: int
    show_ai: bool


class SearchParams(BaseModel):
    """搜索参数模型"""
    tags: Optional[List[str]] = None
    exclude_tags: Optional[List[str]] = None
    bookmarks_range: Optional[tuple[Optional[int], Optional[int]]] = None
    date_range: Optional[tuple[Optional[datetime], Optional[datetime]]] = None
    nsfw_tag: Optional[int] = None
    is_ai: Optional[bool] = None
    author: Optional[str] = None
    uid: Optional[str] = None
    pid: Optional[str] = None
    translate_tags: bool = True
    tag_search_mode: str = "AND"
    sort_by: str = "bookmarks"
    sort_order: str = "desc"
    expand_tags: bool = True
    limit: int = 10
    offset: int = 0

class InfoModel(BaseModel):
    """消息信息模型"""
    msg_id: str
    time: int
    info: Any

    model_config = {
        "arbitrary_types_allowed": True
    }