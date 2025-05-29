import asyncio
from typing import Optional

from nonebot.adapters import Bot, Event
from nonebot.rule import Rule
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Option,
    Query,
    Reply,
    AlconnaMatch,
    Match,
    on_alconna,
    MultiVar,
)
from nonebot_plugin_alconna.uniseg import Receipt
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from ..models import PixivGallery, KwType
from ..config import base_config
from ..utils import InfoStorage
from ..core.search import SearchEngine
from ..core.download import DownloadManager
from ..services.blacklist import blacklist_service


def reply_check() -> Rule:
    """检查是否存在回复消息"""

    async def _rule(bot: Bot, event: Event):
        if event.get_type() == "message":
            return bool(await reply_fetch(event, bot))
        return False

    return Rule(_rule)


from arclet.alconna import CommandMeta

_search_matcher = on_alconna(
    Alconna(
        ".pix",
        Args["tags?", MultiVar(str)],
        Option("--uid", Args["uid_val", str], help_text="按用户ID搜索"),
        Option("--pid", Args["pid_val", str], help_text="按作品ID搜索"),
        Option("-n|--num", Args["num", int]),
        Option("-r|--r18", help_text="搜索R18图片"),
        Option("-s|--setu"),
        Option("-ai|--ai", help_text="包含AI生成图片"),
        Option("--bookmarks", Args["min_bookmarks", int]),
        Option("--date", Args["days", int]),
        Option("--mode", Args["mode", ["AND", "OR"]]),
        Option("--sort", Args["sort_by", ["bookmarks", "date", "random"]]),
        Option("--translate", help_text="启用标签翻译"),
        Option("--no-translate", help_text="禁用标签翻译"),
        meta=CommandMeta(
            fuzzy_match=True,
            strict=False,
        ),
    ),
    priority=5,
    block=True,
)


_original_matcher = on_alconna(
    Alconna(
        ["/"],
        "original",
        meta=CommandMeta(
            fuzzy_match=True,
            strict=False,
        ),
    ),
    priority=5,
    block=True,
    use_cmd_start=False,
    rule=reply_check(),
)

_info_matcher = on_alconna(
    Alconna(
        ["/"],
        "info",
        Args["img_idx?", int],
        meta=CommandMeta(
            fuzzy_match=True,
            strict=False,
        ),
    ),
    priority=5,
    block=True,
    use_cmd_start=False,
    rule=reply_check(),
)

_block_matcher = on_alconna(
    Alconna(
        ["/"],
        "block",
        Args["img_idx?", int],
        Args["reason?", str],
        meta=CommandMeta(
            fuzzy_match=True,
            strict=False,
        ),
    ),
    priority=5,
    block=True,
    use_cmd_start=False,
    rule=reply_check(),
)


async def download_and_format_image(pix: PixivGallery, show_info: bool = True):
    """下载并格式化图片信息

    Args:
        pix: 图片数据
        show_info: 是否显示详细信息

    Returns:
        Tuple[List, PixivGallery]: 消息内容列表和图片数据
    """
    try:
        image_path = await DownloadManager.download_image(pix)
        if not image_path:
            return [f"获取图片 pid: {pix.pid} 失败..."], pix
    except Exception as e:
        logger.error(f"下载图片 {pix.pid} 失败: {e}")
        return [f"获取图片 pid: {pix.pid} 失败: {str(e)[:50]}..."], pix

    message_list = []
    if show_info:
        tags_display = pix.tags.replace(",", ", ")
        message_list.append(
            f"title: {pix.title}\n"
            f"author: {pix.author}\n"
            f"pid: {pix.pid}-{pix.img_p}\n"
            f"uid: {pix.uid}\n"
            f"nsfw: {pix.nsfw_tag}\n"
            f"收藏数: {pix.total_bookmarks}"
        )

    message_list.append(image_path)
    return message_list, pix


