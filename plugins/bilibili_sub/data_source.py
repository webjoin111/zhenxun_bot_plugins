import asyncio
import httpx
import nonebot
import time
from asyncio.exceptions import TimeoutError
from bilibili_api.exceptions import ResponseCodeException
from bilibili_api import search as bilibili_search
from datetime import datetime, timedelta
from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.utils import ResourceDirManager

from .config import base_config, get_credential, check_and_refresh_credential, DYNAMIC_PATH
from .filter import check_page_elements, check_dynamic_hybrid, check_dynamic_content_api
from .model import BilibiliSub
from .utils import (
    get_dynamic_screenshot,
    get_meta,
    get_room_info_by_id,
    get_user_card,
    get_user_dynamics,
    get_videos,
)

ResourceDirManager.add_temp_dir(DYNAMIC_PATH)


async def fetch_image_bytes(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def handle_video_info_error(video_info: dict):
    """处理B站视频信息获取错误并发送通知给超级用户"""
    str_msg = "b站订阅检测失败："
    if video_info["code"] == -352:
        str_msg += "风控校验失败，请登录后再尝试。发送'登录b站'"
    elif video_info["code"] == -799:
        str_msg += "请求过于频繁，请增加时长，更改配置文件下的'CHECK_TIME''"
    else:
        str_msg += f"{video_info['code']}，{video_info['message']}"

    bots = nonebot.get_bots()
    for bot_instance in bots.values():
        if bot_instance:
            await PlatformUtils.send_superuser(bot_instance, str_msg)

    return str_msg


async def add_live_sub(live_id: int, sub_user: str) -> str:
    """

    添加直播订阅
    :param live_id: 直播房间号
    :param sub_user: 订阅用户 id # 7384933:private or 7384933:2342344(group)
    :return:
    """
    try:
        try:
            live_info_raw = await get_room_info_by_id(live_id)
            if not live_info_raw or not live_info_raw.get("room_info"):
                return f"未找到房间号Id：{live_id} 的信息，或API返回结构不符，请检查Id是否正确"
            live_info = live_info_raw["room_info"]
        except ResponseCodeException:
            return f"未找到房间号Id：{live_id} 的信息，请检查Id是否正确"
        uid = live_info["uid"]
        room_id = live_info["room_id"]
        short_id = live_info["short_id"]
        title = live_info["title"]
        live_status = live_info["live_status"]
        if await BilibiliSub.sub_handle(
            room_id,
            "live",
            sub_user,
            uid=uid,
            live_short_id=short_id,
            live_status=live_status,
        ):
            await _get_up_status(room_id)
            uname_obj = await BilibiliSub.get_or_none(sub_id=room_id)
            uname = uname_obj.uname if uname_obj else "未知主播"
            return (
                "订阅成功！🎉\n"
                f"主播名称：{uname}\n"
                f"直播标题：{title}\n"
                f"直播间ID：{room_id}\n"
                f"用户UID：{uid}"
            )
        else:
            return "添加订阅失败..."
    except Exception as e:
        logger.error(f"订阅主播live_id：{live_id} 发生了错误 {type(e)}：{e}")
    return "添加订阅失败..."


async def add_up_sub(uid: int, sub_user: str) -> str:
    """
    添加订阅 UP
    :param uid: UP uid
    :param sub_user: 订阅用户
    """
    try:
        try:
            user_info = await get_user_card(uid)
            if not user_info:
                return f"未找到UpId：{uid} 的信息，或API返回结构不符，请检查Id是否正确"
        except ResponseCodeException:
            return f"未找到UpId：{uid} 的信息，请检查Id是否正确"
        uname = user_info["name"]
        try:
            dynamic_info = await get_user_dynamics(uid)
        except ResponseCodeException as e:
            if e.code == -352:
                return "风控校验失败，请联系管理员登录b站'"
            return f"获取动态失败: {e.code} {e.message}"
        dynamic_upload_time = 0
        if dynamic_info and dynamic_info.get("cards"):
            dynamic_upload_time = dynamic_info["cards"][0]["desc"]["timestamp"]

        video_info_raw = await get_videos(uid)

        if not isinstance(video_info_raw, dict):
            logger.error(
                f"get_videos 返回了非预期的类型: {type(video_info_raw)} for UID: {uid}"
            )
            await handle_video_info_error(
                {
                    "code": -1,
                    "message": f"获取视频信息时返回了非字典类型: {type(video_info_raw)}",
                }
            )
            return "订阅失败，请联系管理员（视频信息获取类型错误）"

        if "code" in video_info_raw and video_info_raw["code"] != 0:
            logger.error(f"get_videos API error for UID {uid}: {video_info_raw}")
            await handle_video_info_error(video_info_raw)
            return f"获取视频列表失败: {video_info_raw.get('message', '未知API错误')}"

        if "list" in video_info_raw and "page" in video_info_raw:
            video_info_data = video_info_raw
        elif "data" in video_info_raw and isinstance(video_info_raw["data"], dict):
            video_info_data = video_info_raw["data"]
        else:
            logger.error(
                f"get_videos 返回的视频数据结构不符合预期 for UID {uid}: {video_info_raw}"
            )
            await handle_video_info_error(video_info_raw.get("data", video_info_raw))
            return "订阅失败，请联系管理员（视频数据结构错误）"

        latest_video_created = 0
        if video_info_data.get("list", {}).get("vlist"):
            latest_video_created = video_info_data["list"]["vlist"][0].get("created", 0)
        else:
            logger.warning(f"UID {uid} 的视频列表 (vlist) 为空或不存在。")

        if await BilibiliSub.sub_handle(
            uid,
            "up",
            sub_user,
            uid=uid,
            uname=uname,
            dynamic_upload_time=dynamic_upload_time,
            latest_video_created=latest_video_created,
        ):
            return f"订阅成功！🎉\nUP主名称：{uname}\n用户UID：{uid}"
        else:
            return "添加订阅失败（数据库处理错误）"
    except ResponseCodeException as e:
        logger.error(
            f"订阅Up uid：{uid} API请求时发生错误 {type(e)}：{e.code} {e.message}"
        )
        return f"API请求错误：{e.message}"
    except Exception as e:
        logger.error(f"订阅Up uid：{uid} 发生了未预料的错误 {type(e)}：{e}")
        import traceback

        logger.error(traceback.format_exc())
    return "添加订阅失败（未知错误）"


async def add_season_sub(media_id: int, sub_user: str) -> str:
    """
    添加订阅 UP
    :param media_id: 番剧 media_id
    :param sub_user: 订阅用户
    """
    try:
        try:
            season_info_raw = await get_meta(media_id)
            if not season_info_raw or not season_info_raw.get("media"):
                return f"未找到media_id：{media_id} 的信息，或API返回结构不符，请检查Id是否正确"
            season_info = season_info_raw["media"]
        except ResponseCodeException:
            return f"未找到media_id：{media_id} 的信息，请检查Id是否正确"
        season_id = season_info["season_id"]
        season_current_episode = season_info["new_ep"]["index"]
        season_name = season_info["title"]
        if await BilibiliSub.sub_handle(
            media_id,
            "season",
            sub_user,
            season_name=season_name,
            season_id=season_id,
            season_current_episode=season_current_episode,
        ):
            return (
                "订阅成功！🎉\n"
                f"番剧名称：{season_name}\n"
                f"当前集数：{season_current_episode}"
            )
        else:
            return "添加订阅失败..."
    except Exception as e:
        logger.error(f"订阅番剧 media_id：{media_id} 发生了错误 {type(e)}：{e}")
    return "添加订阅失败..."


async def delete_sub(sub_id: str, sub_user: str) -> str:
    """
    删除订阅
    :param sub_id: 订阅 id
    :param sub_user: 订阅用户 id # 7384933:private or 7384933:2342344(group)
    """
    if await BilibiliSub.delete_bilibili_sub(int(sub_id), sub_user):
        return f"已成功取消订阅：{sub_id}"
    else:
        return f"取消订阅：{sub_id} 失败，请检查是否订阅过该Id...."


async def get_media_id(keyword: str) -> dict:
    """
    获取番剧的 media_id
    :param keyword: 番剧名称
    """
    _season_data = {}
    # 使用新的 credential 系统
    credential = get_credential()

    # 检查并刷新凭证
    if credential:
        await check_and_refresh_credential()

    for _ in range(3):
        try:
            logger.debug(f"使用bilibili_api搜索番剧: 关键词={keyword}")
            search_result = await bilibili_search.search_by_type(
                keyword,
                search_type=bilibili_search.SearchObjectType.MEDIA_BANGUMI,
                credential=credential,
            )

            if search_result and search_result.get("result"):
                logger.debug(
                    f"搜索番剧成功: 关键词={keyword}, 找到结果数={len(search_result['result'])}"
                )
                idx = 0
                for item in search_result["result"]:
                    if "media_id" in item and "title" in item:
                        title = (
                            item["title"]
                            .replace('<em class="keyword">', "")
                            .replace("</em>", "")
                        )
                        _season_data[idx] = {
                            "media_id": item["media_id"],
                            "title": title,
                        }
                        idx += 1
                if _season_data:
                    logger.info(
                        f"成功获取番剧信息: 关键词={keyword}, 找到番剧数={len(_season_data)}"
                    )
                    return _season_data
                else:
                    logger.warning(f"搜索结果中未找到有效番剧信息: 关键词={keyword}")
            else:
                logger.warning(f"搜索番剧未返回结果: 关键词={keyword}")

        except TimeoutError:
            logger.warning(f"搜索番剧超时: 关键词={keyword}, 尝试重试")
        except Exception as e:
            logger.error(
                f"搜索番剧异常: 关键词={keyword}, 异常类型={type(e).__name__}, 异常信息={e}"
            )
            import traceback

            logger.debug(f"异常详细信息:\n{traceback.format_exc()}")

    logger.error(f"搜索番剧失败: 关键词={keyword}, 已重试3次")
    return {}


async def get_sub_status(id_: int, sub_type: str) -> list | None:
    """
    获取订阅状态
    :param id_: 订阅 id
    :param sub_type: 订阅类型
    """
    start_time = time.time()
    logger.debug(f"开始获取订阅状态: ID={id_}, 类型={sub_type}")

    try:
        if sub_type == "live":
            logger.debug(f"调用直播状态检查: ID={id_}")
            result = await _get_live_status(id_)
            duration = time.time() - start_time
            if result:
                logger.info(
                    f"直播状态检查完成: ID={id_}, 检测到更新, 耗时={duration:.2f}秒"
                )
            else:
                logger.debug(
                    f"直播状态检查完成: ID={id_}, 未检测到更新, 耗时={duration:.2f}秒"
                )
            return result

        elif sub_type == "up":
            logger.debug(f"调用UP主状态检查: ID={id_}")
            result = await _get_up_status(id_)
            duration = time.time() - start_time
            if result:
                logger.info(
                    f"UP主状态检查完成: ID={id_}, 检测到更新, 耗时={duration:.2f}秒"
                )
            else:
                logger.debug(
                    f"UP主状态检查完成: ID={id_}, 未检测到更新, 耗时={duration:.2f}秒"
                )
            return result

        elif sub_type == "season":
            logger.debug(f"调用番剧状态检查: ID={id_}")
            result = await _get_season_status(id_)
            duration = time.time() - start_time
            if result:
                logger.info(
                    f"番剧状态检查完成: ID={id_}, 检测到更新, 耗时={duration:.2f}秒"
                )
            else:
                logger.debug(
                    f"番剧状态检查完成: ID={id_}, 未检测到更新, 耗时={duration:.2f}秒"
                )
            return result

        else:
            logger.warning(f"未知的订阅类型: {sub_type}, ID={id_}")
            return None

    except ResponseCodeException as msg:
        error_code = getattr(msg, "code", "unknown")
        error_message = getattr(msg, "message", str(msg))
        logger.error(
            f"订阅状态检查失败: ID={id_}, 类型={sub_type}, 错误码={error_code}, 错误信息={error_message}"
        )
        return None
    except Exception as e:
        logger.error(
            f"订阅状态检查发生未预期异常: ID={id_}, 类型={sub_type}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return None


async def _get_live_status(id_: int) -> list:
    """
    获取直播订阅状态
    :param id_: 直播间 id
    """
    start_time = time.time()
    logger.debug(f"直播状态检查开始: 房间ID={id_}")

    try:
        logger.debug(f"获取直播间信息: 房间ID={id_}")
        live_info_raw = await get_room_info_by_id(id_)
        if not live_info_raw or not live_info_raw.get("room_info"):
            logger.error(
                f"直播间信息获取失败或结构异常: 房间ID={id_}, 返回数据={live_info_raw}"
            )
            return []

        live_info = live_info_raw["room_info"]
        logger.debug(f"成功获取直播间信息: 房间ID={id_}, 数据结构完整")
    except Exception as e:
        logger.error(
            f"获取直播间信息异常: 房间ID={id_}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return []

    title = live_info["title"]
    room_id = live_info["room_id"]
    live_status = live_info["live_status"]
    cover = live_info["user_cover"]
    logger.debug(
        f"直播间信息: 房间ID={id_}, 实际房间ID={room_id}, 标题={title}, 直播状态={live_status}"
    )

    sub = await BilibiliSub.get_or_none(sub_id=id_)
    if not sub:
        logger.warning(f"直播间订阅信息不存在: 房间ID={id_}")
        return []

    logger.debug(
        f"订阅信息: 房间ID={id_}, 主播名={sub.uname}, 当前状态={sub.live_status}, API状态={live_status}"
    )

    msg_list = []
    image = None

    if sub.live_status != live_status:
        logger.info(
            f"直播状态变化: 房间ID={id_}, 主播={sub.uname}, 旧状态={sub.live_status}, 新状态={live_status}"
        )

        await BilibiliSub.sub_handle(id_, live_status=live_status)
        logger.debug(f"已更新数据库中的直播状态: 房间ID={id_}, 新状态={live_status}")

        try:
            logger.debug(f"开始获取直播封面: 房间ID={id_}, 封面URL={cover}")
            image_bytes = await fetch_image_bytes(cover)
            image = BuildImage(background=image_bytes)
            logger.debug(f"成功获取直播封面: 房间ID={id_}")
        except Exception as e:
            logger.error(
                f"直播封面获取失败: 房间ID={id_}, 异常类型={type(e).__name__}, 异常信息={e}"
            )
            import traceback

            logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
    else:
        logger.debug(f"直播状态未变化: 房间ID={id_}, 状态={live_status}")

    if sub and sub.live_status in [0, 2] and live_status == 1 and image:
        logger.info(f"检测到开播: 房间ID={id_}, 主播={sub.uname}, 标题={title}")
        msg_list = [
            image,
            "\n",
            f"{sub.uname} 开播啦！🎉\n",
            f"标题：{title}\n",
            f"直播间链接：https://live.bilibili.com/{room_id}",
        ]

    duration = time.time() - start_time
    if msg_list:
        logger.info(
            f"直播状态检查完成: 房间ID={id_}, 检测到开播, 耗时={duration:.2f}秒"
        )
    else:
        logger.debug(
            f"直播状态检查完成: 房间ID={id_}, 未检测到开播, 耗时={duration:.2f}秒"
        )

    return msg_list


async def fetch_image_with_retry(url, retries=3, delay=2):
    """带重试的图片获取函数"""
    for i in range(retries):
        try:
            return await fetch_image_bytes(url)
        except Exception as e:
            if i < retries - 1:
                await asyncio.sleep(delay)
            else:
                raise e
    return None


async def _get_up_status(id_: int) -> list:
    start_time = time.time()
    current_time = datetime.now()
    logger.debug(f"UP主状态检查开始: UP主ID={id_}")

    _user = await BilibiliSub.get_or_none(sub_id=id_)
    if not _user:
        logger.warning(f"UP主订阅信息不存在: UP主ID={id_}")
        return []

    try:
        logger.debug(f"获取UP主信息: UP主ID={id_}, UID={_user.uid}")
        user_info = await get_user_card(_user.uid)
        if not user_info:
            logger.warning(f"UP主信息获取失败: UP主ID={id_}, UID={_user.uid}")
            return []
        uname = user_info["name"]
        logger.debug(f"成功获取UP主信息: UP主ID={id_}, UID={_user.uid}, 用户名={uname}")
    except Exception as e:
        logger.error(
            f"获取UP主信息异常: UP主ID={id_}, UID={_user.uid}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return []

    try:
        logger.debug(f"获取UP主视频列表: UP主ID={id_}, UID={_user.uid}")
        video_info_raw = await get_videos(_user.uid)
        logger.debug(f"成功获取视频列表: UP主ID={id_}, UID={_user.uid}")
    except Exception as e:
        logger.error(
            f"获取视频列表异常: UP主ID={id_}, UID={_user.uid}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return []

    if not isinstance(video_info_raw, dict):
        logger.error(
            f"视频信息格式错误: UP主ID={id_}, UID={_user.uid}, 返回类型={type(video_info_raw)}"
        )
        await handle_video_info_error(
            {"code": -1, "message": "获取视频信息时返回了非字典类型"}
        )
        return []

    if "code" in video_info_raw and video_info_raw.get("code", 0) != 0:
        logger.error(
            f"视频API返回错误: UP主ID={id_}, UID={_user.uid}, 错误码={video_info_raw.get('code')}, 错误信息={video_info_raw.get('message', '未知错误')}"
        )
        await handle_video_info_error(video_info_raw)
        return []

    logger.debug(f"解析视频数据结构: UP主ID={id_}, UID={_user.uid}")
    if "list" in video_info_raw and "page" in video_info_raw:
        video_info_data = video_info_raw
        logger.debug(f"使用直接返回的视频数据结构: UP主ID={id_}, UID={_user.uid}")
    elif "data" in video_info_raw and isinstance(video_info_raw["data"], dict):
        video_info_data = video_info_raw["data"]
        logger.debug(f"使用data字段中的视频数据结构: UP主ID={id_}, UID={_user.uid}")
    else:
        logger.error(
            f"视频数据结构不符合预期: UP主ID={id_}, UID={_user.uid}, 数据结构={list(video_info_raw.keys())}"
        )
        await handle_video_info_error(video_info_raw.get("data", video_info_raw))
        return []

    msg_list = []
    time_threshold = current_time - timedelta(minutes=30)
    dividing_line = "\n-------------\n"
    logger.debug(f"设置时间阈值: UP主ID={id_}, 阈值={time_threshold}")

    if _user.uname != uname:
        logger.info(
            f"UP主用户名变更: UP主ID={id_}, UID={_user.uid}, 旧名称={_user.uname}, 新名称={uname}"
        )
        await BilibiliSub.sub_handle(id_, uname=uname)
        logger.debug(f"已更新UP主用户名: UP主ID={id_}, 新名称={uname}")
    else:
        logger.debug(f"UP主用户名未变更: UP主ID={id_}, 用户名={uname}")

    logger.debug(f"开始获取用户动态: UP主ID={id_}, UID={_user.uid}")
    dynamic_img = None
    dynamic_upload_time = 0
    link = ""
    try:
        dynamic_img, dynamic_upload_time, link = await get_user_dynamic(
            _user.uid, _user
        )
        if dynamic_img:
            logger.debug(
                f"成功获取动态: UP主ID={id_}, UID={_user.uid}, 动态链接={link}"
            )
        else:
            logger.debug(f"未获取到新动态: UP主ID={id_}, UID={_user.uid}")
    except ResponseCodeException as msg:
        logger.error(
            f"动态获取失败: UP主ID={id_}, UID={_user.uid}, 错误码={getattr(msg, 'code', 'unknown')}, 错误信息={getattr(msg, 'message', str(msg))}"
        )

    if dynamic_img and (
        _user.dynamic_upload_time is None
        or _user.dynamic_upload_time < dynamic_upload_time
    ):
        dynamic_time = datetime.fromtimestamp(dynamic_upload_time)
        dynamic_time_str = dynamic_time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"检测到新动态: UP主ID={id_}, UID={_user.uid}, 动态链接={link}, 发布时间={dynamic_time_str}"
        )

        if dynamic_time > time_threshold:
            logger.debug(
                f"动态在时间阈值内: UP主ID={id_}, 发布时间={dynamic_time_str}, 阈值={time_threshold}"
            )

            if base_config.get("ENABLE_AD_FILTER"):
                logger.info(f"[广告过滤] 启用广告过滤检查: UP主ID={id_}, UID={_user.uid}, 用户名={_user.uname}, 动态链接={link}")

                # 从链接中提取动态ID
                dynamic_id = link.split("/")[-1] if "/" in link else ""
                logger.debug(f"[广告过滤] 提取动态ID: UP主ID={id_}, 动态ID={dynamic_id}, 原链接={link}")

                # 获取过滤方法配置，默认使用混合方案
                filter_method = base_config.get("AD_FILTER_METHOD", "hybrid")
                logger.debug(f"[广告过滤] 使用过滤方法: {filter_method}, UP主ID={id_}, 动态ID={dynamic_id}")

                is_ad = False
                filter_start_time = time.time()

                try:
                    if filter_method == "api":
                        # 仅使用API检测
                        logger.debug(f"[广告过滤] 执行API检测: UP主ID={id_}, 动态ID={dynamic_id}")
                        is_ad = await check_dynamic_content_api(_user.uid, dynamic_id)
                    elif filter_method == "page":
                        # 仅使用页面检测（原方法）
                        logger.debug(f"[广告过滤] 执行页面检测: UP主ID={id_}, 动态链接={link}")
                        is_ad = await check_page_elements(link)
                    else:
                        # 默认使用混合方案
                        logger.debug(f"[广告过滤] 执行混合检测: UP主ID={id_}, 动态ID={dynamic_id}, 动态链接={link}")
                        is_ad = await check_dynamic_hybrid(_user.uid, dynamic_id, link)

                    filter_duration = time.time() - filter_start_time

                    if is_ad:
                        logger.warning(
                            f"[广告过滤] 动态被过滤拦截: UP主ID={id_}, UID={_user.uid}, 用户名={_user.uname}, 动态ID={dynamic_id}, 方法={filter_method}, 耗时={filter_duration:.2f}秒"
                        )
                        await BilibiliSub.sub_handle(
                            id_, dynamic_upload_time=dynamic_upload_time
                        )
                        return msg_list
                    else:
                        logger.info(f"[广告过滤] 动态通过过滤检查: UP主ID={id_}, UID={_user.uid}, 用户名={_user.uname}, 动态ID={dynamic_id}, 方法={filter_method}, 耗时={filter_duration:.2f}秒")

                except Exception as e:
                    filter_duration = time.time() - filter_start_time
                    logger.error(f"[广告过滤] 过滤检查异常: UP主ID={id_}, UID={_user.uid}, 动态ID={dynamic_id}, 方法={filter_method}, 耗时={filter_duration:.2f}秒, 错误={e}")
                    # 过滤异常时不拦截动态，避免误杀

            logger.debug(f"更新动态时间: UP主ID={id_}, 新时间={dynamic_upload_time}")
            await BilibiliSub.sub_handle(id_, dynamic_upload_time=dynamic_upload_time)
            msg_list = [f"{uname} 发布了动态！📢\n", dynamic_img, f"\n查看详情：{link}"]
            logger.info(
                f"动态推送消息已准备: UP主ID={id_}, UID={_user.uid}, 用户名={uname}"
            )
        else:
            logger.debug(
                f"动态不在时间阈值内，仅更新记录: UP主ID={id_}, 发布时间={dynamic_time_str}, 阈值={time_threshold}"
            )
            await BilibiliSub.sub_handle(id_, dynamic_upload_time=dynamic_upload_time)

    logger.debug(f"开始检查视频更新: UP主ID={id_}, UID={_user.uid}")
    video = None
    if video_info_data.get("list", {}).get("vlist"):
        video = video_info_data["list"]["vlist"][0]
        latest_video_created = video.get("created", 0)
        video_title = video.get("title", "未知标题")
        video_bvid = video.get("bvid", "未知BV号")

        video_time_str = (
            datetime.fromtimestamp(latest_video_created).strftime("%Y-%m-%d %H:%M:%S")
            if latest_video_created
            else "未知时间"
        )
        logger.debug(
            f"获取到最新视频: UP主ID={id_}, 标题={video_title}, BV号={video_bvid}, 发布时间={video_time_str}"
        )

        if (
            latest_video_created
            and (
                _user.latest_video_created is None
                or _user.latest_video_created < latest_video_created
            )
            and datetime.fromtimestamp(latest_video_created) > time_threshold
        ):
            logger.info(
                f"检测到新视频: UP主ID={id_}, UID={_user.uid}, 标题={video_title}, BV号={video_bvid}, 发布时间={video_time_str}"
            )
            video_url = f"https://www.bilibili.com/video/{video_bvid}"

            image = None
            try:
                logger.debug(
                    f"开始获取视频封面: UP主ID={id_}, 视频BV号={video_bvid}, 封面URL={video.get('pic', '无封面')}"
                )
                image_bytes = await fetch_image_with_retry(
                    video["pic"], retries=3, delay=2
                )
                if image_bytes:
                    image = BuildImage(background=image_bytes)
                    logger.debug(
                        f"成功获取视频封面: UP主ID={id_}, 视频BV号={video_bvid}"
                    )
                else:
                    logger.warning(
                        f"视频封面获取失败: UP主ID={id_}, 视频BV号={video_bvid}, 封面URL={video.get('pic', '无封面')}"
                    )
            except Exception as e:
                logger.error(
                    f"视频封面获取异常: UP主ID={id_}, 视频BV号={video_bvid}, 异常类型={type(e).__name__}, 异常信息={e}"
                )

            video_msg = [
                f"{uname} 投稿了新视频啦！🎉\n",
                f"标题：{video_title}\n",
                f"Bvid：{video_bvid}\n",
                f"链接：{video_url}",
            ]
            logger.debug(f"准备视频推送消息: UP主ID={id_}, 视频BV号={video_bvid}")

            if msg_list and image:
                logger.debug(f"组合动态和视频消息(带图): UP主ID={id_}")
                msg_list += [dividing_line, image] + video_msg
            elif image:
                logger.debug(f"仅视频消息(带图): UP主ID={id_}")
                msg_list = [image] + video_msg
            elif msg_list:
                logger.debug(f"组合动态和视频消息(无图): UP主ID={id_}")
                msg_list += [dividing_line] + video_msg
            else:
                logger.debug(f"仅视频消息(无图): UP主ID={id_}")
                msg_list = ["⚠️ 封面获取失败，但仍需通知："] + video_msg

            logger.debug(
                f"更新视频发布时间: UP主ID={id_}, 新时间={latest_video_created}"
            )
            await BilibiliSub.sub_handle(id_, latest_video_created=latest_video_created)
            logger.info(
                f"视频推送消息已准备: UP主ID={id_}, UID={_user.uid}, 用户名={uname}, 视频BV号={video_bvid}"
            )

        elif latest_video_created and (
            _user.latest_video_created is None
            or latest_video_created > _user.latest_video_created
        ):
            logger.debug(
                f"检测到较早的新视频，仅更新记录: UP主ID={id_}, 视频发布时间={video_time_str}, 阈值={time_threshold}"
            )
            await BilibiliSub.sub_handle(id_, latest_video_created=latest_video_created)
        else:
            logger.debug(
                f"未检测到新视频: UP主ID={id_}, 最新视频时间={video_time_str}, 本地记录时间={'无记录' if _user.latest_video_created is None else datetime.fromtimestamp(_user.latest_video_created).strftime('%Y-%m-%d %H:%M:%S')}"
            )
    else:
        logger.info(f"视频列表为空: UP主ID={id_}, UID={_user.uid}")

    duration = time.time() - start_time
    if msg_list:
        logger.info(
            f"UP主状态检查完成: UP主ID={id_}, 检测到更新, 耗时={duration:.2f}秒"
        )
    else:
        logger.debug(
            f"UP主状态检查完成: UP主ID={id_}, 未检测到更新, 耗时={duration:.2f}秒"
        )

    return msg_list


async def _get_season_status(id_) -> list:
    """
    获取 番剧 更新状态
    :param id_: 番剧 id
    """
    start_time = time.time()
    logger.debug(f"番剧状态检查开始: 番剧ID={id_}")

    try:
        logger.debug(f"获取番剧元数据: 番剧ID={id_}")
        season_info_raw = await get_meta(id_)
        if not season_info_raw or not season_info_raw.get("media"):
            logger.error(
                f"番剧信息获取失败或结构异常: 番剧ID={id_}, 返回数据={season_info_raw}"
            )
            return []

        season_info_media = season_info_raw["media"]
        logger.debug(f"成功获取番剧信息: 番剧ID={id_}, 数据结构完整")
    except Exception as e:
        logger.error(
            f"获取番剧信息异常: 番剧ID={id_}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return []

    title = season_info_media["title"]
    logger.debug(f"番剧信息: 番剧ID={id_}, 标题={title}")

    _sub = await BilibiliSub.get_or_none(sub_id=id_)
    if not _sub:
        logger.warning(f"番剧订阅信息不存在: 番剧ID={id_}")
        return []

    _idx = _sub.season_current_episode
    new_ep = season_info_media["new_ep"]["index"]
    logger.debug(
        f"番剧集数信息: 番剧ID={id_}, 标题={title}, 当前集数={_idx}, 最新集数={new_ep}"
    )

    msg_list = []
    image = None

    if new_ep != _idx:
        logger.info(
            f"番剧更新: 番剧ID={id_}, 标题={title}, 旧集数={_idx}, 新集数={new_ep}"
        )

        try:
            logger.debug(
                f"开始获取番剧封面: 番剧ID={id_}, 封面URL={season_info_media['cover']}"
            )
            image_bytes = await fetch_image_bytes(season_info_media["cover"])
            image = BuildImage(background=image_bytes)
            logger.debug(f"成功获取番剧封面: 番剧ID={id_}")
        except Exception as e:
            logger.error(
                f"番剧封面获取失败: 番剧ID={id_}, 异常类型={type(e).__name__}, 异常信息={e}"
            )
            import traceback

            logger.debug(f"异常详细信息:\n{traceback.format_exc()}")

        if image:
            logger.debug(
                f"更新番剧订阅信息: 番剧ID={id_}, 新集数={new_ep}, 更新时间={datetime.now()}"
            )
            await BilibiliSub.sub_handle(
                id_, season_current_episode=new_ep, season_update_time=datetime.now()
            )

            msg_list = [
                image,
                "\n",
                f"[{title}] 更新啦！🎉\n",
                f"最新集数：{new_ep}",
            ]
            logger.info(
                f"番剧更新消息已准备: 番剧ID={id_}, 标题={title}, 新集数={new_ep}"
            )
        else:
            logger.warning(
                f"番剧封面获取失败，无法推送更新消息: 番剧ID={id_}, 标题={title}"
            )
    else:
        logger.debug(f"番剧集数未变化: 番剧ID={id_}, 标题={title}, 集数={new_ep}")

    duration = time.time() - start_time
    if msg_list:
        logger.info(
            f"番剧状态检查完成: 番剧ID={id_}, 检测到更新, 耗时={duration:.2f}秒"
        )
    else:
        logger.debug(
            f"番剧状态检查完成: 番剧ID={id_}, 未检测到更新, 耗时={duration:.2f}秒"
        )

    return msg_list


async def get_user_dynamic(
    uid: int, local_user: BilibiliSub
) -> tuple[bytes | None, int, str]:
    """
    获取用户动态
    :param uid: 用户uid
    :param local_user: 数据库存储的用户数据
    :return: 最新动态截图与时间
    """
    start_time = time.time()
    logger.debug(f"获取用户动态开始: UID={uid}, 用户名={local_user.uname}")

    try:
        logger.debug(f"调用API获取用户动态: UID={uid}")
        dynamic_info = await get_user_dynamics(uid)
        logger.debug(f"成功获取用户动态数据: UID={uid}")
    except Exception as e:
        logger.error(
            f"获取用户动态异常: UID={uid}, 异常类型={type(e).__name__}, 异常信息={e}"
        )
        import traceback

        logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
        return None, 0, ""

    if not dynamic_info:
        logger.warning(f"获取到的动态数据为空: UID={uid}")
        return None, 0, ""

    if not dynamic_info.get("cards"):
        logger.warning(
            f"获取到的动态数据中没有cards字段: UID={uid}, 数据={dynamic_info.keys()}"
        )
        return None, 0, ""

    if not dynamic_info["cards"]:
        logger.debug(f"用户没有动态: UID={uid}")
        return None, 0, ""

    dynamic_upload_time = dynamic_info["cards"][0]["desc"]["timestamp"]
    dynamic_id = dynamic_info["cards"][0]["desc"]["dynamic_id"]
    dynamic_time_str = datetime.fromtimestamp(dynamic_upload_time).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    logger.debug(
        f"最新动态信息: UID={uid}, 动态ID={dynamic_id}, 发布时间={dynamic_time_str}"
    )

    if (
        local_user.dynamic_upload_time is None
        or local_user.dynamic_upload_time < dynamic_upload_time
    ):
        logger.info(
            f"检测到新动态: UID={uid}, 用户名={local_user.uname}, 动态ID={dynamic_id}, 发布时间={dynamic_time_str}"
        )

        try:
            logger.debug(f"开始获取动态截图: UID={uid}, 动态ID={dynamic_id}")
            image = await get_dynamic_screenshot(dynamic_id)
            if image:
                logger.debug(
                    f"成功获取动态截图: UID={uid}, 动态ID={dynamic_id}, 图片大小={len(image)}字节"
                )

                duration = time.time() - start_time
                logger.info(
                    f"获取用户动态完成: UID={uid}, 检测到新动态, 耗时={duration:.2f}秒"
                )

                return (
                    image,
                    dynamic_upload_time,
                    f"https://t.bilibili.com/{dynamic_id}",
                )
            else:
                logger.warning(f"动态截图获取失败: UID={uid}, 动态ID={dynamic_id}")
        except Exception as e:
            logger.error(
                f"获取动态截图异常: UID={uid}, 动态ID={dynamic_id}, 异常类型={type(e).__name__}, 异常信息={e}"
            )
            import traceback

            logger.debug(f"异常详细信息:\n{traceback.format_exc()}")
    else:
        logger.debug(
            f"未检测到新动态: UID={uid}, 最新动态时间={dynamic_time_str}, 本地记录时间={'无记录' if local_user.dynamic_upload_time is None else datetime.fromtimestamp(local_user.dynamic_upload_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    duration = time.time() - start_time
    logger.debug(f"获取用户动态完成: UID={uid}, 未检测到新动态, 耗时={duration:.2f}秒")
    return None, 0, ""


class SubManager:
    def __init__(self):
        self.batch_size = base_config.get("BATCH_SIZE", 5)
        self.current_batch = 0
        self.total_batches = 0
        self.batches = []
        self.last_reload_time = 0

    async def reload_sub_data(self):
        """
        重载数据并分批
        """
        logger.debug("开始重新加载订阅数据并分批")

        live_data, up_data, season_data = await BilibiliSub.get_all_sub_data()

        all_subs = live_data + up_data + season_data
        total_subs = len(all_subs)

        self.total_batches = (total_subs + self.batch_size - 1) // self.batch_size

        self.batches = []
        for i in range(0, total_subs, self.batch_size):
            batch = all_subs[i : i + self.batch_size]
            self.batches.append(batch)

        self.current_batch = 0
        self.last_reload_time = time.time()

        logger.info(
            f"订阅数据重新加载完成: 总订阅数={total_subs}, 批次大小={self.batch_size}, 总批次数={self.total_batches}"
        )

    async def get_next_batch(self) -> list[BilibiliSub]:
        """
        获取下一批次的订阅数据
        :return: 一批订阅数据
        """
        if not self.batches or self.current_batch >= len(self.batches):
            logger.debug(
                f"所有批次已检查完毕或无数据，重新加载: 当前批次={self.current_batch}, 总批次数={len(self.batches) if self.batches else 0}"
            )
            await self.reload_sub_data()

            if not self.batches:
                logger.warning("重新加载后仍然没有订阅数据")
                return []

        current_batch_data = self.batches[self.current_batch]
        logger.info(
            f"获取批次数据: 当前批次={self.current_batch + 1}/{self.total_batches}, 订阅数量={len(current_batch_data)}"
        )

        self.current_batch += 1

        return current_batch_data
