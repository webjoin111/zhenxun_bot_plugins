from nonebot_plugin_alconna import (
    Alconna,
    Args,
    At,
    Option,
    Subcommand,
    Field,
    on_alconna,
    store_true,
    Match,
    Arparma,
    AlconnaMatch,
    Query,
    AlconnaQuery,
)
from nonebot.params import Arg, Depends
from nonebot.typing import T_State
from datetime import datetime
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11 import Bot
from nonebot.permission import SUPERUSER
import re
from typing import Tuple

from zhenxun.utils.rules import ensure_group, admin_check
from zhenxun.services import scheduler_manager
from zhenxun.services.log import logger

from .handlers.cloud_handler import CloudHandler
from .services import DataService, TextProcessor, TimeService
from .generators import ImageWordCloudGenerator
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from nonebot import get_bots
import asyncio

cloud_handler = CloudHandler()


def _parse_time(time_str: str) -> Tuple[int, int]:
    """解析 HH:MM 或 HHMM 格式的时间"""
    match = re.match(r"^(?:([01]\d|2[0-3]):?([0-5]\d))$", time_str)
    if match:
        if ":" in time_str:
            hour, minute = map(int, time_str.split(":"))
        else:
            hour = int(time_str[:2])
            minute = int(time_str[2:])
        return hour, minute
    raise ValueError("时间格式不正确，请使用 HH:MM 或 HHMM 格式")