@_search_matcher.handle()
async def handle_search(
    bot: Bot,
    session: Uninfo,
    arparma: Arparma,
    tags: Query[tuple[str, ...]] = Query("tags", ()),
    uid_val: Match[str] = AlconnaMatch("uid_val"),
    pid_val: Match[str] = AlconnaMatch("pid_val"),
    num: Query[int] = Query("num", 1),
    min_bookmarks: Match[int] = AlconnaMatch("bookmarks.min_bookmarks"),
    days: Match[int] = AlconnaMatch("date.days"),
    mode: Match[str] = AlconnaMatch("mode.mode"),
    sort_by: Match[str] = AlconnaMatch("sort.sort_by"),
    translate: Match[bool] = AlconnaMatch("translate.translate"),
    no_translate: Match[bool] = AlconnaMatch("no-translate.no_translate"),
):
    """
    搜索命令处理函数，实现可追踪的请求处理与结果验证

    Args:
        bot: 机器人实例
        session: 用户会话信息
        arparma: 命令解析结果
        tags: 搜索标签元组
        num: 返回结果数量
        min_bookmarks: 最低收藏数
        days: 最近天数限制
        mode: 标签匹配模式
        sort_by: 排序方式
        translate: 是否启用标签翻译
        no_translate: 是否禁用标签翻译
    """
    import time
    import random

    session_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
    logger.info(
        f"[会话ID:{session_id}] 处理搜索命令: 用户={session.user.id}, 群组={getattr(session, 'group_id', None)}"
    )
    logger.info(f"[会话ID:{session_id}] 搜索参数: 标签={tags.result}, 数量={num.result}")

    if uid_val.available:
        logger.info(f"[会话ID:{session_id}] UID参数: {uid_val.result}")

    if pid_val.available:
        logger.info(f"[会话ID:{session_id}] PID参数: {pid_val.result}")

    if num.result > 10:
        logger.info(f"[会话ID:{session_id}] 请求数量超限: {num.result} > 10")
        await MessageUtils.build_message("最多一次10张哦...").finish()

    allow_group_r18 = base_config.get("ALLOW_GROUP_R18")
    is_r18 = arparma.find("r18") or arparma.find("setu")

    logger.info(
        f"[会话ID:{session_id}] 权限检查: 群组存在={bool(session.group)}, R18参数={is_r18}, "
        f"群R18权限={allow_group_r18}, 超级用户={session.user.id in bot.config.superusers}"
    )

    if not allow_group_r18 and session.group and is_r18 and session.user.id not in bot.config.superusers:
        logger.warning(f"[会话ID:{session_id}] 权限检查未通过: 群聊中非超级用户请求R18内容")
        await MessageUtils.build_message("请私聊我查看R18内容～").finish()

    show_ai = arparma.find("ai")
    logger.debug(f"[会话ID:{session_id}] 是否显示AI参数: {show_ai}")

    tags_arr = tags.result or ()

    tag_translate = base_config.get("DEFAULT_TAG_TRANSLATE")
    tag_expand = base_config.get("DEFAULT_TAG_EXPAND")

    if translate.available:
        tag_translate = True
    elif no_translate.available:
        tag_translate = False

    tag_search_mode = base_config.get("DEFAULT_TAG_SEARCH_MODE")
    if mode.available:
        tag_search_mode = mode.result

    search_params = {
        "tags": list(tags_arr),
        "nsfw_tag": 2 if is_r18 else 0,
        "is_ai": None if show_ai else False,
        "translate_tags": tag_translate,
        "expand_tags": tag_expand,
        "tag_search_mode": tag_search_mode,
        "sort_by": sort_by.result if sort_by.available else "random",
        "sort_order": "desc",
        "limit": num.result,
        "offset": 0,
    }

    if uid_val.available:
        search_params["uid"] = uid_val.result
        logger.debug(f"[会话ID:{session_id}] UID参数: {uid_val.result}")

    if pid_val.available:
        search_params["pid"] = pid_val.result
        logger.debug(f"[会话ID:{session_id}] PID参数: {pid_val.result}")

    if min_bookmarks.available:
        search_params["bookmarks_range"] = (min_bookmarks.result, None)
        logger.debug(f"[会话ID:{session_id}] 最低收藏数: {min_bookmarks.result}")

    if days.available:
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days.result)
        search_params["date_range"] = (start_date, end_date)
        logger.debug(f"[会话ID:{session_id}] 日期范围: 最近{days.result}天")

    try:
        from ..models import SearchParams

        params_obj = SearchParams(**search_params)
        try:
            logger.debug(f"[会话ID:{session_id}] 构建的SearchParams: {params_obj.model_dump()}")
        except AttributeError:
            logger.debug(f"[会话ID:{session_id}] 构建的SearchParams: {params_obj.dict()}")
    except Exception as e:
        logger.error(f"[会话ID:{session_id}] 创建SearchParams失败: {e}")
        await MessageUtils.build_message(f"搜索参数错误: {e}").finish()
        return

    try:
        logger.info(f"[会话ID:{session_id}] 执行搜索: is_r18={is_r18}, show_ai={show_ai}")
        results, total_count = await SearchEngine.search(params_obj)

        if not is_r18:
            original_count = len(results)
            filtered_results = []

            for pix in results:
                if pix.nsfw_tag >= 2:
                    logger.error(
                        f"[会话ID:{session_id}] 过滤失败！发现R18图片: pid={pix.pid}, nsfw_tag={pix.nsfw_tag}, sanity_level={getattr(pix, 'sanity_level', 'N/A')}"
                    )
                    continue

                tags_lower = pix.tags.lower()
                r18_keywords = ["r18", "r-18", "r_18", "r 18", "18禁", "nsfw"]

                has_r18_tag = False
                for keyword in r18_keywords:
                    if keyword in tags_lower:
                        has_r18_tag = True
                        logger.error(
                            f"[会话ID:{session_id}] 过滤失败！标签含R18: pid={pix.pid}, tags={pix.tags}"
                        )
                        break

                if not has_r18_tag:
                    filtered_results.append(pix)

            if len(filtered_results) < original_count:
                logger.warning(
                    f"[会话ID:{session_id}] 安全检查额外过滤: 原{original_count}张 -> 现{len(filtered_results)}张"
                )

            results = filtered_results
    except Exception as e:
        logger.error(f"[会话ID:{session_id}] 搜索异常: {e}")
        await MessageUtils.build_message(f"搜索时发生错误: {e}").finish()

    if not results:
        logger.info(f"[会话ID:{session_id}] 未找到符合条件的图片")

        if pid_val.available and not tags_arr and not uid_val.available:
            msg_not_found = f"没有找到PID为 {pid_val.result} 的图片..."
        elif uid_val.available and not tags_arr and not pid_val.available:
            msg_not_found = f"没有找到UID为 {uid_val.result} 的作品..."
        elif tags_arr:
            msg_not_found = f"没有找到包含标签 '{', '.join(tags_arr)}' 的图片..."
        else:
            msg_not_found = "没有找到符合条件的图片..."

        await MessageUtils.build_message(msg_not_found).finish()

    logger.info(f"[会话ID:{session_id}] 找到符合条件图片: {len(results)}张")
    for i, pix in enumerate(results):
        logger.debug(
            f"[会话ID:{session_id}] 结果{i + 1}: pid={pix.pid}, nsfw={pix.nsfw_tag}, title={pix.title}"
        )

    tasks = []
    for pix in results:
        tasks.append(DownloadManager.format_image_info(pix, base_config.get("SHOW_INFO")))

    result_list = await asyncio.gather(*tasks)

    max_once_num2forward = base_config.get("MAX_ONCE_NUM2FORWARD")
    if max_once_num2forward and max_once_num2forward <= len(results) and session.group:
        try:
            logger.debug(f"[会话ID:{session_id}] 使用合并转发模式发送结果")

            receipt = await MessageUtils.alc_forward_msg(
                [r for r in result_list], bot.self_id, BotConfig.self_nickname
            ).send()

            if receipt and receipt.msg_ids:
                msg_id = receipt.msg_ids[0].get("message_id")
                if msg_id:
                    InfoStorage.add(str(msg_id), results)
                    logger.debug(
                        f"[会话ID:{session_id}] Stored List[PixivGallery] for forwarded message_id: {msg_id}"
                    )
            else:
                logger.error(f"[会话ID:{session_id}] 发送合并转发消息后未能获取有效的message_id")

        except Exception as e:
            logger.error(f"[会话ID:{session_id}] 发送合并转发消息失败: {e}")
            for i, msg_content in enumerate(result_list):
                pix = results[i]
                try:
                    receipt_single = await MessageUtils.build_message(msg_content).send()
                    if receipt_single and receipt_single.msg_ids:
                        single_msg_id = receipt_single.msg_ids[0].get("message_id")
                        if single_msg_id:
                            InfoStorage.add(str(single_msg_id), pix)
                    else:
                        logger.error(
                            f"[会话ID:{session_id}] 发送单张图片 {pix.pid} 后未能获取有效的message_id (fallback)"
                        )
                except Exception as send_err:
                    logger.error(f"[会话ID:{session_id}] 发送图片 {pix.pid} 失败 (fallback): {send_err}")
    else:
        logger.debug(f"[会话ID:{session_id}] 使用单条消息模式发送结果")
        for i, msg_content in enumerate(result_list):
            pix = results[i]
            try:
                receipt_single = await MessageUtils.build_message(msg_content).send()
                if receipt_single and receipt_single.msg_ids:
                    single_msg_id = receipt_single.msg_ids[0].get("message_id")
                    if single_msg_id:
                        InfoStorage.add(str(single_msg_id), pix)
                else:
                    logger.error(f"[会话ID:{session_id}] 发送单张图片 {pix.pid} 后未能获取有效的message_id")
            except Exception as e:
                logger.error(f"[会话ID:{session_id}] 发送图片 {pix.pid} 失败: {e}")
                continue

    logger.info(f"[会话ID:{session_id}] 搜索命令处理完成")


