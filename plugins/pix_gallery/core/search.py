from typing import List, Optional, Tuple
from tortoise.expressions import Q

from zhenxun.services.log import logger

from ..models import PixivGallery, SearchParams
from ..config import base_config
from ..services.translation import get_tag_translator
from ..services.blacklist import blacklist_service
import time
import random

R18_TAGS = frozenset(
    {
        "r18",
        "r-18",
        "r_18",
        "r 18",
        "18禁",
        "nsfw",
    }
)


class SearchEngine:
    """搜索引擎类"""

    @classmethod
    async def search(cls, params: SearchParams) -> Tuple[List[PixivGallery], int]:
        """
        执行高性能搜索，实现严格的 R18 内容隔离

        Args:
            params: 搜索参数封装对象

        Returns:
            Tuple[List[PixivGallery], int]: 过滤后的图片列表及总数
        """
        import time
        import random

        session_id = f"search_{int(time.time())}_{random.randint(1000, 9999)}"
        logger.info(
            f"[会话ID:{session_id}] 开始执行搜索: nsfw_tag={params.nsfw_tag}, limit={params.limit}"
        )

        query = PixivGallery.filter(block_level__isnull=True)
        initial_count = await query.count()
        logger.debug(f"[会话ID:{session_id}] 初始查询结果: {initial_count}张")

        query = await blacklist_service.apply_blacklist_filter(query)
        after_blacklist_count = await query.count()
        logger.debug(f"[会话ID:{session_id}] 黑名单过滤后: {after_blacklist_count}张")

        if params.nsfw_tag is not None:
            if params.nsfw_tag == 0:
                query = query.filter(nsfw_tag__lt=2)
                logger.debug(f"[会话ID:{session_id}] 应用nsfw_tag < 2过滤")
            else:
                query = query.filter(nsfw_tag=params.nsfw_tag)
                logger.debug(
                    f"[会话ID:{session_id}] 仅查询nsfw_tag={params.nsfw_tag}的内容"
                )
        else:
            logger.debug(f"[会话ID:{session_id}] 未指定nsfw_tag，不进行主R18过滤")

        after_nsfw_count = await query.count()
        logger.debug(f"[会话ID:{session_id}] R18过滤后剩余: {after_nsfw_count}张")

        if params.is_ai is not None:
            query = query.filter(is_ai=params.is_ai)
            logger.debug(f"[会话ID:{session_id}] AI图片过滤: is_ai={params.is_ai}")

        if params.bookmarks_range:
            min_bookmarks, max_bookmarks = params.bookmarks_range
            if min_bookmarks is not None:
                query = query.filter(total_bookmarks__gte=min_bookmarks)
                logger.debug(f"[会话ID:{session_id}] 最低收藏数: {min_bookmarks}")
            if max_bookmarks is not None:
                query = query.filter(total_bookmarks__lte=max_bookmarks)
                logger.debug(f"[会话ID:{session_id}] 最高收藏数: {max_bookmarks}")

        if params.date_range:
            start_date, end_date = params.date_range
            if start_date:
                query = query.filter(create_time__gte=start_date)
                logger.debug(f"[会话ID:{session_id}] 开始日期: {start_date}")
            if end_date:
                query = query.filter(create_time__lte=end_date)
                logger.debug(f"[会话ID:{session_id}] 结束日期: {end_date}")

        if params.author:
            query = query.filter(author__icontains=params.author)
            logger.debug(f"[会话ID:{session_id}] 作者匹配: {params.author}")

        if params.uid:
            query = query.filter(uid=params.uid)
            logger.debug(f"[会话ID:{session_id}] UID匹配: {params.uid}")

        if params.pid:
            query = query.filter(pid=params.pid)
            logger.debug(f"[会话ID:{session_id}] PID匹配: {params.pid}")

        if params.tags:
            logger.debug(
                f"[会话ID:{session_id}] 处理标签: {params.tags}, 翻译={params.translate_tags}, "
                f"扩展={params.expand_tags}, 模式={params.tag_search_mode}"
            )

            normalized_tags = []
            for tag in params.tags:
                tag = tag.strip()
                if tag:
                    tag = tag.replace("_", " ").replace("-", " ")
                    normalized_tags.append(tag)

                    tag_lower = tag.lower()
                    if tag_lower == "genshin":
                        normalized_tags.append("GenshinImpact")
                    elif tag_lower == "nikke":
                        normalized_tags.append("NIKKE")
                        normalized_tags.append("勝利の女神:NIKKE")
                    elif tag_lower == "bluearchive" or tag_lower == "blue archive":
                        normalized_tags.append("BlueArchive")
                        normalized_tags.append("Blue Archive")

            logger.debug(f"[会话ID:{session_id}] 规范化后标签: {normalized_tags}")

            if params.translate_tags:
                if params.expand_tags:
                    expanded_tags = await get_tag_translator().expand_tags(
                        normalized_tags
                    )
                    logger.debug(f"[会话ID:{session_id}] 扩展后标签: {expanded_tags}")

                    tag_conditions = []
                    for tag in expanded_tags:
                        tag_condition = Q(tags__icontains=tag)
                        tag_conditions.append(tag_condition)

                        logger.debug(
                            f"[会话ID:{session_id}] 为标签 '{tag}' 创建匹配条件"
                        )

                    if params.tag_search_mode == "AND":
                        for condition in tag_conditions:
                            query = query.filter(condition)
                        logger.debug(
                            f"[会话ID:{session_id}] 应用AND匹配模式（需同时满足所有标签）"
                        )
                    else:
                        combined_condition = Q()
                        for condition in tag_conditions:
                            combined_condition |= condition
                        query = query.filter(combined_condition)
                        logger.debug(
                            f"[会话ID:{session_id}] 应用OR匹配模式（满足任一标签即可）"
                        )
                else:
                    translated_tags = await get_tag_translator().translate_tags(
                        normalized_tags, "cn"
                    )
                    logger.debug(f"[会话ID:{session_id}] 翻译后标签: {translated_tags}")

                    all_search_tags = set(normalized_tags + translated_tags)
                    logger.debug(
                        f"[会话ID:{session_id}] 搜索标签（原始+翻译）: {all_search_tags}"
                    )

                    if params.tag_search_mode == "AND":
                        for tag in all_search_tags:
                            query = query.filter(
                                Q(tags__icontains=tag) | Q(title__icontains=tag)
                            )
                        logger.debug(f"[会话ID:{session_id}] 使用AND模式匹配标签和标题")
                    else:
                        combined_condition = Q()
                        for tag in all_search_tags:
                            combined_condition |= Q(tags__icontains=tag) | Q(
                                title__icontains=tag
                            )
                        query = query.filter(combined_condition)
                        logger.debug(f"[会话ID:{session_id}] 使用OR模式匹配标签和标题")
            else:
                if params.tag_search_mode == "AND":
                    for tag in normalized_tags:
                        tag_condition = Q(tags__icontains=tag) | Q(title__icontains=tag)
                        query = query.filter(tag_condition)
                        logger.debug(
                            f"[会话ID:{session_id}] 为原始标签'{tag}'创建AND匹配条件"
                        )
                else:
                    combined_condition = Q()
                    for tag in normalized_tags:
                        tag_condition = Q(tags__icontains=tag) | Q(title__icontains=tag)
                        combined_condition |= tag_condition

                    query = query.filter(combined_condition)
                    logger.debug(f"[会话ID:{session_id}] 为原始标签创建OR匹配条件")

        if params.exclude_tags:
            normalized_exclude_tags = [
                tag.strip() for tag in params.exclude_tags if tag.strip()
            ]
            for tag in normalized_exclude_tags:
                query = query.filter(~Q(tags__icontains=tag))
                logger.debug(f"[会话ID:{session_id}] 排除标签: {tag}")

        try:
            total_count = await query.count()
            logger.info(f"[会话ID:{session_id}] 匹配总数: {total_count}张")
        except Exception as e:
            logger.error(f"[会话ID:{session_id}] 计算总数异常: {e}")
            total_count = 0

        if params.sort_by == "bookmarks":
            sort_field = "total_bookmarks"
        elif params.sort_by == "date":
            sort_field = "create_time"
        else:
            sort_field = "id"

        if params.sort_order.lower() == "desc":
            sort_field = f"-{sort_field}"

        logger.debug(f"[会话ID:{session_id}] 排序字段: {sort_field}")

        try:
            if params.sort_by == "random":
                if total_count <= params.limit:
                    results = await query.limit(params.limit)
                    logger.debug(
                        f"[会话ID:{session_id}] 返回全部匹配结果: {len(results)}张"
                    )
                else:
                    logger.debug(f"[会话ID:{session_id}] 使用通用随机排序方法")

                    limit_random = base_config.get("LIMIT_RANDOM_RESULTS")

                    if limit_random:
                        id_list = await query.values_list("id", flat=True)

                        if len(id_list) > 1000:
                            logger.warning(
                                f"[会话ID:{session_id}] 结果集过大({len(id_list)}条)，限制为随机1000条"
                            )
                            id_list = id_list[:1000]

                        if len(id_list) <= params.limit:
                            sampled_ids = id_list
                        else:
                            sampled_ids = random.sample(id_list, params.limit)

                        if sampled_ids:
                            results = await PixivGallery.filter(id__in=sampled_ids)
                            logger.debug(
                                f"[会话ID:{session_id}] 随机抽样结果: {len(results)}张"
                            )
                        else:
                            results = []
                            logger.debug(f"[会话ID:{session_id}] 随机抽样结果为空")
                    else:
                        total_pages = (total_count + 999) // 1000

                        if total_count <= params.limit:
                            results = await query.limit(params.limit)
                            logger.debug(
                                f"[会话ID:{session_id}] 结果少于请求数量，返回全部: {len(results)}张"
                            )
                        else:
                            random_page = random.randint(0, total_pages - 1)
                            offset = random_page * 1000

                            fetch_limit = min(1000, total_count - offset)

                            page_results = await query.offset(offset).limit(fetch_limit)
                            logger.debug(
                                f"[会话ID:{session_id}] 随机页数据: 页码={random_page}, 数量={len(page_results)}张"
                            )

                            if len(page_results) > params.limit:
                                indices = random.sample(
                                    range(len(page_results)), params.limit
                                )
                                results = [page_results[i] for i in indices]
                                logger.debug(
                                    f"[会话ID:{session_id}] 从页面数据中抽样: {len(results)}张"
                                )
                            else:
                                results = page_results
                                logger.debug(
                                    f"[会话ID:{session_id}] 使用整页数据: {len(results)}张"
                                )
            else:
                results = (
                    await query.order_by(sort_field)
                    .offset(params.offset)
                    .limit(params.limit)
                )
                logger.debug(f"[会话ID:{session_id}] 排序后结果: {len(results)}张")
        except Exception as e:
            logger.error(f"[会话ID:{session_id}] 获取结果集异常: {e}")
            return [], total_count

        if results:
            logger.info(f"[会话ID:{session_id}] 搜索成功，找到 {len(results)} 条结果")
            sample_result = results[0] if results else None
            if sample_result:
                logger.debug(
                    f"[会话ID:{session_id}] 样本结果: pid={sample_result.pid}, tags={sample_result.tags[:50]}..."
                )

            result_ids = [f"{r.pid}(tags={r.tags[:20]}...)" for r in results[:3]]
            if result_ids:
                logger.debug(
                    f"[会话ID:{session_id}] 前三条结果: {', '.join(result_ids)}"
                )
        else:
            logger.warning(
                f"[会话ID:{session_id}] 搜索未找到结果，总记录数: {total_count}"
            )

            log_sql = str(query.sql())[:1000]
            logger.debug(f"[会话ID:{session_id}] 查询SQL: {log_sql}...")

        if params.nsfw_tag == 0:
            final_safe_results = []
            for pix in results:
                # 严格检查 nsfw_tag，如果是 R18 (nsfw_tag >= 2)，直接跳过
                if pix.nsfw_tag >= 2:
                    logger.error(
                        f"[会话ID:{session_id}] 安全检查：发现R18标记图片: pid={pix.pid}, nsfw_tag={pix.nsfw_tag}"
                    )
                    continue

                # 检查标签中是否包含 R18 相关标签
                actual_tags_lower = {
                    tag.strip().lower() for tag in pix.tags.split(",") if tag.strip()
                }

                # 只有在用户明确搜索了某个标签，并且该标签在图片标签中时，才跳过 R18 标签检查
                bypass_r18_check = False
                if params.tags:
                    user_searched_tags_lower = {
                        tag.strip().lower() for tag in params.tags if tag.strip()
                    }
                    if any(
                        searched_tag in actual_tags_lower
                        for searched_tag in user_searched_tags_lower
                    ):
                        bypass_r18_check = True
                        logger.debug(
                            f"[会话ID:{session_id}] 安全检查：标签直接匹配，跳过精确R18标签检查: pid={pix.pid}"
                        )

                # 如果不跳过 R18 标签检查，则检查是否包含 R18 相关标签
                if not bypass_r18_check and any(r18_tag in actual_tags_lower for r18_tag in R18_TAGS):
                    logger.warning(
                        f"[会话ID:{session_id}] 安全检查：发现精确R18标签图片: pid={pix.pid}, tags='{pix.tags}', matched_in_actual={actual_tags_lower.intersection(R18_TAGS)}"
                    )
                    continue

                # 通过所有检查，添加到安全结果列表
                final_safe_results.append(pix)

            filtered_count = len(results) - len(final_safe_results)
            if filtered_count > 0:
                logger.warning(
                    f"[会话ID:{session_id}] 安全检查额外过滤掉 {filtered_count} 张疑似R18图片"
                )
            results = final_safe_results

        if params.nsfw_tag == 0:
            found_pids = [f"{pix.pid}-{pix.img_p}" for pix in results]
            logger.info(
                f"[会话ID:{session_id}] 最终返回结果: {len(results)}张, PIDs={found_pids}"
            )
        else:
            logger.info(f"[会话ID:{session_id}] 最终返回结果: {len(results)}张")

        return results, total_count

    @classmethod
    async def search_by_tags(
        cls,
        tags: Optional[List[str]],
        num: int = 1,
        is_r18: bool = False,
        show_ai: bool = False,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        translate_tags: Optional[bool] = None,
        expand_tags: Optional[bool] = None,
        tag_search_mode: Optional[str] = None,
    ) -> List[PixivGallery]:
        """
        按标签搜索，并随机返回结果。
        此方法已重构为使用核心的 cls.search 方法。
        """
        session_id = (
            f"search_by_tags_cmd_{int(time.time())}_{random.randint(1000, 9999)}"
        )
        logger.info(
            f"[会话ID:{session_id}] 执行 search_by_tags: tags={tags}, num={num}, r18={is_r18}, show_ai={show_ai}"
        )

        _sort_by = "random"

        _tag_search_mode = (
            tag_search_mode
            if tag_search_mode is not None
            else base_config.get("DEFAULT_TAG_SEARCH_MODE", "AND")
        )

        params = SearchParams(
            tags=tags or [],
            nsfw_tag=2 if is_r18 else 0,
            is_ai=None if show_ai else False,
            translate_tags=translate_tags,
            expand_tags=expand_tags,
            tag_search_mode=_tag_search_mode,
            sort_by=_sort_by,
            sort_order="desc",
            limit=num,
            offset=0,
        )

        try:
            logger.debug(
                f"[会话ID:{session_id}] 构建的SearchParams: {params.model_dump()}"
            )
        except AttributeError:
            logger.debug(f"[会话ID:{session_id}] 构建的SearchParams: {params.dict()}")

        try:
            results, total_count = await cls.search(params)
            logger.info(
                f"[会话ID:{session_id}] search_by_tags 通过 cls.search 找到 {len(results)}/{total_count} 张图片"
            )
            return results
        except Exception as e:
            logger.error(
                f"[会话ID:{session_id}] search_by_tags 调用 cls.search 时发生错误: {e}"
            )
            return []
