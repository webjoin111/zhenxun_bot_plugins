from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Alconna, on_alconna

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.utils.enum import PluginType

from .config import base_config

__plugin_meta__ = PluginMetadata(
    name="Pixiv",
    description="Pixiv图库搜索与管理，支持标签翻译、高级搜索及内容过滤",
    usage="""📔 Pixiv图库使用帮助

基础指令：
  .pix [标签...] [-n 数量] [-r] [-s] [-ai] [--options]
    标签: 支持多标签搜索，自动翻译
    -n/--num: 设置返回数量(最多10张)
    -r/--r18: 搜索R18图片
    -s/--setu: 搜索R18图片(同-r)
    -ai/--ai: 包含AI生成图片(默认不包含)

高级选项：
  --bookmarks: 最低收藏数
  --date: 限制最近天数
  --mode: 标签匹配模式(AND/OR)
  --sort: 排序方式(bookmarks/date/random)
  --translate: 启用标签翻译
  --no-translate: 禁用标签翻译
  --uid: 按用户ID搜索
  --pid: 按作品ID搜索


图片操作：
  引用图片 + /original: 获取原图
  引用图片 + /info [序号]: 查看图片详细信息
  引用图片 + /block [序号] [原因]: 将图片加入黑名单

关键词管理：
  .pix添加 [u/p/k/b] [内容...]: 添加UID/PID/关键词/黑名单
    u: 添加用户UID
    p: 添加作品PID
    k: 添加关键词
    b: 添加PID到黑名单
  .pix处理 [a/f/i/b] [id]: 处理关键词
    a: 通过(PASS)
    f: 未通过(FAIL)
    i: 忽略(IGNORE)
    b: 黑名单(BLACK)
  .pix查看 [u/p/k/a]: 查看关键词列表

收录管理：
  .pix收录 [u/p/k/a] [数量]: 更新图库数据
    u: 收录UID
    p: 收录PID
    k: 收录关键词
    a: 收录所有类型
    -f/--force: 强制更新已收录数据
    -a/--all: 处理所有关键词，包括已收录的
    -n/--max-works: 仅收录作品数量小于指定值的作者(对UID有效)
    -c/--continue: 从上次收录的页码继续(对UID有效)
    -m/--mark: 自定义收藏数阈值，如 -m 500 (忽略配置中的限制)
  .pix停止收录: 停止正在进行的收录任务
  .pix收录状态: 查看当前收录任务状态

黑名单管理：
  .pix黑名单添加 [u/p/k] [内容] [原因]: 添加黑名单
  .pix黑名单移除 [u/p/k] [内容]: 移除黑名单
  .pix黑名单查看 [u/p/k]: 查看黑名单列表

配置管理：
  .pix配置查看: 查看所有配置项
  .pix配置设置 [配置项] [值]: 设置配置项
  .pix配置重置 [配置项]: 重置配置项为默认值

统计功能：
  .pix图库 [标签...]: 查看图库统计信息

示例:
  .pix 萝莉 白丝 -n 3
  .pix 原神 --bookmarks 10000
  .pix添加 u 38297201 30837811 45897196
  .pix收录 u -m 500 -c   # 继续收录UID作品，收藏数>500
  引用图片 + /info
""",
    extra=PluginExtraData(
        author="HibiKier",
        version="0.3.2",
        plugin_type=PluginType.NORMAL,
        configs=[
            RegisterConfig(
                module="pixiv",
                key="PIX_IMAGE_SIZE",
                value="master",
                help="Pixiv图库下载的画质 可能的值：original：原图，master：缩略图（加快发送速度）",
                default_value="master",
            ),
            RegisterConfig(
                module="pixiv",
                key="SEARCH_HIBIAPI_BOOKMARKS",
                value=5000,
                help="最低收藏，Pixiv使用HIBIAPI搜索图片时达到最低收藏才会添加至图库",
                default_value=5000,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="SEARCH_HIBIAPI_AI_BOOKMARKS",
                value=5000,
                help="AI图片最低收藏，Pixiv使用HIBIAPI搜索AI生成图片时达到最低收藏才会添加至图库",
                default_value=5000,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="WITHDRAW_PIX_MESSAGE",
                value=(0, 1),
                help="自动撤回，参1：延迟撤回色图时间(秒)，0 为关闭 | 参2：监控聊天类型，0(私聊) 1(群聊) 2(群聊+私聊)",
                default_value=(0, 1),
                type=tuple,
            ),
            RegisterConfig(
                module="pixiv",
                key="TIMEOUT",
                value=10,
                help="下载图片超时限制（秒）",
                default_value=10,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="SHOW_INFO",
                value=True,
                help="是否显示图片的基本信息，如PID等",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="MAX_ONCE_NUM2FORWARD",
                value=5,
                help="单次发送的图片数量达到指定值时转发为合并消息",
                default_value=5,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="ALLOW_GROUP_SETU",
                value=False,
                help="允许非超级用户使用-s参数",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="ALLOW_GROUP_R18",
                value=False,
                help="允许非超级用户使用-r参数",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="MAX_AUTHOR_PAGES",
                value=50,
                help="单个作者最大收录页数，一页约30张作品，设为0表示无限制",
                default_value=50,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="MAX_IMAGE_PAGES",
                value=60,
                help="单个插画最大收录页数，超过此页数将跳过收录",
                default_value=60,
                type=int,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_SHOW_AI",
                value=False,
                help="默认是否显示AI生成图片",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_TAG_TRANSLATE",
                value=False,
                help="默认是否翻译标签",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_TAG_EXPAND",
                value=True,
                help="默认是否扩展标签搜索",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_TAG_SEARCH_MODE",
                value="AND",
                help="默认标签搜索模式(AND/OR)",
                default_value="AND",
                type=str,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_SORT_BY",
                value="bookmarks",
                help="默认排序方式",
                default_value="bookmarks",
                type=str,
            ),
            RegisterConfig(
                module="pixiv",
                key="DEFAULT_SORT_ORDER",
                value="desc",
                help="默认排序顺序",
                default_value="desc",
                type=str,
            ),
            RegisterConfig(
                module="pixiv",
                key="ENABLE_CONTENT_FILTER",
                value=True,
                help="是否启用内容过滤",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="LIMIT_RANDOM_RESULTS",
                value=True,
                help="随机搜索时是否限制结果集为前1000条（关闭此选项可确保所有图片都有机会展示，但可能影响性能）",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="pixiv",
                key="PIXIV_NGINX_URL",
                value=["pximg.re"],
                help="Pixiv反向代理地址，支持多个地址(列表)，按顺序尝试",
                default_value=["pximg.re"],
                type=list,
            ),
            RegisterConfig(
                module="pixiv",
                key="PIXIV_SMALL_NGINX_URL",
                value=None,
                help="Pixiv备用反向代理地址",
                default_value=None,
            ),
            RegisterConfig(
                module="pixiv",
                key="HIBIAPI_URL",
                value="https://api.obfs.dev",
                help="Pixiv API 接口地址，用于获取图片数据",
                default_value="https://api.obfs.dev",
            ),
        ],
    ).to_dict(),
)

from .commands import search_cmd, manage_cmd, config_cmd