@_original_matcher.handle()
async def _(bot: Bot, event: Event):
    """获取原图命令处理"""
    reply: Reply | None = await reply_fetch(event, bot)
    if reply and (pix_model := InfoStorage.get(str(reply.id))):
        if isinstance(pix_model, list):
            await MessageUtils.build_message(
                "请对单张图片使用 /original 命令，或使用 /info [序号] 后再获取原图。"
            ).finish(reply_to=True)
            return

        image_path = await DownloadManager.download_image(pix_model, True)
        if not image_path:
            await MessageUtils.build_message("下载原图失败...").finish()
            return

        receipt: Receipt = await MessageUtils.build_message(image_path).send(reply_to=True)
        if receipt and receipt.msg_ids:
            msg_id = receipt.msg_ids[0].get("message_id")
            if msg_id:
                InfoStorage.add(str(msg_id), pix_model)
    else:
        await MessageUtils.build_message("没有找到该图片相关信息或数据已过期...").finish(reply_to=True)


@_info_matcher.handle()
async def _(bot: Bot, event: Event, idx_query: Query[int] = Query("img_idx")):
    """图片信息命令处理，支持查看合并转发消息中的第N张图片信息"""
    reply: Reply | None = await reply_fetch(event, bot)
    if not reply:
        await MessageUtils.build_message("请回复一条消息以查看其信息。").finish()
        return

    stored_data = InfoStorage.get(str(reply.id))

    if stored_data is None:
        await MessageUtils.build_message("没有找到该图片相关信息或数据已过期...").finish(reply_to=True)
        return

    pix_model_to_show: Optional[PixivGallery] = None

    if idx_query.available:
        index_from_user = idx_query.result
        if isinstance(stored_data, list):
            if not stored_data or not all(isinstance(item, PixivGallery) for item in stored_data):
                await MessageUtils.build_message("回复的消息中存储的数据格式不正确，无法按序号查看。").finish(
                    reply_to=True
                )
                return

            if 0 < index_from_user <= len(stored_data):
                pix_model_to_show = stored_data[index_from_user - 1]
            else:
                await MessageUtils.build_message(
                    f"合并消息中没有第 {index_from_user} 张图片。总共有 {len(stored_data)} 张。"
                ).finish(reply_to=True)
                return
        else:
            await MessageUtils.build_message(
                "您回复的不是一条包含多张图片的合并消息，无法按序号查看。"
            ).finish(reply_to=True)
            return
    else:
        if isinstance(stored_data, list):
            if not stored_data or not all(isinstance(item, PixivGallery) for item in stored_data):
                await MessageUtils.build_message("回复的消息中存储的数据格式不正确。").finish(reply_to=True)
                return
            await MessageUtils.build_message(
                f"这条合并消息包含 {len(stored_data)} 张图片，请使用 /info [序号] 查看特定图片信息。"
            ).finish(reply_to=True)
            return
        elif isinstance(stored_data, PixivGallery):
            pix_model_to_show = stored_data
        else:
            await MessageUtils.build_message("存储的信息格式不正确，无法解析。").finish(reply_to=True)
            return

    if pix_model_to_show:
        tags_display = pix_model_to_show.tags.replace(",", ", ")
        result_text = (
            f"title: {pix_model_to_show.title}\n"
            f"author: {pix_model_to_show.author}\n"
            f"nsfw: {pix_model_to_show.nsfw_tag}\n"
            f"是否AI: {'是' if pix_model_to_show.is_ai else '否'}\n"
            f"收藏数: {pix_model_to_show.total_bookmarks}\n"
            f"tags: {tags_display}\n\n"
            f"UID链接: https://www.pixiv.net/users/{pix_model_to_show.uid}\n"
            f"Pixiv链接: https://www.pixiv.net/artworks/{pix_model_to_show.pid}"
        )
        await MessageUtils.build_message(result_text).finish(reply_to=True)


