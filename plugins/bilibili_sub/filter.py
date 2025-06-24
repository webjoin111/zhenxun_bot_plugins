import asyncio
import re
from playwright.async_api import async_playwright
from zhenxun.services.log import logger

from .config import get_credential
from .utils import get_user_dynamics


async def check_dynamic_content_api(uid: int, dynamic_id: str) -> bool:
    """
    使用 bilibili-api 检查动态内容是否包含广告
    :param uid: 用户ID
    :param dynamic_id: 动态ID
    :return: True表示包含广告，应该过滤
    """
    try:
        logger.info(f"[广告过滤-API] 开始检查动态: UID={uid}, 动态ID={dynamic_id}")

        # 获取用户动态数据
        logger.debug(f"[广告过滤-API] 正在获取用户动态数据: UID={uid}")
        dynamics_data = await get_user_dynamics(uid)
        if not dynamics_data or not dynamics_data.get("cards"):
            logger.warning(f"[广告过滤-API] 未获取到动态数据: UID={uid}, 数据为空或无cards字段")
            return False

        logger.debug(f"[广告过滤-API] 成功获取动态数据: UID={uid}, 动态数量={len(dynamics_data.get('cards', []))}")

        # 查找指定的动态
        logger.debug(f"[广告过滤-API] 正在查找指定动态: UID={uid}, 动态ID={dynamic_id}")
        target_dynamic = None
        available_ids = []
        for card in dynamics_data["cards"]:
            card_dynamic_id = str(card["desc"]["dynamic_id"])
            available_ids.append(card_dynamic_id)
            if card_dynamic_id == str(dynamic_id):
                target_dynamic = card
                break

        if not target_dynamic:
            logger.warning(f"[广告过滤-API] 未找到指定动态: UID={uid}, 动态ID={dynamic_id}, 可用动态ID={available_ids[:5]}...")
            return False

        logger.debug(f"[广告过滤-API] 成功找到目标动态: UID={uid}, 动态ID={dynamic_id}")

        # 检查动态类型
        dynamic_type = target_dynamic["desc"].get("type", 0)
        logger.debug(f"[广告过滤-API] 动态类型检查: UID={uid}, 动态ID={dynamic_id}, 类型={dynamic_type}")

        # 商品相关的动态类型
        goods_types = {
            19: "商品分享",
            64: "专栏文章（可能包含商品）",
        }

        if dynamic_type in goods_types:
            logger.warning(f"[广告过滤-API] 检测到商品类型动态: UID={uid}, 动态ID={dynamic_id}, 类型={dynamic_type}({goods_types[dynamic_type]})")
            return True

        logger.debug(f"[广告过滤-API] 动态类型检查通过: UID={uid}, 动态ID={dynamic_id}, 类型={dynamic_type}")

        # 检查动态内容
        logger.debug(f"[广告过滤-API] 开始检查动态内容: UID={uid}, 动态ID={dynamic_id}")
        card_data = target_dynamic.get("card", "")
        if isinstance(card_data, str):
            try:
                import json
                card_json = json.loads(card_data)
                logger.debug(f"[广告过滤-API] 成功解析动态卡片JSON: UID={uid}, 动态ID={dynamic_id}")
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"[广告过滤-API] 动态卡片JSON解析失败: UID={uid}, 动态ID={dynamic_id}, 错误={e}")
                card_json = {}
        else:
            card_json = card_data
            logger.debug(f"[广告过滤-API] 动态卡片数据为字典格式: UID={uid}, 动态ID={dynamic_id}")

        # 检查动态文本内容
        text_content = ""
        content_sources = []

        # 获取动态描述文本
        if "item" in card_json:
            item = card_json["item"]
            if "description" in item:
                text_content += item["description"]
                content_sources.append("item.description")
            if "content" in item:
                text_content += item["content"]
                content_sources.append("item.content")

        # 获取用户发布的文本
        if "user" in card_json and "description" in card_json["user"]:
            text_content += card_json["user"]["description"]
            content_sources.append("user.description")

        logger.debug(f"[广告过滤-API] 提取文本内容: UID={uid}, 动态ID={dynamic_id}, 来源={content_sources}, 长度={len(text_content)}")

        # 检查是否包含商品相关关键词
        logger.debug(f"[广告过滤-API] 开始关键词检查: UID={uid}, 动态ID={dynamic_id}")
        ad_keywords = [
            "商品", "购买", "链接", "店铺", "优惠", "折扣", "带货", "种草",
            "好物", "推荐", "下单", "抢购", "限时", "特价", "促销",
            "¥", "￥", "元", "价格", "原价", "现价", "到手价",
            "淘宝", "天猫", "京东", "拼多多", "抖音", "小红书",
            "直播间", "橱窗", "购物车", "加购", "收藏"
        ]

        text_lower = text_content.lower()
        found_keywords = []
        for keyword in ad_keywords:
            if keyword in text_content or keyword.lower() in text_lower:
                found_keywords.append(keyword)

        if found_keywords:
            logger.warning(f"[广告过滤-API] 检测到广告关键词: UID={uid}, 动态ID={dynamic_id}, 关键词={found_keywords}")
            return True

        logger.debug(f"[广告过滤-API] 关键词检查通过: UID={uid}, 动态ID={dynamic_id}")

        # 检查是否包含商品卡片或链接
        logger.debug(f"[广告过滤-API] 开始商品卡片检查: UID={uid}, 动态ID={dynamic_id}")
        goods_fields = []
        if "goods" in card_json:
            goods_fields.append("goods")
        if "mall" in card_json:
            goods_fields.append("mall")

        if goods_fields:
            logger.warning(f"[广告过滤-API] 检测到商品卡片: UID={uid}, 动态ID={dynamic_id}, 字段={goods_fields}")
            return True

        logger.debug(f"[广告过滤-API] 商品卡片检查通过: UID={uid}, 动态ID={dynamic_id}")

        # 检查URL中是否包含商品链接
        logger.debug(f"[广告过滤-API] 开始商品链接检查: UID={uid}, 动态ID={dynamic_id}")
        url_patterns = {
            r"item\.taobao\.com": "淘宝商品",
            r"detail\.tmall\.com": "天猫商品",
            r"item\.jd\.com": "京东商品",
            r"yangkeduo\.com": "拼多多商品",
            r"haohuo\.jinritemai\.com": "抖音好货",
        }

        for pattern, platform in url_patterns.items():
            if re.search(pattern, text_content, re.IGNORECASE):
                logger.warning(f"[广告过滤-API] 检测到商品链接: UID={uid}, 动态ID={dynamic_id}, 平台={platform}, 模式={pattern}")
                return True

        logger.debug(f"[广告过滤-API] 商品链接检查通过: UID={uid}, 动态ID={dynamic_id}")
        logger.info(f"[广告过滤-API] 动态内容检查完成，未发现广告: UID={uid}, 动态ID={dynamic_id}")
        return False

    except Exception as e:
        logger.error(f"[广告过滤-API] API方式检查动态内容失败: UID={uid}, 动态ID={dynamic_id}, 错误类型={type(e).__name__}, 错误={e}")
        import traceback
        logger.debug(f"[广告过滤-API] 详细错误信息:\n{traceback.format_exc()}")
        return False  # 出错时不过滤，避免误杀



