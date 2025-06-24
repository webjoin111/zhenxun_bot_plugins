import datetime
import traceback
from io import BytesIO

from bilibili_api import user as bilibili_user_module
from bilibili_api import bangumi as bilibili_bangumi_module
from bilibili_api import live as bilibili_live_module
from bilibili_api import Credential as BilibiliCredential

from nonebot_plugin_htmlrender import get_new_page

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.image_utils import BuildImage
from zhenxun.configs.path_config import IMAGE_PATH

from .config import get_credential

BORDER_PATH = IMAGE_PATH / "border"
BORDER_PATH.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://api.bilibili.com"


async def get_pic(url: str) -> bytes:
    """
    获取图像
    :param url: 图像链接
    :return: 图像二进制
    """
    return (await AsyncHttpx.get(url, timeout=10)).content


async def create_live_des_image(uid: int, title: str, cover: str, tags: str, des: str):
    """
    生成主播简介图片
    :param uid: 主播 uid
    :param title: 直播间标题
    :param cover: 直播封面
    :param tags: 直播标签
    :param des: 直播简介
    :return:
    """
    credential = get_credential()
    if not credential:
        logger.warning(
            "create_live_des_image: No credential available for get_user_info"
        )
        return

    user_instance = bilibili_user_module.User(uid=uid, credential=credential)
    user_info = await user_instance.get_user_info()

    user_name = user_info.get("name", "未知用户")
    user_sex = user_info.get("sex", "保密")
    face_url = user_info.get("face", "")
    user_sign = user_info.get("sign", "")

    if not face_url:
        logger.warning(f"create_live_des_image: Face URL not found for UID {uid}")
        return

    ava = BuildImage(100, 100, background=BytesIO(await get_pic(face_url)))
    await ava.circle()
    cover_img = BuildImage(470, 265, background=BytesIO(await get_pic(cover)))


def _create_live_des_image(
    title: str,
    cover: BuildImage,
    tags: str,
    des: str,
    user_name: str,
    sex: str,
    sign: str,
    ava: BuildImage,
):
    """
    生成主播简介图片
    :param title: 直播间标题
    :param cover: 直播封面
    :param tags: 直播标签
    :param des: 直播简介
    :param user_name: 主播名称
    :param sex: 主播性别
    :param sign: 主播签名
    :param ava: 主播头像
    :return:
    """
    border = BORDER_PATH / "0.png"
    if border.exists():
        BuildImage(1772, 2657, background=border)
    bk = BuildImage(1772, 2657, font_size=30)
    bk.paste(cover, (0, 100), center_type="by_width")


async def get_meta(media_id: int, auth: BilibiliCredential | None = None, **kwargs):
    """
    根据番剧 ID 获取番剧元数据信息
    MODIFIED: Uses module.Class syntax
    """
    credential = auth or get_credential()
    bangumi_instance = bilibili_bangumi_module.Bangumi(
        media_id=media_id, credential=credential
    )
    return await bangumi_instance.get_meta()


async def get_videos(uid: int, auth: BilibiliCredential | None = None, **kwargs):
    """
    获取用户投搞视频信息
    MODIFIED: Uses module.Class syntax
    """
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=uid, credential=credential)
    return await user_instance.get_videos(**kwargs)


async def get_user_card(
    mid: int, photo: bool = False, auth: BilibiliCredential | None = None, **kwargs
):
    """
    获取用户卡片信息
    MODIFIED: Uses module.Class syntax
    """
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=mid, credential=credential)
    user_info = await user_instance.get_user_info()
    return user_info


async def get_user_dynamics(
    uid: int,
    offset: str = "0",
    need_top: bool = False,
    auth: BilibiliCredential | None = None,
    **kwargs,
):
    """
    获取指定用户历史动态
    MODIFIED: Uses module.Class syntax
    """
    credential = auth or get_credential()
    user_instance = bilibili_user_module.User(uid=uid, credential=credential)
    return await user_instance.get_dynamics(offset=offset, **kwargs)


async def get_room_info_by_id(
    live_id: int, auth: BilibiliCredential | None = None, **kwargs
):
    """
    根据房间号获取指定直播间信息
    MODIFIED: Uses module.Class syntax
    """
    credential = auth or get_credential()
    liveroom_instance = bilibili_live_module.LiveRoom(
        room_display_id=live_id, credential=credential
    )
    return await liveroom_instance.get_room_info()


async def get_dynamic_screenshot(dynamic_id: int) -> bytes | None:
    url = f"https://t.bilibili.com/{dynamic_id}"
    try:
        async with get_new_page(
            viewport={"width": 2000, "height": 1000},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            device_scale_factor=3,
        ) as page:
            credential = get_credential()
            if credential:
                try:
                    cookies = credential.get_cookies()
                    if cookies:
                        await page.context.add_cookies(
                            [
                                {
                                    "domain": ".bilibili.com",
                                    "name": name,
                                    "path": "/",
                                    "value": value,
                                }
                                for name, value in cookies.items()
                            ]
                        )
                except Exception as e:
                    logger.warning(f"获取 cookies 失败: {e}")
            await page.goto(url, wait_until="networkidle")
            if page.url == "https://www.bilibili.com/404":
                logger.warning(f"动态 {dynamic_id} 不存在")
                return None
            await page.wait_for_load_state(state="domcontentloaded")
            card = await page.query_selector(".card")
            assert card
            clip = await card.bounding_box()
            assert clip
            bar = await page.query_selector(".bili-tabs__header")
            assert bar
            bar_bound = await bar.bounding_box()
            assert bar_bound
            clip["height"] = bar_bound["y"] - clip["y"]
            return await page.screenshot(clip=clip, full_page=True)
    except Exception:
        logger.warning(
            f"Error in get_dynamic_screenshot({url}): {traceback.format_exc()}"
        )
    return None


def calc_time_total(t: float):
    """
    Calculate the total time in a human-readable format.
    Args:
    t (float | int): The time in seconds.
    Returns:
    str: The total time in a human-readable format.
    Example:
    >>> calc_time_total(4.5)
    '4500 毫秒'
    >>> calc_time_total(3600)
    '1 小时'
    >>> calc_time_total(3660)
    '1 小时 1 分钟'
    """
    if not isinstance(t, (int, float)):
        try:
            t = float(t)
        except (ValueError, TypeError):
            return "时间格式错误"

    t_int = int(t * 1000)
    if t_int < 5000:
        return f"{t_int} 毫秒"
    timedelta_obj = datetime.timedelta(seconds=t_int // 1000)
    day = timedelta_obj.days
    hour, mint, sec = tuple(
        int(n) for n in str(timedelta_obj).split(",")[-1].split(":")
    )
    total = ""
    if day:
        total += f"{day} 天 "
    if hour:
        total += f"{hour} 小时 "
    if mint:
        total += f"{mint} 分钟 "
    if sec and not day and not hour:
        total += f"{sec} 秒 "
    return total.strip()
