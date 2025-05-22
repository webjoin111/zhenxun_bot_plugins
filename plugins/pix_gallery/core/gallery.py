"""
图库管理核心模块，负责图库数据的处理和管理。
"""

import traceback
from typing import List, Dict, Any, Optional, Tuple, Set
from copy import deepcopy
from datetime import datetime

from tortoise.expressions import F, Q
from tortoise.functions import Concat
from zhenxun.services.log import logger

from ..models import (
    PixivGallery,
    PidModel,
    UidModel,
    KeywordModel,
    KwType,
    KwHandleType,
    PixivContentManagement,
)
from ..config import base_config
from pydantic import parse_obj_as

from .search import R18_TAGS

AI_TAGS_EXACT = frozenset(
    {
        "ai",
        "ai生成",
        "aiイラスト",
        "stablediffusion",
        "novelai",
        "midjourney",
        "aigenerated",
        "artificialintelligence",
        "aiart",
        "aicg",
    }
)
AI_TAGS_CONTAINS = frozenset(
    {
        "ai作画",
        "ai绘图",
        "aiイラストレーション",
    }
)


class GalleryManager:
    """图库管理类"""

    @classmethod
    async def get_gallery_stats(
        cls, tags: Optional[List[str]] = None
    ) -> Dict[str, int]:
        """获取图库统计信息

        参数:
            tags: 标签列表，用于过滤

        返回:
            Dict[str, int]: 统计数据
        """
        query = PixivGallery.filter(block_level__isnull=True)

        if tags:
            for tag in tags:
                query = query.filter(
                    Q(tags__icontains=tag)
                    | Q(author__icontains=tag)
                    | Q(pid__icontains=tag)
                    | Q(uid__icontains=tag)
                    | Q(title__icontains=tag)
                )

        total = await query.count()
        normal = await query.filter(nsfw_tag__not=2).count()
        r18 = await query.filter(nsfw_tag=2).count()
        ai = await query.filter(is_ai=True).count()

        return {"total": total, "normal": normal, "r18": r18, "ai": ai}

    @classmethod
    async def add_keyword(cls, user_id: str, content: str, kw_type: KwType) -> str:
        """添加关键词

        参数:
            user_id: 用户ID
            content: 关键词内容
            kw_type: 关键词类型

        返回:
            str: 操作结果消息
        """
        try:
            exists = await PixivContentManagement.filter(
                content=content, content_type=kw_type, is_blacklist=False
            ).exists()
            if exists:
                return f"关键词 {content} 已存在，无需重复添加"

            await PixivContentManagement.create(
                content=content,
                content_type=kw_type,
                is_blacklist=False,
                handle_type=KwHandleType.PASS,
                operator_id=user_id,
                seek_count=0,
                is_available=True,
            )

            return f"已成功添加 {kw_type.value} 类型的关键词: {content}"
        except Exception as e:
            logger.error(f"添加关键词失败: {e}")
            return f"添加关键词失败: {e}"

    @classmethod
    async def get_exists_id(cls) -> Set[str]:
        """获取已存在的pid以及img_P

        返回:
            Set[str]: pid_img_p集合
        """
        results = await PixivGallery.annotate(
            t=Concat("pid", "_", F("img_p"))
        ).values_list("t", flat=True)
        return set(results)

    @classmethod
    def pid2model(cls, model: PidModel, img_p: int = 0) -> List[PixivGallery]:
        """转换 PID 数据为图库模型，增强 R18 和 AI 标签检测"""
        data_list = []
        data_json = model.dict()
        del data_json["id"]
        data_json["pid"] = str(model.id)
        data_json["uid"] = str(model.user.id)
        data_json["author"] = model.user.name
        data_json["tags"] = model.tags_text

        logger.debug(
            f"转换 PID={model.id} 数据: title='{model.title}', author='{model.user.name}'"
        )
        logger.debug(f"原始标签 (pid2model): {model.tags_text}")

        raw_tags_text = model.tags_text or ""
        actual_tags_lower = {
            tag.strip().lower() for tag in raw_tags_text.split(",") if tag.strip()
        }

        is_r18 = False
        matched_r18_tags = []
        for tag in actual_tags_lower:
            if tag in R18_TAGS:
                is_r18 = True
                matched_r18_tags.append(tag)

        if is_r18:
            data_json["nsfw_tag"] = 2
            logger.info(
                f"PID={model.id} (pid2model) 设置为 R18，精确匹配标签: {matched_r18_tags}"
            )
        else:
            data_json["nsfw_tag"] = 0
            logger.debug(f"PID={model.id} (pid2model) 初步设置为普通内容 (nsfw_tag=0)")

        # 检查 x_restrict 字段（如果存在）
        x_restrict = getattr(model, "x_restrict", None)
        if x_restrict == 1:
            if data_json["nsfw_tag"] != 2:
                logger.warning(
                    f"PID={model.id} (pid2model) x_restrict=1 表明R18，但标签未检测到，强制设为R18"
                )
            data_json["nsfw_tag"] = 2

        # 检查 sanity_level 字段
        if model.sanity_level >= 6:
            if data_json["nsfw_tag"] != 2:
                logger.warning(
                    f"PID={model.id} (pid2model) sanity_level ({model.sanity_level}) 表明R18，但标签未检测到，强制设为R18"
                )
            data_json["nsfw_tag"] = 2
        elif model.sanity_level >= 4 and data_json["nsfw_tag"] == 0:
            data_json["nsfw_tag"] = 1
            logger.info(
                f"PID={model.id} (pid2model) sanity_level ({model.sanity_level}) 表明轻微R-15，设为nsfw_tag=1"
            )

        # 保存 sanity_level 字段，方便调试
        data_json["sanity_level"] = model.sanity_level

        is_ai = False
        matched_ai_tags = []
        for tag in actual_tags_lower:
            if tag in AI_TAGS_EXACT:
                is_ai = True
                matched_ai_tags.append(tag)
                break
        if not is_ai:
            for tag_part in AI_TAGS_CONTAINS:
                if any(tag_part in pix_tag for pix_tag in actual_tags_lower):
                    is_ai = True
                    matched_ai_tags.append(f"contains:{tag_part}")
                    break

        data_json["is_ai"] = is_ai
        if is_ai:
            logger.info(
                f"PID={model.id} (pid2model) 检测为 AI 生成，匹配标签/部分: {matched_ai_tags}"
            )

        if model.illust_ai_type == 2 and not data_json["is_ai"]:
            logger.warning(
                f"PID={model.id} (pid2model) illust_ai_type (2) 表明AI，但标签未检测到，强制设为AI"
            )
            data_json["is_ai"] = True
        elif model.illust_ai_type == 1 and not data_json["is_ai"]:
            logger.info(
                f"PID={model.id} (pid2model) illust_ai_type (1) 表明可疑AI，标签未检测到，暂不标记为AI"
            )

        data_json["img_p"] = str(img_p)

        max_image_pages = base_config.get("MAX_IMAGE_PAGES", 60)

        if model.meta_pages:
            if len(model.meta_pages) > max_image_pages:
                logger.debug(
                    f"pix PID: {model.id} 图片数量 ({len(model.meta_pages)}) 大于配置上限 ({max_image_pages}), 已跳过"
                )
                return []

            current_img_p = img_p
            for meta_page in model.meta_pages:
                copy_data = deepcopy(data_json)
                copy_data["img_p"] = str(current_img_p)
                copy_data["image_urls"] = meta_page.get("image_urls", {})
                copy_data["is_multiple"] = True

                try:
                    if "create_date" in copy_data and isinstance(
                        copy_data["create_date"], str
                    ):
                        pass
                    data_list.append(PixivGallery(**copy_data))
                except Exception as e:
                    logger.error(
                        f"创建 PixivGallery 对象失败 (多页): PID={model.id}, p={current_img_p}, error: {e}, data: {copy_data}"
                    )
                    continue

                current_img_p += 1
        else:
            data_json["is_multiple"] = False
            try:
                if "create_date" in data_json and isinstance(
                    data_json["create_date"], str
                ):
                    pass
                data_list.append(PixivGallery(**data_json))
            except Exception as e:
                logger.error(
                    f"创建 PixivGallery 对象失败 (单页): PID={model.id}, p={img_p}, error: {e}, data: {data_json}"
                )

        return data_list

    @classmethod
    def uid2model(
        cls, model: UidModel, min_bookmarks_limit: Optional[int] = None
    ) -> List[PixivGallery]:
        """UID数据转换为图库模型

        参数:
            model: UID数据模型
            min_bookmarks_limit: 自定义收藏数阈值，如果为None则使用配置

        返回:
            List[PixGallery]: 图库模型列表
        """
        data_list = []
        max_image_pages = base_config.get("MAX_IMAGE_PAGES", 60)

        # 记录收藏数阈值参数
        if min_bookmarks_limit is not None:
            logger.info(f"[收藏数阈值] uid2model接收到自定义阈值: {min_bookmarks_limit}")
        else:
            logger.info(f"[收藏数阈值] uid2model未接收到自定义阈值，将使用默认值")

        default_bookmarks = base_config.get("SEARCH_HIBIAPI_BOOKMARKS", 5000)
        default_ai_bookmarks = base_config.get("SEARCH_HIBIAPI_AI_BOOKMARKS", 5000)
        logger.info(f"[收藏数阈值] 配置中的默认值: 普通图片={default_bookmarks}, AI图片={default_ai_bookmarks}")

        min_bookmarks = (
            min_bookmarks_limit
            if min_bookmarks_limit is not None
            else default_bookmarks
        )

        logger.info(f"[收藏数阈值] uid2model最终使用的阈值: {min_bookmarks}")

        for illust in model.illusts:
            if not isinstance(illust, PidModel):
                logger.warning(
                    f"UID {model.user.id} 的作品列表中包含非 PidModel 对象: {type(illust)}"
                )
                continue

            # 判断是否为AI图片
            is_ai = False
            raw_tags_text = illust.tags_text or ""
            actual_tags_lower = {
                tag.strip().lower() for tag in raw_tags_text.split(",") if tag.strip()
            }

            # 检查是否包含AI标签
            for tag in actual_tags_lower:
                if tag in AI_TAGS_EXACT:
                    is_ai = True
                    break
            if not is_ai:
                for tag_part in AI_TAGS_CONTAINS:
                    if any(tag_part in pix_tag for pix_tag in actual_tags_lower):
                        is_ai = True
                        break

            # 检查illust_ai_type字段
            if illust.illust_ai_type == 2:
                is_ai = True

            # 根据是否为AI图片选择不同的收藏阈值
            current_min_bookmarks = min_bookmarks
            if is_ai and min_bookmarks_limit is None:
                current_min_bookmarks = default_ai_bookmarks
                logger.debug(f"pix PID: {illust.id} 检测为AI图片，使用AI图片收藏阈值: {current_min_bookmarks}")

            if illust.total_bookmarks >= current_min_bookmarks:
                if illust.meta_pages and len(illust.meta_pages) > max_image_pages:
                    logger.debug(
                        f"pix PID: {illust.id} 图片数量 ({len(illust.meta_pages)}) "
                        f"大于配置上限 ({max_image_pages}), 已跳过"
                    )
                    continue
                data_list.extend(cls.pid2model(illust))
            else:
                logger.debug(
                    f"pix PID: {illust.id} "
                    f"收录收藏数不足: {illust.total_bookmarks}, 阈值: {current_min_bookmarks}, 已跳过"
                )
        return data_list

    @classmethod
    def keyword2model(
        cls, model: KeywordModel, min_bookmarks_limit: Optional[int] = None
    ) -> List[PixivGallery]:
        """关键词数据转换为图库模型

        参数:
            model: 关键词数据模型
            min_bookmarks_limit: 自定义收藏数阈值，如果为None则使用配置

        返回:
            List[PixGallery]: 图库模型列表
        """
        data_list = []
        max_image_pages = base_config.get("MAX_IMAGE_PAGES", 60)

        # 记录收藏数阈值参数
        if min_bookmarks_limit is not None:
            logger.info(f"[收藏数阈值] keyword2model接收到自定义阈值: {min_bookmarks_limit}")
        else:
            logger.info(f"[收藏数阈值] keyword2model未接收到自定义阈值，将使用默认值")

        default_bookmarks = base_config.get("SEARCH_HIBIAPI_BOOKMARKS", 5000)
        default_ai_bookmarks = base_config.get("SEARCH_HIBIAPI_AI_BOOKMARKS", 5000)
        logger.info(f"[收藏数阈值] 配置中的默认值: 普通图片={default_bookmarks}, AI图片={default_ai_bookmarks}")

        min_bookmarks = (
            min_bookmarks_limit
            if min_bookmarks_limit is not None
            else default_bookmarks
        )

        logger.info(f"[收藏数阈值] keyword2model最终使用的阈值: {min_bookmarks}")

        for illust in model.illusts:
            if not isinstance(illust, PidModel):
                logger.warning(
                    f"关键词 {model.keyword} 的作品列表中包含非 PidModel 对象: {type(illust)}"
                )
                continue

            # 判断是否为AI图片
            is_ai = False
            raw_tags_text = illust.tags_text or ""
            actual_tags_lower = {
                tag.strip().lower() for tag in raw_tags_text.split(",") if tag.strip()
            }

            # 检查是否包含AI标签
            for tag in actual_tags_lower:
                if tag in AI_TAGS_EXACT:
                    is_ai = True
                    break
            if not is_ai:
                for tag_part in AI_TAGS_CONTAINS:
                    if any(tag_part in pix_tag for pix_tag in actual_tags_lower):
                        is_ai = True
                        break

            # 检查illust_ai_type字段
            if illust.illust_ai_type == 2:
                is_ai = True

            # 根据是否为AI图片选择不同的收藏阈值
            current_min_bookmarks = min_bookmarks
            if is_ai and min_bookmarks_limit is None:
                current_min_bookmarks = default_ai_bookmarks
                logger.debug(f"pix PID: {illust.id} (关键词搜索) 检测为AI图片，使用AI图片收藏阈值: {current_min_bookmarks}")

            if illust.total_bookmarks >= current_min_bookmarks:
                if illust.meta_pages and len(illust.meta_pages) > max_image_pages:
                    logger.debug(
                        f"pix PID: {illust.id} (关键词搜索) 图片数量 ({len(illust.meta_pages)}) "
                        f"大于配置上限 ({max_image_pages}), 已跳过"
                    )
                    continue
                data_list.extend(cls.pid2model(illust))
            else:
                logger.debug(
                    f"pix PID:{illust.id} (关键词搜索 '{model.keyword}')"
                    f" 收录收藏数不足: {illust.total_bookmarks}, 阈值: {current_min_bookmarks}, 已跳过"
                )
        return data_list

    @classmethod
    async def update_keywords(
        cls,
        kw_type: Optional[KwType] = None,
        limit: Optional[int] = None,
        only_new: bool = False,
        force_update: bool = False,
        max_works_limit: Optional[int] = None,
        continue_last: bool = False,
        min_bookmarks_limit: Optional[int] = None,
        task_id: str = None,
    ) -> Tuple[int, int, int, int, int]:
        """更新关键词数据 (部分优化，主要关注日志和错误处理)"""
        from ..services.api import api_service
        from ..models import PidModel, UidModel, KeywordModel

        # 记录收藏数阈值参数
        if min_bookmarks_limit is not None:
            logger.info(f"[收藏数阈值] update_keywords接收到自定义阈值: {min_bookmarks_limit}")
        else:
            default_bookmarks = base_config.get("SEARCH_HIBIAPI_BOOKMARKS", 5000)
            default_ai_bookmarks = base_config.get("SEARCH_HIBIAPI_AI_BOOKMARKS", 5000)
            logger.info(f"[收藏数阈值] update_keywords未接收到自定义阈值，默认值: 普通图片={default_bookmarks}, AI图片={default_ai_bookmarks}")

        if task_id:
            if not api_service.start_processing(task_id):
                logger.warning(f"已有其他任务正在处理中，无法开始任务 {task_id}")
                return 0, 0, 0, 0, 0
            logger.info(f"开始任务 {task_id}")

        try:
            query = PixivContentManagement.filter(is_available=True, is_blacklist=False)
            if kw_type is not None:
                query = query.filter(content_type=kw_type)

            if limit:
                query = query.order_by("seek_count", "id").limit(limit)
            else:
                query = query.order_by("seek_count", "id")

            if only_new:
                query = query.filter(seek_count=0)

            keywords = await query

            if not keywords:
                logger.warning("没有找到需要更新的关键词")
                return 0, 0, 0, 0, 0

            total_keywords = len(keywords)
            logger.info(f"开始处理 {total_keywords} 个关键词")

            exists_ids = await cls.get_exists_id() if not force_update else set()

            add_count = 0
            exists_count = 0
            skipped_authors = 0
            skipped_fully_collected = 0
            continued_count = 0

            models_to_save_bulk: List[PixivGallery] = []

            for index, keyword_obj in enumerate(keywords, 1):
                if api_service.stop_requested:
                    logger.warning(
                        f"收到停止请求，中断处理，已处理 {index - 1}/{total_keywords} 个关键词"
                    )
                    break

                current_keyword_content = keyword_obj.content
                logger.info(
                    f"处理关键词 {index}/{total_keywords}: {current_keyword_content}, "
                    f"类型: {keyword_obj.content_type}, ID: {keyword_obj.id}"
                )

                try:
                    data_dict = None
                    start_page = 1

                    if (
                        continue_last
                        and keyword_obj.content_type == KwType.UID
                        and keyword_obj.last_page_collected > 0
                    ):
                        start_page = keyword_obj.last_page_collected + 1
                        logger.info(
                            f"继续收录UID {current_keyword_content} 的作品，从第 {start_page} 页开始"
                        )
                        continued_count += 1

                    if keyword_obj.content_type == KwType.UID:
                        try:
                            uid_exists = await api_service.verify_uid_exists(
                                current_keyword_content
                            )
                            if not uid_exists:
                                logger.error(
                                    f"UID {current_keyword_content} 不存在或已被删除，跳过处理"
                                )
                                keyword_obj.seek_count += 1
                                keyword_obj.update_time = datetime.now()
                                await keyword_obj.save()
                                continue
                        except Exception as e_verify:
                            logger.error(
                                f"验证UID {current_keyword_content} 时出错: {e_verify}, 跳过处理"
                            )
                            keyword_obj.seek_count += 1
                            keyword_obj.update_time = datetime.now()
                            await keyword_obj.save()
                            continue

                        if max_works_limit is not None:
                            try:
                                checkpage_data = await api_service.fetch_uid(
                                    current_keyword_content, 1
                                )
                                total_works_estimate = len(
                                    checkpage_data.get("illusts", [])
                                )
                                user_details_in_response = checkpage_data.get(
                                    "user", {}
                                )
                                actual_total_works = user_details_in_response.get(
                                    "total_illusts",
                                    user_details_in_response.get(
                                        "total_public_illusts", total_works_estimate
                                    ),
                                )

                                if actual_total_works >= max_works_limit:
                                    logger.info(
                                        f"跳过大作者UID {current_keyword_content}，作品数量({actual_total_works})超过限制({max_works_limit})"
                                    )
                                    keyword_obj.seek_count += 1
                                    keyword_obj.update_time = datetime.now()
                                    await keyword_obj.save()
                                    skipped_authors += 1
                                    continue
                            except Exception as e_count:
                                logger.error(
                                    f"检查UID {current_keyword_content} 作品数量时出错: {e_count}, 继续处理"
                                )

                    if keyword_obj.content_type == KwType.UID:
                        max_author_pages = base_config.get("MAX_AUTHOR_PAGES", 50)
                        data_dict = await api_service.fetch_uid(
                            current_keyword_content, max_author_pages, start_page
                        )
                        if "last_page" in data_dict:
                            keyword_obj.last_page_collected = data_dict["last_page"]
                    elif keyword_obj.content_type == KwType.PID:
                        if (
                            not force_update
                            and await PixivGallery.filter(
                                pid=current_keyword_content
                            ).exists()
                        ):
                            logger.info(
                                f"PID {current_keyword_content} 已收录，快速跳过"
                            )
                            exists_count += 1
                            keyword_obj.seek_count += 1
                            keyword_obj.update_time = datetime.now()
                            await keyword_obj.save()
                            continue
                        data_dict = await api_service.fetch_pid(current_keyword_content)
                    elif keyword_obj.content_type == KwType.KEYWORD:
                        data_dict = await api_service.fetch_keyword(
                            current_keyword_content
                        )

                    if data_dict:
                        converted_models_for_keyword: List[PixivGallery] = []
                        try:
                            if keyword_obj.content_type == KwType.UID:
                                model = parse_obj_as(UidModel, data_dict)
                                converted_models_for_keyword = cls.uid2model(
                                    model, min_bookmarks_limit
                                )
                            elif keyword_obj.content_type == KwType.PID:
                                if "illust" in data_dict:
                                    model = parse_obj_as(PidModel, data_dict["illust"])
                                    converted_models_for_keyword = cls.pid2model(model)
                                else:
                                    logger.error(
                                        f"PID数据结构异常: {current_keyword_content}, 缺少illust字段. Data: {str(data_dict)[:200]}"
                                    )
                            elif keyword_obj.content_type == KwType.KEYWORD:
                                model = parse_obj_as(KeywordModel, data_dict)
                                converted_models_for_keyword = cls.keyword2model(
                                    model, min_bookmarks_limit
                                )
                        except Exception as e_parse:
                            logger.error(
                                f"转换数据模型失败: {current_keyword_content}, 错误: {e_parse}, "
                                f"Data: {str(data_dict)[:500]}"
                            )
                            logger.debug(f"详细错误: {traceback.format_exc()}")
                            keyword_obj.seek_count += 1
                            keyword_obj.update_time = datetime.now()
                            await keyword_obj.save()
                            continue

                        initial_count_for_keyword = len(converted_models_for_keyword)
                        if not force_update and converted_models_for_keyword:
                            filtered_models_for_keyword = []
                            for model_item in converted_models_for_keyword:
                                key = f"{model_item.pid}_{model_item.img_p}"
                                if key not in exists_ids:
                                    filtered_models_for_keyword.append(model_item)
                                    exists_ids.add(key)
                                else:
                                    exists_count += 1

                            skipped_count_for_keyword = initial_count_for_keyword - len(
                                filtered_models_for_keyword
                            )
                            if skipped_count_for_keyword > 0:
                                logger.info(
                                    f"关键词 '{current_keyword_content}': "
                                    f"过滤已存在数据: 原有 {initial_count_for_keyword} 条，"
                                    f"过滤掉 {skipped_count_for_keyword} 条，"
                                    f"剩余 {len(filtered_models_for_keyword)} 条"
                                )
                            converted_models_for_keyword = filtered_models_for_keyword

                        if converted_models_for_keyword:
                            add_count += len(converted_models_for_keyword)
                            models_to_save_bulk.extend(converted_models_for_keyword)
                            logger.info(
                                f"关键词 '{current_keyword_content}': "
                                f"准备保存 {len(converted_models_for_keyword)} 条新数据"
                            )

                    keyword_obj.seek_count += 1
                    keyword_obj.update_time = datetime.now()
                    await keyword_obj.save()

                except Exception as e_outer:
                    logger.error(
                        f"处理关键词 {current_keyword_content} 过程中发生主循环错误: {e_outer}"
                    )
                    logger.debug(f"详细错误追踪: {traceback.format_exc()}")
                    try:
                        keyword_obj.seek_count += 1
                        keyword_obj.update_time = datetime.now()
                        await keyword_obj.save()
                    except Exception as e_save_keyword:
                        logger.error(
                            f"在错误处理中保存关键词 {current_keyword_content} 状态失败: {e_save_keyword}"
                        )

            if models_to_save_bulk:
                logger.info(f"开始批量保存 {len(models_to_save_bulk)} 条数据...")
                try:
                    await PixivGallery.bulk_create(
                        models_to_save_bulk,
                        ignore_conflicts=True if force_update else False,
                    )
                    logger.info("数据批量保存完成")
                except Exception as e_bulk_save:
                    logger.error(f"批量保存数据失败: {e_bulk_save}。尝试逐条保存...")
                    logger.debug(f"详细错误: {traceback.format_exc()}")
                    saved_individually = 0
                    for model_to_save in models_to_save_bulk:
                        try:
                            await model_to_save.save()
                            saved_individually += 1
                        except Exception as e_single_save:
                            logger.error(
                                f"逐条保存 PID {model_to_save.pid}_{model_to_save.img_p} 失败: {e_single_save}"
                            )
                    logger.info(f"通过逐条保存成功 {saved_individually} 条数据。")
                    if saved_individually < len(models_to_save_bulk):
                        logger.warning(
                            "部分数据在逐条保存时也失败了，最终新增数量可能不完全准确。"
                        )

            if api_service.stop_requested:
                logger.info(
                    f"关键词处理被中断，已处理 {index}/{total_keywords} 个关键词，"
                    f"新增: {add_count}, 已存在: {exists_count}, "
                    f"跳过大作者: {skipped_authors}, 跳过已完整收录: {skipped_fully_collected}, "
                    f"继续收录: {continued_count}"
                )
            else:
                logger.info(
                    f"关键词处理完成，新增: {add_count}, 已存在: {exists_count}, "
                    f"跳过大作者: {skipped_authors}, 跳过已完整收录: {skipped_fully_collected}, "
                    f"继续收录: {continued_count}"
                )

            return (
                add_count,
                exists_count,
                skipped_authors,
                skipped_fully_collected,
                continued_count,
            )

        finally:
            if task_id:
                api_service.reset_processing_state()
                logger.info(f"结束任务 {task_id}")

    @classmethod
    async def handle_keyword(
        cls,
        user_id: str,
        keyword_id: int,
        kw_type: Optional[KwType] = None,
        handle_type: Optional[KwHandleType] = None,
    ) -> str:
        """处理关键词

        参数:
            user_id: 处理人ID
            keyword_id: 关键词ID
            kw_type: 关键词类型
            handle_type: 处理类型

        返回:
            str: 处理结果消息
        """
        query = PixivContentManagement.filter(id=keyword_id, is_blacklist=False)
        if kw_type:
            query = query.filter(content_type=kw_type)

        keyword = await query.first()
        if not keyword:
            return f"未找到ID为 {keyword_id} 的关键词"

        keyword.handle_type = handle_type
        keyword.operator_id = user_id
        keyword.update_time = datetime.now()
        await keyword.save()

        return f"已将关键词 {keyword.content} 设置为 {handle_type.value} 处理方式"

    @classmethod
    async def get_keyword_status(
        cls, kw_type: Optional[KwType] = None
    ) -> List[Dict[str, Any]]:
        """获取关键词状态

        参数:
            kw_type: 关键词类型

        返回:
            List[Dict[str, Any]]: 关键词状态列表
        """
        query = PixivContentManagement.filter(is_blacklist=False)
        if kw_type:
            query = query.filter(content_type=kw_type)

        keywords = await query.order_by("content_type", "id")

        result = []
        for kw in keywords:
            kw_info = {
                "id": kw.id,
                "content": kw.content,
                "kw_type": kw.content_type.value,
                "handle_type": kw.handle_type.value if kw.handle_type else "未处理",
                "seek_count": kw.seek_count,
            }

            collected_works = 0

            if kw.content_type == KwType.UID:
                try:
                    collected_works = await PixivGallery.filter(uid=kw.content).count()
                except Exception as e:
                    logger.error(f"统计UID {kw.content} 已收录作品数量失败: {e}")

            elif kw.content_type == KwType.PID:
                try:
                    pid_exists = await PixivGallery.filter(pid=kw.content).exists()
                    collected_works = 1 if pid_exists else 0
                except Exception as e:
                    logger.error(f"检查PID {kw.content} 收录状态失败: {e}")

            kw_info["collected_works"] = collected_works

            result.append(kw_info)

        return result