async def _generate_and_send_wordcloud(group_id: str):
    """生成并发送词云的实际执行函数"""
    try:
        logger.debug(f"开始为群 {group_id} 生成定时词云...")
        time_service = TimeService()
        start, stop = time_service.get_time_range("今日")
        start_tz = time_service.convert_to_timezone(start, "Asia/Shanghai")
        stop_tz = time_service.convert_to_timezone(stop, "Asia/Shanghai")

        try:
            message_data = await asyncio.wait_for(
                DataService.get_messages(
                    user_id=None,
                    group_id=int(group_id),
                    time_range=(start_tz, stop_tz),
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"获取群 {group_id} 的消息数据超时")
            return
        except Exception as e:
            logger.error(f"获取群 {group_id} 的消息数据失败", e=e)
            return

        if message_data and message_data.messages:
            try:
                text_processor = TextProcessor()
                word_frequencies = await text_processor.extract_keywords(
                    message_data.messages
                )

                if word_frequencies:
                    try:
                        generator = ImageWordCloudGenerator()
                        image_bytes = await asyncio.wait_for(
                            generator.generate(word_frequencies), timeout=60.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"生成群 {group_id} 的词云图片超时")
                        msg_to_send = MessageUtils.build_message(
                            "生成词云图片超时，请稍后再试。"
                        )
                        return

                    if image_bytes:
                        msg_to_send = MessageUtils.build_message(
                            ["今日词云：", image_bytes]
                        )
                    else:
                        msg_to_send = MessageUtils.build_message(
                            "生成今日词云图片失败。"
                        )
                else:
                    msg_to_send = MessageUtils.build_message(
                        "今天没有足够的数据生成词云。"
                    )
            except Exception as e:
                logger.error(f"处理群 {group_id} 的词云数据失败", e=e)
                msg_to_send = MessageUtils.build_message(
                    "生成词云时发生错误，请查看日志。"
                )
        else:
            msg_to_send = MessageUtils.build_message("今天没有足够的数据生成词云。")

        if msg_to_send:
            max_retries = 3
            retry_delay = 1

            for attempt in range(max_retries):
                try:
                    bots = get_bots()
                    if not bots:
                        logger.error("无法获取任何Bot实例")
                        return

                    bot = next(iter(bots.values()), None)
                    if not bot:
                        logger.error("无法获取有效的Bot实例")
                        return

                    target = PlatformUtils.get_target(group_id=group_id)
                    if not target:
                        logger.error(f"无法为群 {group_id} 创建发送目标")
                        return

                    await msg_to_send.send(target=target, bot=bot)
                    logger.info(f"成功发送定时词云到群 {group_id}")
                    break
                except Exception as e:
                    logger.warning(
                        f"发送定时词云到群 {group_id} 失败 (尝试 {attempt + 1}/{max_retries}): {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        logger.error(f"发送定时词云到群 {group_id} 最终失败")

    except Exception as e:
        logger.error(f"生成定时词云时发生未预期的错误: {e}", e=e)


async def scheduled_wordcloud_job(group_id: str | None, **kwargs):
    """
    一个包装函数，作为暴露给 scheduler_manager 的任务入口。
    kwargs 在这里是空的，但保留以符合签名。
    """
    if not group_id:
        logger.warning("定时词云任务执行失败：group_id 为空。")
        return

    await _generate_and_send_wordcloud(group_id)


scheduler_manager.register("word_clouds")(scheduled_wordcloud_job)

_matcher = on_alconna(
    Alconna(
        "wordcloud",
        Args[
            "date?",
            ["今日", "昨日", "本周", "上周", "本月", "上月", "本季", "年度", "历史"],
        ]["at_user?", At],
        Option("-m|--my", action=store_true, help_text="个人词云"),
        Option("-d|--a_date", Args["z_date", str]),
        Option(
            "-g|--group",
            Args["target_group_id", int, Field(completion="指定群号 (SUPERUSER)")],
            help_text="指定群聊 (仅超级用户)",
        ),
    ),
    priority=5,
    block=True,
    rule=ensure_group,
)


_matcher.shortcut(
    r"^我的(?P<date>今日|昨日|本周|上周|本月|上月|本季|年度)词云$",
    command="wordcloud",
    arguments=["{date}", "--my"],
    prefix=True,
)

_matcher.shortcut(
    r"^我的(?P<date>今日|昨日|本周|上周|本月|上月|本季|年度)词云\s+-g\s+(?P<group_id>\d+)$",
    command="wordcloud",
    arguments=["{date}", "--my", "-g", "{group_id}"],
    prefix=True,
)

_matcher.shortcut(
    r"^我的词云(?:\s+-g\s+(?P<group_id>\d+))?$",
    command="wordcloud",
    arguments=lambda match: ["今日", "--my"]
    + (["-g", match.group("group_id")] if match.group("group_id") else []),
    prefix=True,
)


_matcher.shortcut(
    r"历史词云\S?(?P<date>.*?)(?:\s+-g\s+(?P<group_id>\d+))?$",
    command="wordcloud",
    arguments=lambda match: ["--a_date", match.group("date").strip()]
    + (["-g", match.group("group_id")] if match.group("group_id") else []),
    prefix=True,
)

_matcher.shortcut(
    r"(?P<date>今日|昨日|本周|上周|本月|上月|本季|年度)词云$",
    command="wordcloud",
    arguments=["{date}"],
    prefix=True,
)

_matcher.shortcut(
    r"(?P<date>今日|昨日|本周|上周|本月|上月|本季|年度)词云\s+-g\s+(?P<group_id>\d+)",
    command="wordcloud",
    arguments=["{date}", "-g", "{group_id}"],
    prefix=True,
)


@_matcher.handle()
async def handle_first_receive(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    date: Match[str],
    arparma: Arparma,
    z_date: Match[str],
    target_group: Query[int] = AlconnaQuery("group.target_group_id"),
):
    if target_group.available:
        is_superuser = await SUPERUSER(bot, event)
        if not is_superuser:
            await _matcher.finish("需要超级用户权限才能查看指定群组的词云。")
            return
        state["target_group_id"] = target_group.result

    await cloud_handler.handle_first_receive(state, date, arparma, z_date)


@_matcher.got(
    "start",
    prompt="请输入你要查询的起始日期（如 2022-01-01）",
    parameterless=[Depends(cloud_handler.parse_datetime("start"))],
)
@_matcher.got(
    "stop",
    prompt="请输入你要查询的结束日期（如 2022-02-22）",
    parameterless=[Depends(cloud_handler.parse_datetime("stop"))],
)
async def handle_message(
    event: GroupMessageEvent,
    state: T_State,
    start: datetime = Arg(),
    stop: datetime = Arg(),
    my: bool = Arg(),
):
    target_group_id = state.get("target_group_id")

    await cloud_handler.handle_message(event, state, start, stop, my, target_group_id)


schedule_alconna = Alconna(
    "定时词云",
    Subcommand(
        "开启",
        Args["time_str", str, Field(completion="输入时间 (HH:MM 或 HHMM)")],
        Option(
            "-g",
            Args["target_group_id", int, Field(completion="指定群号 (SUPERUSER)")],
            help_text="指定群聊",
        ),
        Option("-all", help_text="为所有群聊独立开启 (SUPERUSER)"),
    ),
    Subcommand(
        "关闭",
        Option(
            "-g",
            Args["target_group_id", int, Field(completion="指定群号 (SUPERUSER)")],
            help_text="指定群聊",
        ),
        Option("-all", help_text="关闭所有独立任务 (SUPERUSER)"),
    ),
    Subcommand(
        "暂停",
        Option("-g", Args["target_group_id", int]),
        Option("-all", help_text="暂停所有独立任务 (SUPERUSER)"),
    ),
    Subcommand(
        "恢复",
        Option("-g", Args["target_group_id", int]),
        Option("-all", help_text="恢复所有独立任务 (SUPERUSER)"),
    ),
    Subcommand(
        "状态",
        Option(
            "-g",
            Args["target_group_id", int, Field(completion="指定群号 (SUPERUSER)")],
            help_text="指定群聊",
        ),
        Option("-all", help_text="查看所有独立任务 (SUPERUSER)"),
    ),
)

schedule_matcher = on_alconna(schedule_alconna, priority=4, block=True)


@schedule_matcher.assign("开启")
async def handle_schedule_on(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    time_str: Match[str] = AlconnaMatch("time_str"),
    target_group: Query[int] = AlconnaQuery("开启.g.target_group_id"),
    all_groups: Query[bool] = AlconnaQuery("开启.all.value", default=False),
):
    if not time_str.available:
        await schedule_matcher.finish("请提供定时时间 (HH:MM 或 HHMM 格式)。")
    try:
        hour, minute = _parse_time(time_str.result)
    except ValueError as e:
        await schedule_matcher.finish(str(e))
        return

    plugin_name_to_schedule = "word_clouds"
    is_superuser = await SUPERUSER(bot, event)
    is_admin = await admin_check("word_clouds", None)(bot=bot, event=event, state=state)

    if all_groups.result:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能对所有群组操作。")

        await schedule_matcher.send("正在为所有群组批量创建定时词云任务，请稍候...")
        groups, _ = await PlatformUtils.get_group_list(bot, True)
        if not groups:
            await schedule_matcher.finish("未能获取到任何群组列表。")

        tasks = []
        for group in groups:
            tasks.append(
                scheduler_manager.add_daily_task(
                    plugin_name=plugin_name_to_schedule,
                    group_id=group.group_id,
                    hour=hour,
                    minute=minute,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(
            1 for r in results if r is not None and not isinstance(r, Exception)
        )
        fail_count = len(results) - success_count

        msg = f"批量创建任务完成！\n成功: {success_count}个\n失败: {fail_count}个"
        await schedule_matcher.finish(msg)
        return

    current_group_id = str(event.group_id)
    if target_group.available:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能指定群组。")
            return
        target_gid = str(target_group.result)
    else:
        if not is_admin and not is_superuser:
            await schedule_matcher.finish("需要管理员权限才能设置当前群的定时词云。")
            return
        target_gid = current_group_id

    schedule = await scheduler_manager.add_daily_task(
        plugin_name=plugin_name_to_schedule,
        group_id=target_gid,
        hour=hour,
        minute=minute,
    )
    if schedule:
        message = (
            f"已为群 {target_gid} 设置定时词云，时间为每天 {hour:02d}:{minute:02d}。"
        )
    else:
        message = "设置定时词云失败。"
    await schedule_matcher.finish(message)


@schedule_matcher.assign("关闭")
async def handle_schedule_off(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    target_group: Query[int] = AlconnaQuery("关闭.g.target_group_id"),
    all_groups: Query[bool] = AlconnaQuery("关闭.all.value", default=False),
):
    plugin_name_to_schedule = "word_clouds"
    is_superuser = await SUPERUSER(bot, event)
    is_admin = await admin_check("word_clouds", None)(bot=bot, event=event, state=state)

    if all_groups.result:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能对所有群组操作。")

        targeter = scheduler_manager.target(plugin_name=plugin_name_to_schedule)
        count, _ = await targeter.remove()

        if count > 0:
            await schedule_matcher.finish(f"已成功取消 {count} 个独立的定时词云任务。")
        else:
            await schedule_matcher.finish("没有找到任何独立的定时词云任务来取消。")
        return

    current_group_id = str(event.group_id)
    if target_group.available:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能指定群组。")
        target_gid = str(target_group.result)
    else:
        if not is_admin and not is_superuser:
            await schedule_matcher.finish("需要管理员权限才能取消当前群的定时词云。")
        target_gid = current_group_id

    targeter = scheduler_manager.target(
        plugin_name=plugin_name_to_schedule, group_id=target_gid
    )
    count, _ = await targeter.remove()
    if count > 0:
        await schedule_matcher.finish(
            f"已成功取消群 {target_gid} 的 {count} 个定时词云任务。"
        )
    else:
        await schedule_matcher.finish(
            f"群 {target_gid} 没有找到匹配的定时词云任务来取消。"
        )


@schedule_matcher.assign("暂停")
async def handle_schedule_pause(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    target_group: Query[int] = AlconnaQuery("暂停.g.target_group_id"),
    all_groups: Query[bool] = AlconnaQuery("暂停.all.value", default=False),
):
    plugin_name_to_schedule = "word_clouds"
    is_superuser = await SUPERUSER(bot, event)
    is_admin = await admin_check("word_clouds", None)(bot=bot, event=event, state=state)

    if all_groups.result:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能操作所有任务。")
        targeter = scheduler_manager.target(plugin_name=plugin_name_to_schedule)
        count, _ = await targeter.pause()
        await schedule_matcher.finish(f"已成功暂停 {count} 个独立的定时词云任务。")
        return

    current_group_id = str(event.group_id)
    if target_group.available:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能指定群组。")
        target_gid = str(target_group.result)
    else:
        if not is_admin and not is_superuser:
            await schedule_matcher.finish("需要管理员权限。")
        target_gid = current_group_id

    targeter = scheduler_manager.target(
        plugin_name=plugin_name_to_schedule, group_id=target_gid
    )
    count, _ = await targeter.pause()
    await schedule_matcher.finish(
        f"已成功暂停群 {target_gid} 的 {count} 个定时词云任务。"
    )


@schedule_matcher.assign("恢复")
async def handle_schedule_resume(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    target_group: Query[int] = AlconnaQuery("恢复.g.target_group_id"),
    all_groups: Query[bool] = AlconnaQuery("恢复.all.value", default=False),
):
    plugin_name_to_schedule = "word_clouds"
    is_superuser = await SUPERUSER(bot, event)
    is_admin = await admin_check("word_clouds", None)(bot=bot, event=event, state=state)

    if all_groups.result:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能操作所有任务。")
        targeter = scheduler_manager.target(plugin_name=plugin_name_to_schedule)
        count, _ = await targeter.resume()
        await schedule_matcher.finish(f"已成功恢复 {count} 个独立的定时词云任务。")
        return

    current_group_id = str(event.group_id)
    if target_group.available:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能指定群组。")
        target_gid = str(target_group.result)
    else:
        if not is_admin and not is_superuser:
            await schedule_matcher.finish("需要管理员权限。")
        target_gid = current_group_id

    targeter = scheduler_manager.target(
        plugin_name=plugin_name_to_schedule, group_id=target_gid
    )
    count, _ = await targeter.resume()
    await schedule_matcher.finish(
        f"已成功恢复群 {target_gid} 的 {count} 个定时词云任务。"
    )


@schedule_matcher.assign("状态")
async def handle_schedule_status(
    bot: Bot,
    event: GroupMessageEvent,
    state: T_State,
    target_group: Query[int] = AlconnaQuery("状态.g.target_group_id"),
    all_groups: Query[bool] = AlconnaQuery("状态.all.value", default=False),
):
    current_group_id = str(event.group_id)
    is_superuser = await SUPERUSER(bot, event)
    is_admin = await admin_check("word_clouds", None)(bot=bot, event=event, state=state)

    plugin_name_to_schedule = "word_clouds"

    if all_groups.result:
        if not is_superuser:
            await schedule_matcher.finish("需要超级用户权限才能查看所有群组的状态。")
            return

        schedules = await scheduler_manager.get_all_schedules(plugin_name_to_schedule)
        if not schedules:
            await schedule_matcher.finish("当前没有任何群组设置了定时词云。")
            return

        status_lines = [f"插件 '{plugin_name_to_schedule}' 的定时状态："]
        for s in schedules:
            target = (
                s.group_id if s.group_id != scheduler_manager.ALL_GROUPS else "所有群组"
            )
            time_cfg = s.trigger_config
            status_lines.append(
                f"目标 {target}: 每天 {time_cfg.get('hour')}:{time_cfg.get('minute')}"
            )
        await schedule_matcher.finish("\n".join(status_lines))
        return

    gid_to_check = (
        str(target_group.result) if target_group.available else current_group_id
    )

    if target_group.available and not is_superuser:
        await schedule_matcher.finish("需要超级用户权限才能查看指定群组的状态。")
        return

    schedule = await scheduler_manager.get_all_schedules(
        plugin_name=plugin_name_to_schedule,
        group_id=gid_to_check,
    )

    if schedule:
        time_cfg = schedule[0].trigger_config
        await schedule_matcher.finish(
            f"群 {gid_to_check} 的定时词云已开启，时间为每天 {time_cfg.get('hour')}:{time_cfg.get('minute')}。"
        )
    else:
        await schedule_matcher.finish(f"群 {gid_to_check} 未设置定时词云。")


global_schedule_matcher = on_alconna(
    Alconna(
        "全局定时词云",
        Subcommand(
            "开启",
            Args["time_str", str, Field(completion="输入时间 (HH:MM 或 HHMM)")],
        ),
        Subcommand("关闭"),
        Subcommand("状态"),
    ),
    priority=4,
    block=True,
    permission=SUPERUSER,
)


@global_schedule_matcher.assign("开启")
async def handle_global_schedule_on(
    bot: Bot,
    time_str: Match[str] = AlconnaMatch("time_str"),
):
    if not time_str.available:
        await global_schedule_matcher.finish("请提供定时时间 (HH:MM 或 HHMM 格式)。")
    try:
        hour, minute = _parse_time(time_str.result)
    except ValueError as e:
        await global_schedule_matcher.finish(str(e))
        return

    plugin_name_to_schedule = "word_clouds"
    target_gid = scheduler_manager.ALL_GROUPS

    schedule = await scheduler_manager.add_daily_task(
        plugin_name=plugin_name_to_schedule,
        group_id=target_gid,
        hour=hour,
        minute=minute,
        bot_id=bot.self_id,
    )
    if schedule:
        message = f"已为所有群设置全局定时词云，时间为每天 {hour:02d}:{minute:02d}。"
    else:
        message = "设置全局定时词云失败。"
    await global_schedule_matcher.finish(message)


@global_schedule_matcher.assign("关闭")
async def handle_global_schedule_off(bot: Bot):
    plugin_name_to_schedule = "word_clouds"
    target_gid = scheduler_manager.ALL_GROUPS
    targeter = scheduler_manager.target(
        plugin_name=plugin_name_to_schedule, group_id=target_gid, bot_id=bot.self_id
    )
    count, _ = await targeter.remove()
    if count > 0:
        await global_schedule_matcher.finish(f"已成功取消 {count} 个全局定时词云任务。")
    else:
        await global_schedule_matcher.finish("没有找到匹配的全局定时词云任务来取消。")


@global_schedule_matcher.assign("状态")
async def handle_global_schedule_status(bot: Bot):
    plugin_name_to_schedule = "word_clouds"
    target_gid = scheduler_manager.ALL_GROUPS
    schedule = await scheduler_manager.get_all_schedules(
        plugin_name=plugin_name_to_schedule, group_id=target_gid, bot_id=bot.self_id
    )
    if schedule:
        time_cfg = schedule[0].trigger_config
        await global_schedule_matcher.finish(
            f"全局定时词云已开启，时间为每天 {time_cfg.get('hour')}:{time_cfg.get('minute')}。"
        )
    else:
        await global_schedule_matcher.finish("未设置全局定时词云。")
