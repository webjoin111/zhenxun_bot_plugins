import asyncio
from typing import Dict
from arclet.alconna.typing import CommandMeta
from bilibili_api import login_v2 as bilibili_login
from nonebot.log import logger
from nonebot.params import ArgStr
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot_plugin_alconna import Alconna, Args, on_alconna
from nonebot_plugin_session import EventSession
from zhenxun.utils.image_utils import text2image
from zhenxun.utils.message import MessageUtils

from .config import get_credential, save_credential_to_file, clear_credential
from .data_source import (
    BilibiliSub,
    add_live_sub,
    add_season_sub,
    add_up_sub,
    get_media_id,
)

# 命令定义
add_sub = on_alconna(
    Alconna(
        "添加订阅",
        Args["sub_type", str]["sub_msg", str],
        meta=CommandMeta(compact=True),
    ),
    aliases={"dy", "添加订阅"},
    priority=5,
    block=True,
)

del_sub = on_alconna(
    Alconna(
        "删除订阅",
        Args["sub_type", str]["sub_msg", str],
        meta=CommandMeta(compact=True),
    ),
    aliases={"td", "取消订阅"},
    priority=5,
    block=True,
)

show_sub_info = on_alconna("查看订阅", priority=5, block=True)

blive_check = on_alconna(
    Alconna("bil_check"),
    aliases={"检测b站", "检测b站登录", "b站登录检测"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

blive_login = on_alconna(
    Alconna("bil_login"),
    aliases={"登录b站", "b站登录"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

blive_logout = on_alconna(
    Alconna("bil_logout", Args["uid", int]),
    aliases={"退出b站", "退出b站登录", "b站登录退出"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)

# 登录会话管理（为了支持更好的登录体验）
login_sessions: Dict[str, bilibili_login.QrCodeLogin] = {}


@add_sub.handle()
@del_sub.handle()
async def _(session: EventSession, state: T_State, sub_type: str, sub_msg: str):
    gid = session.id3 or session.id2
    if gid:
        sub_user = f"{session.id1}:{gid}"
    else:
        sub_user = f"{session.id1}"
    state["sub_type"] = sub_type
    state["sub_user"] = sub_user
    if "http" in sub_msg:
        sub_msg = sub_msg.split("?")[0]
        sub_msg = sub_msg[:-1] if sub_msg[-1] == "/" else sub_msg
        sub_msg = sub_msg.split("/")[-1]
    id_ = sub_msg[2:] if sub_msg.startswith("md") else sub_msg
    if not id_.isdigit():
        if sub_type in ["season", "动漫", "番剧"]:
            rst = "*以为您找到以下番剧，请输入Id选择：*\n"
            state["season_data"] = await get_media_id(id_)
            if len(state["season_data"]) == 0:
                await MessageUtils.build_message(f"未找到番剧：{sub_msg}").finish()
            for i, x in enumerate(state["season_data"]):
                rst += f"{i + 1}.{state['season_data'][x]['title']}\n----------\n"
            await MessageUtils.build_message("\n".join(rst.split("\n")[:-1])).send()
        else:
            await MessageUtils.build_message("Id 必须为全数字！").finish()
    else:
        state["id"] = int(id_)


@add_sub.got("sub_type")
@add_sub.got("sub_user")
@add_sub.got("id")
async def _(
    session: EventSession,
    state: T_State,
    id_: str = ArgStr("id"),
    sub_type: str = ArgStr("sub_type"),
    sub_user: str = ArgStr("sub_user"),
):
    if sub_type in ["season", "动漫", "番剧"] and state.get("season_data"):
        season_data = state["season_data"]
        if not id_.isdigit() or int(id_) < 1 or int(id_) > len(season_data):
            await add_sub.reject_arg("id", "Id必须为数字且在范围内！请重新输入...")
        id_ = season_data[int(id_) - 1]["media_id"]
    id_ = int(id_)
    if sub_type in ["主播", "直播"]:
        await MessageUtils.build_message(await add_live_sub(id_, sub_user)).send()
    elif sub_type.lower() in ["up", "用户"]:
        await MessageUtils.build_message(await add_up_sub(id_, sub_user)).send()
    elif sub_type in ["season", "动漫", "番剧"]:
        await MessageUtils.build_message(await add_season_sub(id_, sub_user)).send()
    else:
        await MessageUtils.build_message(
            "参数错误，第一参数必须为：主播/up/番剧！"
        ).finish()
    gid = session.id3 or session.id2
    logger.info(
        f"(USER {session.id1}, GROUP "
        f"{gid if gid else 'private'})"
        f" 添加订阅：{sub_type} -> {sub_user} -> {id_}"
    )


@del_sub.got("sub_type")
@del_sub.got("sub_user")
@del_sub.got("id")
async def _(
    session: EventSession,
    id_: str = ArgStr("id"),
    sub_type: str = ArgStr("sub_type"),
    sub_user: str = ArgStr("sub_user"),
):
    if sub_type in ["主播", "直播"]:
        result = await BilibiliSub.delete_bilibili_sub(int(id_), sub_user, "live")
    elif sub_type.lower() in ["up", "用户"]:
        result = await BilibiliSub.delete_bilibili_sub(int(id_), sub_user, "up")
    else:
        result = await BilibiliSub.delete_bilibili_sub(int(id_), sub_user)
    if result:
        await MessageUtils.build_message(f"删除订阅id：{id_} 成功...").send()
        gid = session.id3 or session.id2
        logger.info(
            f"(USER {session.id1}, GROUP {gid if gid else 'private'}) 删除订阅 {id_}"
        )
    else:
        await MessageUtils.build_message(f"删除订阅id：{id_} 失败...").send()


@show_sub_info.handle()
async def _(session: EventSession):
    gid = session.id3 or session.id2
    id_ = gid if gid else session.id1
    data = await BilibiliSub.filter(sub_users__contains=id_).all()
    live_rst = ""
    up_rst = ""
    season_rst = ""
    for x in data:
        if x.sub_type == "live":
            live_rst += (
                f"\t直播间id：{x.sub_id}\n\t名称：{x.uname}\n------------------\n"
            )
        if x.sub_type == "up":
            up_rst += f"\tUP：{x.uname}\n\tuid：{x.uid}\n------------------\n"
        if x.sub_type == "season":
            season_rst += (
                f"\t番剧id：{x.sub_id}\n"
                f"\t番名：{x.season_name}\n"
                f"\t当前集数：{x.season_current_episode}\n"
                f"------------------\n"
            )
    live_rst = "当前订阅的直播：\n" + live_rst if live_rst else live_rst
    up_rst = "当前订阅的UP：\n" + up_rst if up_rst else up_rst
    season_rst = "当前订阅的番剧：\n" + season_rst if season_rst else season_rst
    if not live_rst and not up_rst and not season_rst:
        live_rst = "该群目前没有任何订阅..." if gid else "您目前没有任何订阅..."

    img = await text2image(live_rst + up_rst + season_rst, padding=10, color="#f9f6f2")
    await MessageUtils.build_message(img).finish()


@blive_check.handle()
async def _():
    msgs = []

    # 检查新的 credential 系统
    credential = get_credential()
    if credential:
        status_lines = ["新登录系统状态："]

        if credential.has_sessdata():
            status_lines.append("✅ SESSDATA: 已设置")
        else:
            status_lines.append("❌ SESSDATA: 未设置")

        if credential.has_bili_jct():
            status_lines.append("✅ bili_jct: 已设置")
        else:
            status_lines.append("❌ bili_jct: 未设置")

        if credential.has_buvid3():
            status_lines.append("✅ buvid3: 已设置")
        else:
            status_lines.append("❌ buvid3: 未设置")

        if credential.has_dedeuserid():
            status_lines.append("✅ DedeUserID: 已设置")
        else:
            status_lines.append("❌ DedeUserID: 未设置")

        if credential.has_ac_time_value():
            status_lines.append("✅ ac_time_value: 已设置 (支持自动刷新)")
        else:
            status_lines.append("❌ ac_time_value: 未设置 (不支持自动刷新)")

        try:
            is_valid = await credential.check_valid()
            if is_valid:
                status_lines.append("✅ 凭证有效，可以正常使用")
            else:
                status_lines.append("❌ 凭证无效，请重新登录")
        except Exception as e:
            logger.error("检查凭证有效性时出错", e=e)
            status_lines.append(f"❓ 凭证状态检查失败: {str(e)}")

        try:
            need_refresh = await credential.check_refresh()
            if need_refresh:
                status_lines.append("⚠️ 凭证需要刷新，将在下次检查时自动刷新")
            else:
                status_lines.append("✅ 凭证不需要刷新")
        except Exception as e:
            logger.error("检查凭证刷新状态时出错", e=e)
            status_lines.append(f"❓ 凭证刷新状态检查失败: {str(e)}")

        msgs.append("\n".join(status_lines))

    if not msgs:
        await MessageUtils.build_message("当前未登录B站账号，请使用 bil_login 命令登录。").finish()

    await MessageUtils.build_message("\n".join(msgs)).finish()


@blive_login.handle()
async def _():
    login_handler = bilibili_login.QrCodeLogin(
        platform=bilibili_login.QrCodeLoginChannel.WEB
    )
    await login_handler.generate_qrcode()

    qr_picture_obj = login_handler.get_qrcode_picture()

    if not qr_picture_obj or not qr_picture_obj.content:
        await MessageUtils.build_message("获取二维码图像数据失败").finish()
        return

    await MessageUtils.build_message(qr_picture_obj.content).send()

    try:
        max_wait_time = 120
        interval = 3
        elapsed_time = 0
        logged_in = False
        while elapsed_time < max_wait_time:
            login_status = await login_handler.check_state()
            if login_status == bilibili_login.QrCodeLoginEvents.DONE:
                logged_in = True
                break
            elif login_status == bilibili_login.QrCodeLoginEvents.TIMEOUT:
                await MessageUtils.build_message("二维码已超时，请重新登录").finish()
                return
            await asyncio.sleep(interval)
            elapsed_time += interval

        if not logged_in:
            await MessageUtils.build_message("登录超时，请重试").finish()
            return

        credential = login_handler.get_credential()
        assert credential, "登录失败，返回凭据数据为空"

        # 保存到新系统
        try:
            await save_credential_to_file(credential)
            logger.info("登录凭证已保存到 credential 系统")
        except Exception as e:
            logger.error(f"保存到 credential 系统失败: {e}")
            await MessageUtils.build_message(f"保存登录信息失败: {e}").finish()
            return

    except Exception as e:
        await MessageUtils.build_message(f"登录失败: {e}").finish()
        return

    await MessageUtils.build_message("登录成功，已将验证信息保存").finish()


@blive_logout.handle()
async def _(uid: int):
    credential = get_credential()
    if not credential:
        await MessageUtils.build_message("当前没有登录信息").finish()
        return

    if credential.dedeuserid != str(uid):
        await MessageUtils.build_message(f"当前登录的账号UID不是 {uid}").finish()
        return

    # 清除凭证
    try:
        await clear_credential()
        await MessageUtils.build_message(f"账号 {uid} 已退出登录").finish()
    except Exception as e:
        logger.error(f"退出登录失败: {e}")
        await MessageUtils.build_message(f"退出登录失败: {e}").finish()