async def check_dynamic_hybrid(uid: int, dynamic_id: str, url: str) -> bool:
    """
    混合方案：优先使用API检测，如果API检测失败则使用原始页面检测
    :param uid: 用户ID
    :param dynamic_id: 动态ID
    :param url: 动态URL
    :return: True表示包含广告，应该过滤
    """
    # 首先尝试API检测
    logger.info(f"[广告过滤-混合] 开始混合检测: UID={uid}, 动态ID={dynamic_id}, URL={url}")

    try:
        logger.debug(f"[广告过滤-混合] 第一阶段：尝试API检测: UID={uid}, 动态ID={dynamic_id}")
        api_result = await check_dynamic_content_api(uid, dynamic_id)
        if api_result:
            logger.warning(f"[广告过滤-混合] API检测发现广告，过滤动态: UID={uid}, 动态ID={dynamic_id}")
            return True

        # API检测通过，记录并返回
        logger.info(f"[广告过滤-混合] API检测通过，动态无广告: UID={uid}, 动态ID={dynamic_id}")
        return False

    except Exception as e:
        # 如果API检测失败，回退到原始页面检测
        logger.warning(f"[广告过滤-混合] API检测失败，回退到页面检测: UID={uid}, 动态ID={dynamic_id}, 错误类型={type(e).__name__}, 错误={e}")
        try:
            logger.debug(f"[广告过滤-混合] 第二阶段：尝试页面检测: UID={uid}, 动态ID={dynamic_id}")
            page_result = await check_page_elements(url)
            if page_result:
                logger.warning(f"[广告过滤-混合] 页面检测发现广告，过滤动态: UID={uid}, 动态ID={dynamic_id}")
                return True
            else:
                logger.info(f"[广告过滤-混合] 页面检测通过，动态无广告: UID={uid}, 动态ID={dynamic_id}")
                return False
        except Exception as page_e:
            logger.error(f"[广告过滤-混合] 页面检测也失败，不过滤动态: UID={uid}, 动态ID={dynamic_id}, 错误类型={type(page_e).__name__}, 错误={page_e}")
            import traceback
            logger.debug(f"[广告过滤-混合] 页面检测详细错误信息:\n{traceback.format_exc()}")
            return False  # 两种检测都失败时，不过滤


