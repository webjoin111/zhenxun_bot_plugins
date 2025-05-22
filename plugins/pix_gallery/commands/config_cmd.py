from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna import Alconna, Args, Arparma
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils._image_template import ImageTemplate
from zhenxun.configs.config import Config

from ..config import base_config
from ..utils import parse_value_type


_view_config_matcher = on_alconna(
    Alconna(
        ".pix配置查看",
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_set_config_matcher = on_alconna(
    Alconna(
        ".pix配置设置",
        Args["key", str]["value", str],
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)

_reset_config_matcher = on_alconna(
    Alconna(
        ".pix配置重置",
        Args["key", str],
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
)


@_view_config_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
):
    """配置查看命令处理"""
    configs = {}
    for key in base_config.configs:
        configs[key] = base_config.get(key)

    column_name = ["配置项", "值"]
    data_list = [[k, str(v)] for k, v in configs.items()]

    title = "PIX配置查看"

    image = await ImageTemplate.table_page(title, None, column_name, data_list)
    await MessageUtils.build_message(image).send()

    logger.info("PIX 配置查看", arparma.header_result, session=session)


@_set_config_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    key: str,
    value: str,
):
    """配置设置命令处理"""
    if key not in base_config.configs:
        await MessageUtils.build_message(f"配置项 {key} 不存在").finish()

    current_value = base_config.get(key)

    try:
        typed_value = parse_value_type(value, type(current_value))
    except Exception as e:
        await MessageUtils.build_message(f"值类型转换错误: {e}").finish()

    Config.set_config("pixiv", key, typed_value, auto_save=True)
    await MessageUtils.build_message(f"配置项 {key} 已设置为 {typed_value}").send()

    logger.info(
        f"PIX 配置设置: key: {key}, value: {typed_value}",
        arparma.header_result,
        session=session,
    )


@_reset_config_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    key: str,
):
    """配置重置命令处理"""
    if key not in base_config.configs:
        await MessageUtils.build_message(f"配置项 {key} 不存在").finish()

    default_value = base_config.configs[key].default_value

    if default_value is None:
        await MessageUtils.build_message(f"配置项 {key} 没有默认值").finish()

    Config.set_config("pixiv", key, default_value, auto_save=True)
    await MessageUtils.build_message(f"配置项 {key} 已重置为 {default_value}").send()

    logger.info(f"PIX 配置重置: key: {key}", arparma.header_result, session=session)
