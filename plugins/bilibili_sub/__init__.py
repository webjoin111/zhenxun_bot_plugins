import asyncio
import time
import nonebot
from datetime import datetime
from nonebot.adapters.onebot.v11 import Bot
from nonebot.drivers import Driver
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_apscheduler import scheduler
from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from .config import base_config, load_credential_from_file
from .data_source import (
    BilibiliSub,
    SubManager,
    get_sub_status,
)
from . import commands  # 导入命令模块

__plugin_meta__ = PluginMetadata(
    name="B站订阅",
    description="非常便利的B站订阅通知",
    usage="""
        usage：
            B站直播，番剧，UP动态开播等提醒
            主播订阅相当于 直播间订阅 + UP订阅
            指令：
                添加订阅 ['主播'/'UP'/'番剧'] [id/链接/番名]
                删除订阅 ['主播'/'UP'/'id'] [id]
                查看订阅
            示例：
                添加订阅主播 2345344 <-(直播房间id)
                添加订阅UP 2355543 <-(个人主页id)
                添加订阅番剧 史莱姆 <-(支持模糊搜索)
                添加订阅番剧 125344 <-(番剧id)
                删除订阅id 2324344 <-(任意id，通过查看订阅获取)
        """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.5",
        superuser_help="""
    登录b站获取cookie防止风控：
            bil_check/检测b站
            bil_login/登录b站
            bil_logout/退出b站 uid
            示例:
                登录b站
                检测b站
                bil_logout 12345<-(退出登录的b站uid，通过检测b站获取)
        """,
        configs=[
            RegisterConfig(
                module="bilibili_sub",
                key="LIVE_MSG_AT_ALL",
                value=False,
                help="直播提醒是否AT全体（仅在真寻是管理员时生效）",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="UP_MSG_AT_ALL",
                value=False,
                help="UP动态投稿提醒是否AT全体（仅在真寻是管理员时生效）",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="CHECK_TIME",
                value=60,
                help="b站检测时间间隔(秒)",
                default_value=60,
                type=int,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="ENABLE_SLEEP_MODE",
                value=True,
                help="是否开启固定时间段内休眠",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="SLEEP_START_TIME",
                value="01:00",
                help="开启休眠时间",
                default_value="01:00",
                type=str,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="SLEEP_END_TIME",
                value="07:30",
                help="关闭休眠时间",
                default_value="07:30",
                type=str,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="ENABLE_AD_FILTER",
                value=True,
                help="是否开启广告过滤",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="AD_FILTER_METHOD",
                value="hybrid",
                help="广告过滤方法: api(仅API检测), page(仅页面检测), hybrid(混合方案)",
                default_value="hybrid",
                type=str,
            ),
            RegisterConfig(
                module="bilibili_sub",
                key="BATCH_SIZE",
                value=5,
                help="每次检查的订阅批次大小",
                default_value=5,
                type=int,
            ),
            RegisterConfig(
                module="BiliBili",
                key="COOKIES",
                value="",
                default_value="",
                help="B站cookies数据，由系统自动管理，请勿手动修改",
            ),
        ],
        admin_level=base_config.get("GROUP_BILIBILI_SUB_LEVEL"),
    ).to_dict(),
)

Config.add_plugin_config(
    "bilibili_sub",
    "GROUP_BILIBILI_SUB_LEVEL",
    5,
    help="群内bilibili订阅需要管理的权限",
    default_value=5,
    type=int,
)



driver: Driver = nonebot.get_driver()

sub_manager: SubManager | None = None


@driver.on_startup
async def _():
    global sub_manager
    sub_manager = SubManager()
    # 加载 B站 凭证
    await load_credential_from_file()





def should_run():
    """判断当前时间是否在运行时间段内（7点30到次日1点）"""
    now = datetime.now().time()
    # 如果当前时间在 7:30 到 23:59:59 之间，或者 0:00 到 1:00 之间，则运行
    return (
        now >= datetime.strptime(base_config.get("SLEEP_END_TIME"), "%H:%M").time()
    ) or (now < datetime.strptime(base_config.get("SLEEP_START_TIME"), "%H:%M").time())


# 信号量，限制并发任务数
semaphore = asyncio.Semaphore(200)


