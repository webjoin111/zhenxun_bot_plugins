import asyncio
from pathlib import Path
from typing import List, Optional, Union

from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.services.log import logger

from ..models import PixivGallery
from ..config import base_config
from ..utils import get_temp_image_path


class DownloadManager:
    """下载管理类"""

    @classmethod
    async def download_image(
        cls, pix: PixivGallery, is_original: bool = False, max_retries: int = 3
    ) -> Optional[Path]:
        """下载图片

        参数:
            pix: 图片数据
            is_original: 是否获取原图
            max_retries: 最大重试次数

        返回:
            Optional[Path]: 图片路径
        """
        image_size = base_config.get("PIX_IMAGE_SIZE")
        timeout = base_config.get("TIMEOUT")
        nginx_urls = base_config.get("PIXIV_NGINX_URL")
        small_url = base_config.get("PIXIV_SMALL_NGINX_URL")

        if isinstance(nginx_urls, list):
            if (
                nginx_urls
                and len(nginx_urls) > 0
                and all(isinstance(c, str) and len(c) == 1 for c in nginx_urls)
            ):
                nginx_urls = ["".join(nginx_urls)]
                logger.debug(f"检测到NGINX_URL配置为字符列表，已修正为: {nginx_urls}")
        else:
            nginx_urls = [nginx_urls] if nginx_urls else []

        if image_size in pix.image_urls:
            original_url = pix.image_urls[image_size]
        else:
            key = next(iter(pix.image_urls.keys()))
            original_url = pix.image_urls[key]

        file_path = Path(get_temp_image_path(pix.pid))
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.6; rv:2.0.1) Gecko/20100101 Firefox/4.0.1",
            "Referer": "https://www.pixiv.net/",
        }

        urls_to_try = []

        original_domain_url = original_url

        if nginx_urls:
            for nginx_url in nginx_urls:
                if "limit_sanity_level" in original_url or (is_original and nginx_url):
                    image_type = original_url.split(".")[-1]
                    if pix.is_multiple:
                        proxy_url = f"https://{nginx_url}/{pix.pid}-{int(pix.img_p) + 1}.{image_type}"
                    else:
                        proxy_url = f"https://{nginx_url}/{pix.pid}.{image_type}"
                    urls_to_try.append(proxy_url)

        if small_url:
            small_domain_url = original_url
            if "img-master" in small_domain_url:
                small_domain_url = (
                    "img-master" + small_domain_url.split("img-master")[-1]
                )
            elif "img-original" in small_domain_url:
                small_domain_url = (
                    "img-original" + small_domain_url.split("img-original")[-1]
                )
            small_proxy_url = f"https://{small_url}/{small_domain_url}"
            urls_to_try.append(small_proxy_url)

        if not urls_to_try:
            urls_to_try.append(original_url)

        logger.debug(f"图片 {pix.pid} 备选URL数量: {len(urls_to_try)}")

        retries = 0
        url_index = 0

        while retries <= max_retries:
            current_url = urls_to_try[url_index % len(urls_to_try)]

            try:
                if retries > 0:
                    retry_delay = 1 + retries * 0.5
                    logger.info(
                        f"下载 {current_url} 第{retries}次重试，等待{retry_delay}秒..."
                    )
                    await asyncio.sleep(retry_delay)

                success = await AsyncHttpx.download_file(
                    current_url, file_path, headers=headers, timeout=timeout
                )

                if success:
                    logger.info(f"下载 {current_url} 成功.. Path：{file_path}")
                    return file_path
                else:
                    logger.error(f"下载图片失败: {pix.pid} URL: {current_url}")
                    url_index += 1
                    if url_index % len(urls_to_try) == 0:
                        retries += 1

            except Exception as e:
                logger.error(
                    f"下载 [{current_url}] 错误 Path：{file_path} || 错误 {type(e).__name__}: {str(e)}"
                )
                url_index += 1
                if url_index % len(urls_to_try) == 0:
                    retries += 1

                if retries > max_retries:
                    logger.error(
                        f"下载图片失败: {pix.pid} (已尝试所有反代地址并重试{max_retries}次)"
                    )
                    return None

        return None

    @classmethod
    async def format_image_info(
        cls, pix: PixivGallery, show_info: bool = True
    ) -> List[Union[str, Path]]:
        """格式化图片信息和内容

        参数:
            pix: 图片数据
            show_info: 是否显示详细信息

        返回:
            List[Union[str, Path]]: 消息内容列表
        """
        message_list = []

        if show_info:
            tags_display = pix.tags.replace(",", ", ")

            message_list.append(
                f"title: {pix.title}\n"
                f"author: {pix.author}\n"
                f"pid: {pix.pid}-{pix.img_p}\n"
                f"uid: {pix.uid}\n"
                f"nsfw: {pix.nsfw_tag}\n"
                f"收藏数: {pix.total_bookmarks}\n"
                f"tags: {tags_display}"
            )

        image_path = await cls.download_image(pix)
        if image_path:
            message_list.append(image_path)
        else:
            message_list.append(f"获取图片 pid: {pix.pid} 失败...")

        return message_list