async def check_page_elements(url):
    """
    使用无头浏览器和 Cookie 检查页面中的元素是否被拦截，并导出所有页面元素。

    :param url: 要检查的页面 URL
    :return: 是否包含被拦截的元素
    """
    try:
        logger.info(f"[广告过滤-页面] 开始页面检测: URL={url}")
        cookies = []

        # 使用新的 credential 系统获取 cookies
        logger.debug(f"[广告过滤-页面] 正在获取登录凭证: URL={url}")
        credential = get_credential()
        if credential:
            try:
                credential_cookies = credential.get_cookies()
                if credential_cookies:
                    cookies = [
                        {"name": k, "value": v, "domain": ".bilibili.com", "path": "/"}
                        for k, v in credential_cookies.items()
                    ]
                    logger.debug(f"[广告过滤-页面] 成功获取 {len(cookies)} 个cookies: URL={url}")
            except Exception as e:
                logger.warning(f"[广告过滤-页面] 从 credential 获取 cookies 失败: URL={url}, 错误={e}")

        if not cookies:
            logger.warning(f"[广告过滤-页面] 未获取到有效的 cookies，可能影响检测准确性: URL={url}")
        else:
            logger.debug(f"[广告过滤-页面] 将使用登录状态进行检测: URL={url}")

        for cookie in cookies:
            if (
                "sameSite" in cookie
                and isinstance(cookie["sameSite"], str)
                and cookie.get("sameSite", "").lower() == "unspecified"
            ):
                cookie["sameSite"] = "Lax"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            class_names = [
                "opus-text-rich-hl",
                "goods-shop",
                "bili-dyn-card-goods",
                "dyn-goods",
                "dyn-goods__mark",
            ]
            max_attempts = 3
            attempt = 0

            while attempt < max_attempts:
                await page.goto(url)
                await page.wait_for_load_state("networkidle")

                found_blocked_element = False
                found_elements = []
                for class_name in class_names:
                    element_count = await page.locator(f".{class_name}").count()
                    if element_count > 0:
                        logger.debug(f"[广告过滤-页面] 发现广告元素: {class_name}, 数量={element_count}, URL={url}")
                        found_elements.append(f"{class_name}({element_count})")
                        found_blocked_element = True

                if found_blocked_element:
                    attempt += 1
                    logger.warning(f"[广告过滤-页面] 检测到广告元素: {found_elements}, 尝试次数: {attempt}/{max_attempts}, URL={url}")
                    if attempt < max_attempts:
                        logger.debug(f"[广告过滤-页面] 等待1秒后重新检测: URL={url}")
                        await asyncio.sleep(1)
                        continue
                    else:
                        logger.warning(f"[广告过滤-页面] 多次确认页面包含广告元素: {found_elements}, URL={url}")
                        await browser.close()
                        return True
                else:
                    logger.info(f"[广告过滤-页面] 页面检测通过，未发现广告元素: URL={url}")
                    await browser.close()
                    return False

            logger.warning(f"[广告过滤-页面] 三次尝试均检测到广告元素，最终判定为包含广告: URL={url}")
            await browser.close()
            return True

    except Exception as e:
        logger.error(f"[广告过滤-页面] 页面检查失败: URL={url}, 错误类型={type(e).__name__}, 错误={e}")
        import traceback
        logger.debug(f"[广告过滤-页面] 详细错误信息:\n{traceback.format_exc()}")
        return False


async def main():
    url = input("请输入要检查的页面 URL: ")
    result = await check_page_elements(url)
    print(f"页面是否包含被拦截的元素 (True表示包含，将被过滤): {result}")


if __name__ == "__main__":
    asyncio.run(main())
