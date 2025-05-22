import time
from enum import Enum

from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    MultiVar,
    Option,
    Query,
    AlconnaMatch,
    Subcommand,
)
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils._image_template import ImageTemplate

from ..models import KwType, KwHandleType, PixivContentManagement
from ..core.gallery import GalleryManager
from ..services.blacklist import blacklist_service
from ..services.api import api_service
from ..config import base_config


_add_matcher = on_alconna(
    Alconna(
        ".pix添加",
        Args["add_type", ["u", "p", "k", "b"]]["content", MultiVar(str)],
    ),
    priority=5,
    block=True,
    permission=SUPERUSER,
)

_handle_matcher = on_alconna(
    Alconna(
        ".pix处理",
        Args["handle_type", ["a", "f", "i", "b"]]["id", int],
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_seek_matcher = on_alconna(
    Alconna(
        ".pix收录",
        Args["seek_type?", ["u", "p", "k", "a"]]["num?", int],
        Option("--force|-f", help_text="强制更新所有数据，包括已收录的"),
        Option("--all|-a", help_text="处理所有关键词，包括已收录的"),
        Option(
            "--max-works|-n",
            Args["max_works", int],
            help_text="仅收录作品数量小于指定值的作者(仅对UID有效)",
        ),
        Option("--continue|-c", help_text="从上次收录的页码继续(仅对UID有效)"),
        Option(
            "--mark|-m",
            Args["min_bookmarks", int],
            help_text="只收录收藏数大于指定值的作品，忽略配置中的限制",
        ),
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_stop_seek_matcher = on_alconna(
    Alconna(
        ".pix停止收录",
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_seek_status_matcher = on_alconna(
    Alconna(
        ".pix收录状态",
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_pix_blacklist_manager = on_alconna(
    Alconna(
        ".pix黑名单",
        Subcommand(
            "add",
            Args["bl_add_type", ["u", "p", "k"]]["bl_add_content", str][
                "bl_add_reason?", str
            ],
        ),
        Subcommand(
            "del",
            Args["bl_del_type?", ["u", "p", "k"]]["bl_del_content", str],
        ),
        Subcommand(
            "view",
            Args["bl_view_type?", ["u", "p", "k"]],
        ),
    ),
    priority=5,
    block=True,
    permission=SUPERUSER,
)

_gallery_matcher = on_alconna(
    Alconna(
        ".pix图库",
        Args["tags?", str] / "\n",
    ),
    priority=5,
    block=True,
)

_keyword_matcher = on_alconna(
    Alconna(
        ".pix查看",
        Args["seek_type?", ["u", "p", "k", "a"]],
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)


@_add_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    add_type: str,
    content: list[str],
):
    """添加命令处理"""
    results = []

    if add_type == "u":
        filtered_uids = []
        skipped_uids = []

        for uid in content:
            exists = await PixivContentManagement.filter(
                content=uid, content_type=KwType.UID, is_blacklist=False
            ).exists()
            if exists:
                skipped_uids.append(uid)
            else:
                filtered_uids.append(uid)

        if skipped_uids:
            results.append(f"跳过已存在的UID: {', '.join(skipped_uids)}")

        content = filtered_uids

        if not content:
            results.append("所有UID都已添加，无需重复添加")
            await MessageUtils.build_message("\n".join(results)).send()
            logger.info(
                f"PIX 添加结果: {results}", arparma.header_result, session=session
            )
            return

    for item in content:
        try:
            if add_type == "b":
                kw_type = KwType.PID
                result = await blacklist_service.add_blacklist(
                    session.user.id, item, kw_type
                )
            elif add_type == "u":
                try:
                    uid_exists = await api_service.verify_uid_exists(item)
                    if uid_exists:
                        kw_type = KwType.UID
                        result = await GalleryManager.add_keyword(
                            session.user.id, item, kw_type
                        )
                    else:
                        result = "当前UID不存在，请检查UID是否正确..."
                except Exception as e:
                    result = f"验证UID时发生错误: {e}"
            elif add_type == "p":
                try:
                    await api_service.fetch_pid(item)
                    kw_type = KwType.PID
                    result = await GalleryManager.add_keyword(
                        session.user.id, item, kw_type
                    )
                except Exception as e:
                    result = f"当前PID不存在，请检查PID是否正确... ({e})"
            else:
                kw_type = KwType.KEYWORD
                result = await GalleryManager.add_keyword(
                    session.user.id, item, kw_type
                )

            results.append(result)
        except Exception as e:
            results.append(f"处理 {add_type}:{item} 时发生错误: {e}")

    await MessageUtils.build_message("\n".join(results)).send()
    logger.info(f"PIX 添加结果: {results}", arparma.header_result, session=session)


@_handle_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    handle_type: str,
    id: int,
):
    """关键词处理命令"""
    handle_map = {
        "a": KwHandleType.PASS,
        "f": KwHandleType.FAIL,
        "i": KwHandleType.IGNORE,
        "b": KwHandleType.BLACK,
    }

    result = await GalleryManager.handle_keyword(
        session.user.id, id, None, handle_map[handle_type]
    )
    await MessageUtils.build_message(result).send()
    logger.info(f"PIX 处理结果: {result}", arparma.header_result, session=session)


@_seek_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    seek_type: Match[str],
    num: Match[int],
    max_works: Match[int] = AlconnaMatch("max-works.max_works"),
):
    """收录命令处理

    处理Pixiv关键词收录操作。
    """
    from datetime import datetime
    import random
    import asyncio

    st = None
    if seek_type.available:
        if seek_type.result == "u":
            st = KwType.UID
            logger.debug("收录类型: UID")
        elif seek_type.result == "p":
            st = KwType.PID
            logger.debug("收录类型: PID")
        elif seek_type.result == "k":
            st = KwType.KEYWORD
            logger.debug("收录类型: 标签")

    n = None
    if num.available:
        n = num.result

    force_update = arparma.find("force")
    only_new = not arparma.find("all")

    max_works_limit = max_works.result if max_works.available else None
    if max_works_limit and max_works_limit <= 0:
        max_works_limit = None

    continue_last = arparma.find("continue")
    if continue_last and (st is None or st != KwType.UID):
        await MessageUtils.build_message("继续收录仅对UID类型有效").finish()

    min_bookmarks_limit = arparma.other_args.get("min_bookmarks")
    logger.info(
        f"[收藏数阈值] arparma.other_args.get('min_bookmarks') = {min_bookmarks_limit}"
    )
    if min_bookmarks_limit is not None and min_bookmarks_limit <= 0:
        min_bookmarks_limit = None

    if min_bookmarks_limit is not None:
        logger.info(
            f"[收藏数阈值] 命令解析: 使用自定义收藏数阈值: {min_bookmarks_limit}"
        )
    else:
        logger.info("[收藏数阈值] 命令解析: 未指定自定义收藏数阈值，将使用默认值")

    task_id = (
        f"pix_collect_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}"
    )
    user_id = session.user.id
    group_id = getattr(session, "group_id", None)

    async def collect_task():
        try:
            logger.info(
                f"[任务ID:{task_id}] 开始执行收录任务，用户: {user_id}, 群组: {group_id}"
            )
            await MessageUtils.build_message(
                f"收录任务已在后台启动 (任务ID: {task_id})\n您可以继续使用其他命令"
            ).send()

            start = time.time()
            result = await GalleryManager.update_keywords(
                st,
                n,
                only_new,
                force_update,
                max_works_limit,
                continue_last,
                min_bookmarks_limit,
                task_id,
            )
            end = time.time()

            message = f"[任务ID:{task_id}] 收录完成\n累计耗时: {int(end - start)} 秒\n共保存 {result[0]} 条数据!\n已存在数据: {result[1]} 条!"
            if max_works_limit and st == KwType.UID:
                message += f"\n已跳过大作者: {result[2]} 个"

            if result[3] > 0:
                message += f"\n已跳过完整收录: {result[3]} 个"

            if continue_last and result[4] > 0:
                message += f"\n继续收录成功: {result[4]} 个UID"

            if min_bookmarks_limit is not None:
                message += f"\n收藏数过滤: >{min_bookmarks_limit} (自定义阈值)"
            else:
                default_bookmarks = base_config.get("SEARCH_HIBIAPI_BOOKMARKS", 5000)
                message += f"\n收藏数过滤: >{default_bookmarks} (默认阈值)"

            if api_service.stop_requested:
                message += "\n注意: 该任务被手动终止"

            await MessageUtils.build_message(message).send()
            logger.info(f"收录任务 {task_id} 完成: {message}")

        except Exception as e:
            error_msg = f"[任务ID:{task_id}] 收录过程中发生错误: {e}"
            await MessageUtils.build_message(error_msg).send()
            logger.error(error_msg)

    asyncio.create_task(collect_task())
    logger.info(f"收录任务 {task_id} 已启动", arparma.header_result, session=session)


@_stop_seek_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
):
    """停止收录命令处理"""
    if api_service.is_processing:
        api_service.stop_processing()
        task_id = api_service.task_id or "未知"
        message = (
            f"已发送停止请求，正在等待当前关键词处理完成后中断任务 (ID: {task_id})"
        )
        await MessageUtils.build_message(message).send()
        logger.info(
            f"PIX 停止收录: 已请求停止任务 {task_id}",
            arparma.header_result,
            session=session,
        )
    else:
        message = "当前没有正在进行的收录任务"
        await MessageUtils.build_message(message).send()
        logger.info(
            "PIX 停止收录: 无任务正在进行", arparma.header_result, session=session
        )


@_seek_status_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
):
    """收录状态查询命令处理"""
    status = api_service.get_processing_status()

    if status["is_processing"]:
        task_id = status["task_id"] or "未知"
        stop_status = "已请求停止" if status["stop_requested"] else "正在进行中"
        message = f"当前收录任务 (ID: {task_id}) {stop_status}"
    else:
        message = "当前没有正在进行的收录任务"

    await MessageUtils.build_message(message).send()
    logger.info(f"PIX 收录状态查询: {message}", arparma.header_result, session=session)


@_pix_blacklist_manager.assign("add")
async def handle_blacklist_add(
    session: Uninfo,
    arparma: Arparma,
    bl_add_type: Query[str] = Query("add.bl_add_type"),
    bl_add_content: Query[str] = Query("add.bl_add_content"),
    bl_add_reason: Query[str] = Query("add.bl_add_reason"),
):
    """黑名单添加命令处理 (子命令)"""
    if not bl_add_type.available or not bl_add_content.available:
        await MessageUtils.build_message(
            "添加黑名单参数不完整。请提供类型和内容。"
        ).finish()
        return

    type_val = bl_add_type.result
    content_val = bl_add_content.result
    kw_type = None

    if type_val == "u":
        kw_type = KwType.UID
    elif type_val == "p":
        kw_type = KwType.PID
    elif type_val == "k":
        kw_type = KwType.KEYWORD
    else:
        await MessageUtils.build_message(
            f"未知的黑名单类型: {type_val}。请使用 u, p, 或 k。"
        ).finish()
        return

    reason_val = bl_add_reason.result if bl_add_reason.available else None
    result_msg = await blacklist_service.add_blacklist(
        session.user.id, content_val, kw_type, reason_val
    )
    await MessageUtils.build_message(result_msg).send()
    logger.info(f"PIX 黑名单添加: {result_msg}", arparma.header_result, session=session)


@_pix_blacklist_manager.assign("del")
async def handle_blacklist_remove(
    session: Uninfo,
    arparma: Arparma,
    bl_del_type: Query[str] = Query("del.bl_del_type"),
    bl_del_content: Query[str] = Query("del.bl_del_content"),
):
    """黑名单移除命令处理 (子命令)"""
    if not bl_del_content.available:
        await MessageUtils.build_message("移除黑名单的内容参数缺失。").finish()
        return

    content_val = bl_del_content.result
    kw_type = None

    if bl_del_type.available:
        type_val = bl_del_type.result
        if type_val == "u":
            kw_type = KwType.UID
        elif type_val == "p":
            kw_type = KwType.PID
        elif type_val == "k":
            kw_type = KwType.KEYWORD

    result_msg = await blacklist_service.remove_blacklist(content_val, kw_type)
    await MessageUtils.build_message(result_msg).send()
    logger.info(f"PIX 黑名单移除: {result_msg}", arparma.header_result, session=session)


@_pix_blacklist_manager.assign("view")
async def handle_blacklist_view(
    session: Uninfo,
    arparma: Arparma,
    bl_view_type: Query[str] = Query("view.bl_view_type"),
):
    """黑名单查看命令处理 (子命令)"""
    kw_type = None
    if bl_view_type.available:
        type_val = bl_view_type.result
        if type_val == "u":
            kw_type = KwType.UID
        elif type_val == "p":
            kw_type = KwType.PID
        elif type_val == "k":
            kw_type = KwType.KEYWORD

    blacklist_items = await blacklist_service.get_blacklist(kw_type)

    column_name = ["ID", "内容", "类型", "原因", "添加人"]
    data_list = [
        [
            item["id"],
            item["content"],
            item["content_type"].value
            if isinstance(item["content_type"], Enum)
            else str(item["content_type"]),
            item["reason"] or "无",
            item["operator_id"],
        ]
        for item in blacklist_items
    ]

    title_suffix = ""
    if kw_type:
        type_map_display = {
            KwType.UID: "UID",
            KwType.PID: "PID",
            KwType.KEYWORD: "关键词",
        }
        title_suffix = f" ({type_map_display.get(kw_type, '')}类型)"

    image = await ImageTemplate.table_page(
        f"PIX黑名单列表{title_suffix}", None, column_name, data_list
    )
    await MessageUtils.build_message(image).send()
    logger.info(
        f"PIX 黑名单查看 (类型: {kw_type.value if kw_type else '所有'})",
        arparma.header_result,
        session=session,
    )


@_gallery_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    tags: Match[str],
):
    """图库统计命令处理"""
    tags_list = []
    if tags.available and tags.result.strip():
        tags_list = tags.result.strip().split()

    stats = await GalleryManager.get_gallery_stats(tags_list)

    column_name = ["类型", "数量"]
    data_list = [
        ["总数", stats["total"]],
        ["普通", stats["normal"]],
        ["R18", stats["r18"]],
        ["AI", stats["ai"]],
    ]

    title = "PIX图库统计"
    if tags_list:
        title += f" (标签: {', '.join(tags_list)})"

    image = await ImageTemplate.table_page(title, None, column_name, data_list)
    await MessageUtils.build_message(image).send(reply_to=True)

    logger.info(
        f"PIX 查看图库 tags: {tags_list}", arparma.header_result, session=session
    )


@_keyword_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    seek_type: Match[str],
):
    """关键词查看命令处理"""
    kw_type = None
    if seek_type.available:
        if seek_type.result == "u":
            kw_type = KwType.UID
        elif seek_type.result == "p":
            kw_type = KwType.PID
        elif seek_type.result == "k":
            kw_type = KwType.KEYWORD

    result = await GalleryManager.get_keyword_status(kw_type)

    column_name = ["ID", "内容", "类型", "处理方式", "收录次数", "已收录数量"]
    data_list = [
        [
            r["id"],
            r["content"],
            r["kw_type"],
            r["handle_type"],
            r["seek_count"],
            r.get("collected_works", "未知"),
        ]
        for r in result
    ]

    title = "PIX关键词统计"
    if kw_type:
        type_map = {
            KwType.UID: "作者UID",
            KwType.PID: "作品PID",
            KwType.KEYWORD: "搜索关键词",
        }
        title = f"PIX {type_map.get(kw_type, '')} 统计"

    image = await ImageTemplate.table_page(title, None, column_name, data_list)
    await MessageUtils.build_message(image).send(reply_to=True)

    logger.info(
        f"PIX 查看关键词 type: {seek_type.result if seek_type.available else 'all'}",
        arparma.header_result,
        session=session,
    )
