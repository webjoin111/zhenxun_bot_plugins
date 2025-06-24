from datetime import datetime

from tortoise import fields

from zhenxun.services.log import logger
from zhenxun.services.db_context import Model


class BilibiliSub(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    sub_id = fields.CharField(255)
    """订阅id"""
    sub_type = fields.CharField(255)
    """订阅类型"""
    sub_users = fields.TextField()
    """订阅用户"""
    live_short_id = fields.IntField(null=True)
    """直播短id"""
    live_status = fields.IntField(null=True)
    """直播状态 0: 停播  1: 直播"""
    uid = fields.BigIntField(null=True)
    """主播/UP UID"""
    uname = fields.CharField(255, null=True)
    """主播/UP 名称"""
    latest_video_created = fields.BigIntField(null=True)
    """最后视频上传时间"""
    dynamic_upload_time = fields.BigIntField(null=True, default=0)
    """动态发布时间"""
    season_name = fields.CharField(255, null=True)
    """番剧名称"""
    season_id = fields.IntField(null=True)
    """番剧id"""
    season_current_episode = fields.CharField(255, null=True)
    """番剧最新集数"""
    season_update_time = fields.DateField(null=True)
    """番剧更新日期"""

    class Meta:
        table = "bilibili_sub"
        table_description = "B站订阅数据表"
        unique_together = ("sub_id", "sub_type")

    @classmethod
    async def sub_handle(
        cls,
        sub_id: int,
        sub_type: str | None = None,
        sub_user: str = "",
        *,
        live_short_id: int | None = None,
        live_status: int | None = None,
        dynamic_upload_time: int = 0,
        uid: int | None = None,
        uname: str | None = None,
        latest_video_created: int | None = None,
        season_name: str | None = None,
        season_id: int | None = None,
        season_current_episode: str | None = None,
        season_update_time: datetime | None = None,
    ) -> bool:
        """
        说明:
            添加订阅
        参数:
            :param sub_id: 订阅名称，房间号，番剧号等
            :param sub_type: 订阅类型
            :param sub_user: 订阅此条目的用户
            :param live_short_id: 直接短 id
            :param live_status: 主播开播状态
            :param dynamic_upload_time: 主播/UP最新动态时间
            :param uid: 主播/UP uid
            :param uname: 用户名称
            :param latest_video_created: 最新视频上传时间
            :param season_name: 番剧名称
            :param season_id: 番剧 season_id
            :param season_current_episode: 番剧最新集数
            :param season_update_time: 番剧更新时间
        """
        data = {
            "sub_type": sub_type,
            "live_short_id": live_short_id,
            "live_status": live_status,
            "dynamic_upload_time": dynamic_upload_time,
            "uid": uid,
            "uname": uname,
            "latest_video_created": latest_video_created,
            "season_name": season_name,
            "season_id": season_id,
            "season_current_episode": season_current_episode,
            "season_update_time": season_update_time,
        }

        if sub_user:
            sub_user_formatted = sub_user if sub_user.endswith(",") else f"{sub_user},"
        else:
            sub_user_formatted = ""

        sub = None
        if sub_type:
            sub = await cls.get_or_none(sub_id=sub_id, sub_type=sub_type)
        else:
            sub = await cls.get_or_none(sub_id=sub_id)

        if sub:
            current_sub_users = sub.sub_users or ""
            if sub_user_formatted and sub_user_formatted not in current_sub_users:
                data["sub_users"] = current_sub_users + sub_user_formatted
            else:
                data["sub_users"] = current_sub_users

            data["sub_type"] = sub_type or sub.sub_type
            data["live_short_id"] = (
                live_short_id if live_short_id is not None else sub.live_short_id
            )
            data["live_status"] = (
                live_status if live_status is not None else sub.live_status
            )
            data["dynamic_upload_time"] = (
                dynamic_upload_time
                if dynamic_upload_time != 0
                else sub.dynamic_upload_time
            )
            data["uid"] = uid if uid is not None else sub.uid
            data["uname"] = uname if uname is not None else sub.uname
            data["latest_video_created"] = (
                latest_video_created
                if latest_video_created is not None
                else sub.latest_video_created
            )
            data["season_name"] = (
                season_name if season_name is not None else sub.season_name
            )
            data["season_id"] = season_id if season_id is not None else sub.season_id
            data["season_current_episode"] = (
                season_current_episode
                if season_current_episode is not None
                else sub.season_current_episode
            )
            data["season_update_time"] = (
                season_update_time
                if season_update_time is not None
                else sub.season_update_time
            )

            final_data_for_update = {k: v for k, v in data.items()}

        else:
            if not sub_type:
                logger.error(
                    f"sub_handle: sub_type is required for new subscription with sub_id {sub_id}"
                )
                return False
            final_data_for_update = {k: v for k, v in data.items() if v is not None}
            final_data_for_update["sub_id"] = sub_id
            final_data_for_update["sub_type"] = sub_type
            final_data_for_update["sub_users"] = sub_user_formatted

        lookup_keys = {"sub_id": str(sub_id)}
        if sub_type:
            lookup_keys["sub_type"] = sub_type

        update_defaults = {
            k: v for k, v in final_data_for_update.items() if k not in lookup_keys
        }

        await cls.update_or_create(**lookup_keys, defaults=update_defaults)
        return True

    @classmethod
    async def delete_bilibili_sub(
        cls, sub_id: int, sub_user: str, sub_type: str | None = None
    ) -> bool:
        """
        说明:
            删除订阅
        参数:
            :param sub_id: 订阅名称
            :param sub_user: 删除此条目的用户
        """
        try:
            sub_user_formatted = sub_user if sub_user.endswith(",") else f"{sub_user},"
            if sub_type:
                sub = await cls.filter(
                    sub_id=sub_id,
                    sub_type=sub_type,
                    sub_users__contains=sub_user_formatted,
                ).first()
            else:
                sub = await cls.filter(
                    sub_id=sub_id, sub_users__contains=sub_user_formatted
                ).first()
            if not sub:
                return False
            sub.sub_users = sub.sub_users.replace(sub_user_formatted, "")
            if sub.sub_users.strip():
                await sub.save(update_fields=["sub_users"])
            else:
                await sub.delete()
            return True
        except Exception as e:
            logger.info(f"bilibili_sub 删除订阅错误 {type(e)}: {e}")
        return False

    @classmethod
    async def get_all_sub_data(
        cls,
    ) -> tuple[list["BilibiliSub"], list["BilibiliSub"], list["BilibiliSub"]]:
        """
        说明:
            分类获取所有数据
        """
        live_data = []
        up_data = []
        season_data = []
        query = await cls.all()
        for x_item in query:
            if x_item.sub_type == "live":
                live_data.append(x_item)
            if x_item.sub_type == "up":
                up_data.append(x_item)
            if x_item.sub_type == "season":
                season_data.append(x_item)
        return live_data, up_data, season_data

    @classmethod
    async def _run_script(cls):
        return [
            "ALTER TABLE bilibili_sub ALTER COLUMN season_update_time TYPE timestamp with time zone USING season_update_time::timestamp with time zone;",
            "ALTER TABLE bilibili_sub ALTER COLUMN sub_id TYPE character varying(255);",
        ]