# 推送
@scheduler.scheduled_job(
    "interval",
    seconds=base_config.get("CHECK_TIME") if base_config.get("CHECK_TIME") else 30,
    max_instances=500,
    misfire_grace_time=40,
)
async def check_subscriptions():
    """
    定时任务：检查订阅并发送消息
    使用分批检查功能，每次检查一批订阅
    """
    start_time = time.time()
    logger.debug(
        f"B站订阅检查任务开始执行 - 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    async with semaphore:  # 限制并发任务数
        if base_config.get("ENABLE_SLEEP_MODE"):
            if not should_run():
                logger.debug(
                    f"B站订阅检查任务处于休眠时间段，跳过执行 - 当前时间: {datetime.now().strftime('%H:%M:%S')}"
                )
                return
            else:
                logger.debug(
                    f"B站订阅检查任务处于活动时间段 - 当前时间: {datetime.now().strftime('%H:%M:%S')}"
                )

        bots = nonebot.get_bots()
        if not bots:
            logger.warning("B站订阅检查任务未找到可用的机器人实例")
            return

        logger.debug(f"B站订阅检查任务找到 {len(bots)} 个机器人实例")

        # 选择一个机器人实例
        bot_id, bot_instance = next(iter(bots.items()))
        if not bot_instance:
            logger.warning("B站订阅检查任务未找到有效的机器人实例")
            return

        logger.debug(f"B站订阅检查任务使用机器人: {bot_id}")

        try:
            # 获取下一批次的订阅数据
            logger.debug("B站订阅检查任务正在获取批次订阅数据...")
            batch_subs = await sub_manager.get_next_batch()

            if not batch_subs:
                logger.info("B站订阅检查任务未找到可用的订阅数据")
                return

            logger.info(f"B站订阅检查任务获取到批次数据: 订阅数量={len(batch_subs)}")

            # 检查批次中的每个订阅
            for sub in batch_subs:
                try:
                    logger.info(
                        f"B站订阅检查任务开始检测: ID={sub.sub_id}, 类型={sub.sub_type}, 名称={getattr(sub, 'uname', '') or getattr(sub, 'season_name', '未知')}"
                    )

                    # 获取订阅状态，设置超时时间为30秒
                    logger.debug(
                        f"B站订阅检查任务正在获取订阅状态: ID={sub.sub_id}, 类型={sub.sub_type}"
                    )
                    check_start_time = time.time()
                    msg_list = await asyncio.wait_for(
                        get_sub_status(sub.sub_id, sub.sub_type), timeout=30
                    )
                    check_duration = time.time() - check_start_time
                    logger.debug(
                        f"B站订阅检查任务获取订阅状态完成: ID={sub.sub_id}, 耗时={check_duration:.2f}秒"
                    )

                    if msg_list:
                        logger.info(
                            f"B站订阅检查任务检测到更新: ID={sub.sub_id}, 类型={sub.sub_type}, 消息长度={len(msg_list)}"
                        )
                        await send_sub_msg(msg_list, sub, bot_instance)
                    else:
                        logger.debug(
                            f"B站订阅检查任务未检测到更新: ID={sub.sub_id}, 类型={sub.sub_type}"
                        )

                    # 如果是直播订阅，额外检测UP主动态
                    if sub.sub_type == "live":
                        logger.debug(
                            f"B站订阅检查任务正在额外检测直播UP主动态: ID={sub.sub_id}"
                        )
                        up_check_start_time = time.time()
                        msg_list_up = await asyncio.wait_for(
                            get_sub_status(sub.sub_id, "up"), timeout=30
                        )
                        up_check_duration = time.time() - up_check_start_time
                        logger.debug(
                            f"B站订阅检查任务额外检测直播UP主动态完成: ID={sub.sub_id}, 耗时={up_check_duration:.2f}秒"
                        )

                        if msg_list_up:
                            logger.info(
                                f"B站订阅检查任务检测到直播UP主动态更新: ID={sub.sub_id}, 消息长度={len(msg_list_up)}"
                            )
                            await send_sub_msg(msg_list_up, sub, bot_instance)
                        else:
                            logger.debug(
                                f"B站订阅检查任务未检测到直播UP主动态更新: ID={sub.sub_id}"
                            )

                except asyncio.TimeoutError:
                    logger.error(
                        f"B站订阅检查任务超时: ID={sub.sub_id}, 类型={sub.sub_type}, 名称={getattr(sub, 'uname', '') or getattr(sub, 'season_name', '未知')}"
                    )
                except Exception as e:
                    logger.error(
                        f"B站订阅检查任务异常: ID={sub.sub_id}, 类型={sub.sub_type}, 错误类型={type(e).__name__}, 错误信息={e}"
                    )
                    import traceback

                    logger.debug(
                        f"B站订阅检查任务异常详细信息:\n{traceback.format_exc()}"
                    )

                # 每个订阅检查之间添加短暂延迟，避免过于频繁的请求
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(
                f"B站订阅检查任务批次处理异常: 错误类型={type(e).__name__}, 错误信息={e}"
            )
            import traceback

            logger.debug(
                f"B站订阅检查任务批次处理异常详细信息:\n{traceback.format_exc()}"
            )

    total_duration = time.time() - start_time
    logger.debug(f"B站订阅检查任务执行完成 - 总耗时: {total_duration:.2f}秒")


async def send_sub_msg(msg_list: list, sub: BilibiliSub, bot: Bot):
    """
    推送信息
    :param msg_list: 消息列表
    :param sub: BilibiliSub
    :param bot: Bot
    """
    start_time = time.time()
    logger.debug(
        f"B站订阅推送开始: ID={sub.sub_id}, 类型={sub.sub_type}, 名称={getattr(sub, 'uname', '') or getattr(sub, 'season_name', '未知')}"
    )

    temp_group = []
    if not msg_list:
        logger.warning(
            f"B站订阅推送收到空消息列表: ID={sub.sub_id}, 类型={sub.sub_type}"
        )
        return

    # 获取订阅用户列表
    sub_users = sub.sub_users.split(",")[:-1]
    logger.debug(
        f"B站订阅推送目标用户数量: {len(sub_users)}, ID={sub.sub_id}, 类型={sub.sub_type}"
    )

    success_count = 0
    error_count = 0

    for x in sub_users:
        try:
            # 群聊消息推送
            if ":" in x and x.split(":")[1] not in temp_group:
                group_id = x.split(":")[1]
                temp_group.append(group_id)
                logger.debug(
                    f"B站订阅推送准备发送到群: {group_id}, ID={sub.sub_id}, 类型={sub.sub_type}"
                )

                # 检查机器人权限
                try:
                    role_info = await bot.get_group_member_info(
                        group_id=int(group_id),
                        user_id=int(bot.self_id),
                        no_cache=True,
                    )
                    bot_role = role_info["role"]
                    logger.debug(
                        f"B站订阅推送机器人在群 {group_id} 中的角色: {bot_role}"
                    )

                    # 根据配置和权限决定是否@全体
                    at_all_msg = None
                    if bot_role in ["owner", "admin"]:
                        if (
                            sub.sub_type == "live"
                            and Config.get_config("bilibili_sub", "LIVE_MSG_AT_ALL")
                        ) or (
                            sub.sub_type == "up"
                            and Config.get_config("bilibili_sub", "UP_MSG_AT_ALL")
                        ):
                            at_all_msg = UniMessage.at_all() + "\n"
                            logger.debug(
                                f"B站订阅推送将在群 {group_id} 中@全体成员: ID={sub.sub_id}, 类型={sub.sub_type}"
                            )
                            msg_list.insert(0, at_all_msg)
                except Exception as role_err:
                    logger.warning(
                        f"B站订阅推送获取机器人在群 {group_id} 中的角色失败: {type(role_err).__name__}, {role_err}"
                    )

                # 检查插件是否被禁用
                if await GroupConsole.is_block_plugin(group_id, "bilibili_sub"):
                    logger.debug(
                        f"B站订阅推送在群 {group_id} 中被禁用，跳过发送: ID={sub.sub_id}, 类型={sub.sub_type}"
                    )
                    continue

                # 发送消息
                logger.debug(
                    f"B站订阅推送正在发送到群 {group_id}: ID={sub.sub_id}, 类型={sub.sub_type}"
                )
                await PlatformUtils.send_message(
                    bot,
                    user_id=None,
                    group_id=group_id,
                    message=MessageUtils.build_message(msg_list),
                )
                logger.debug(
                    f"B站订阅推送成功发送到群 {group_id}: ID={sub.sub_id}, 类型={sub.sub_type}"
                )
                success_count += 1

                # 如果添加了@全体，发送后移除，避免影响其他群
                if at_all_msg:
                    msg_list.remove(at_all_msg)

            # 私聊消息推送
            else:
                user_id = x
                logger.debug(
                    f"B站订阅推送准备发送到私聊用户: {user_id}, ID={sub.sub_id}, 类型={sub.sub_type}"
                )
                await PlatformUtils.send_message(
                    bot,
                    user_id=user_id,
                    group_id=None,
                    message=MessageUtils.build_message(msg_list),
                )
                logger.debug(
                    f"B站订阅推送成功发送到私聊用户: {user_id}, ID={sub.sub_id}, 类型={sub.sub_type}"
                )
                success_count += 1

        except Exception as e:
            error_count += 1
            logger.error(
                f"B站订阅推送发生错误: ID={sub.sub_id}, 类型={sub.sub_type}, 错误类型={type(e).__name__}, 错误信息={e}"
            )
            import traceback

            logger.debug(f"B站订阅推送错误详细信息:\n{traceback.format_exc()}")

    total_duration = time.time() - start_time
    logger.info(
        f"B站订阅推送完成: ID={sub.sub_id}, 类型={sub.sub_type}, 成功={success_count}, 失败={error_count}, 耗时={total_duration:.2f}秒"
    )