@_block_matcher.handle()
async def _(
    bot: Bot,
    event: Event,
    idx_query: Query[int] = Query("img_idx"),
    reason_query: Query[str] = Query("reason"),
):
    """图片黑名单命令处理，支持将合并转发消息中的第N张图片加入黑名单"""
    reply: Reply | None = await reply_fetch(event, bot)
    if not reply:
        await MessageUtils.build_message("请回复一条消息以将其加入黑名单。").finish()
        return

    stored_data = InfoStorage.get(str(reply.id))

    if stored_data is None:
        await MessageUtils.build_message("没有找到该图片相关信息或数据已过期...").finish(reply_to=True)
        return

    pix_model_to_block: Optional[PixivGallery] = None

    if idx_query.available:
        index_from_user = idx_query.result
        if isinstance(stored_data, list):
            if not stored_data or not all(isinstance(item, PixivGallery) for item in stored_data):
                await MessageUtils.build_message(
                    "回复的消息中存储的数据格式不正确，无法按序号加入黑名单。"
                ).finish(reply_to=True)
                return

            if 0 < index_from_user <= len(stored_data):
                pix_model_to_block = stored_data[index_from_user - 1]
            else:
                await MessageUtils.build_message(
                    f"合并消息中没有第 {index_from_user} 张图片。总共有 {len(stored_data)} 张。"
                ).finish(reply_to=True)
                return
        else:
            await MessageUtils.build_message(
                "您回复的不是一条包含多张图片的合并消息，无法按序号加入黑名单。"
            ).finish(reply_to=True)
            return
    else:
        if isinstance(stored_data, list):
            if not stored_data or not all(isinstance(item, PixivGallery) for item in stored_data):
                await MessageUtils.build_message("回复的消息中存储的数据格式不正确。").finish(reply_to=True)
                return
            await MessageUtils.build_message(
                f"这条合并消息包含 {len(stored_data)} 张图片，请使用 /block [序号] 将特定图片加入黑名单。"
            ).finish(reply_to=True)
            return
        elif isinstance(stored_data, PixivGallery):
            pix_model_to_block = stored_data
        else:
            await MessageUtils.build_message("存储的信息格式不正确，无法解析。").finish(reply_to=True)
            return

    if pix_model_to_block:
        reason = reason_query.result if reason_query.available else "用户手动加入黑名单"

        user_id = str(event.get_user_id())

        result = await blacklist_service.add_blacklist(
            user_id=user_id, content=pix_model_to_block.pid, bl_type=KwType.PID, reason=reason
        )

        tags_display = pix_model_to_block.tags.replace(",", ", ")
        result_text = (
            f"{result}\n\n"
            f"已加入黑名单的图片信息:\n"
            f"title: {pix_model_to_block.title}\n"
            f"author: {pix_model_to_block.author}\n"
            f"pid: {pix_model_to_block.pid}-{pix_model_to_block.img_p}\n"
            f"uid: {pix_model_to_block.uid}\n"
            f"收藏数: {pix_model_to_block.total_bookmarks}\n"
            f"tags: {tags_display[:100]}{'...' if len(tags_display) > 100 else ''}"
        )

        await MessageUtils.build_message(result_text).finish(reply_to=True)
