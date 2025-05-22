"""
标签翻译服务，支持多语言标签互译。
"""
import json
import aiohttp
from typing import Dict, List, Set, Optional
from pathlib import Path

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from ..utils import detect_language

# 创建标签数据存储目录
TAG_DATA_PATH = DATA_PATH / "pix" / "tag_data"
TAG_DATA_PATH.mkdir(parents=True, exist_ok=True)
TAG_MAPPING_FILE = TAG_DATA_PATH / "tag_mapping.json"


class TagTranslator:
    """标签翻译服务类"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TagTranslator, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # 标签映射: {"en": {"cat": {"jp": "猫", "cn": "猫"}, ...}, "jp": {...}, "cn": {...}}
        self.tag_mapping: Dict[str, Dict[str, Dict[str, str]]] = {
            "en": {},
            "jp": {},
            "cn": {}
        }
        
        # 游戏名称映射表 - 提高特定游戏相关标签的搜索成功率
        self.game_name_mappings = {
            "原神": ["Genshin Impact", "genshin", "Genshin", "GenshinImpact", "げんしん", "原神"],
            "崩坏3": ["Honkai Impact", "honkai", "Honkai Impact 3rd", "崩坏3", "Honkai"],
            "碧蓝航线": ["Azur Lane", "azur lane", "碧蓝航线", "AzurLane"],
            "明日方舟": ["Arknights", "arknights", "明日方舟"],
            "碧蓝档案": ["Blue Archive", "blue archive", "碧蓝档案", "BlueArchive"],
            "fgo": ["Fate/Grand Order", "fate grand order", "FGO"],
            "少女前线": ["Girls Frontline", "girls frontline", "少女前线"],
            "nikke": ["NIKKE", "nikke", "勝利の女神:NIKKE", "胜利女神", "Goddess of Victory"]
        }
        
        # 热门角色别名映射表
        self.character_aliases = {
            "胡桃": ["Hu Tao", "hutao", "胡桃"],
            "雷电将军": ["Raiden Shogun", "raiden", "雷电将军"],
            "甘雨": ["Ganyu", "ganyu", "甘雨"],
            "刻晴": ["Keqing", "keqing", "刻晴"],
            "八重神子": ["Yae Miko", "yae", "八重神子"],
        }
        
        self.load_tag_mapping()
        self._initialized = True
    
    def load_tag_mapping(self):
        """从文件加载标签映射"""
        if TAG_MAPPING_FILE.exists():
            try:
                with open(TAG_MAPPING_FILE, "r", encoding="utf-8") as f:
                    self.tag_mapping = json.load(f)
                logger.info(f"标签翻译数据已加载，共 {sum(len(v) for v in self.tag_mapping.values())} 个标签映射")
            except Exception as e:
                logger.error(f"标签翻译数据加载失败: {e}")
    
    async def save_tag_mapping(self):
        """保存标签映射到文件"""
        try:
            with open(TAG_MAPPING_FILE, "w", encoding="utf-8") as f:
                json.dump(self.tag_mapping, f, ensure_ascii=False, indent=2)
            logger.debug("标签翻译数据已保存")
        except Exception as e:
            logger.error(f"标签翻译数据保存失败: {e}")
    
    async def translate_tag(self, tag: str, from_lang: str = None, to_lang: str = "cn") -> str:
        """翻译单个标签
        
        参数:
            tag: 标签
            from_lang: 源语言，不指定则自动检测
            to_lang: 目标语言
            
        返回:
            str: 翻译后的标签
        """
        # 自动检测输入语言
        if not from_lang:
            from_lang = detect_language(tag)
        
        # 如果源语言和目标语言相同，直接返回
        if from_lang == to_lang:
            return tag
        
        # 尝试从缓存获取
        if tag in self.tag_mapping[from_lang]:
            return self.tag_mapping[from_lang][tag].get(to_lang, tag)
        
        # 尝试翻译API调用
        try:
            translated = await self._fetch_translation(tag, from_lang, to_lang)
            if translated and translated != tag:
                # 更新缓存
                if tag not in self.tag_mapping[from_lang]:
                    self.tag_mapping[from_lang][tag] = {}
                self.tag_mapping[from_lang][tag][to_lang] = translated
                
                # 双向关联
                if translated not in self.tag_mapping[to_lang]:
                    self.tag_mapping[to_lang][translated] = {}
                self.tag_mapping[to_lang][translated][from_lang] = tag
                
                # 定期保存映射
                await self.save_tag_mapping()
                return translated
        except Exception as e:
            logger.warning(f"标签翻译失败: {tag} ({from_lang}->{to_lang}): {e}")
        
        return tag
    
    async def _fetch_translation(self, tag: str, from_lang: str, to_lang: str) -> Optional[str]:
        """从翻译API获取标签翻译
        
        参数:
            tag: 标签
            from_lang: 源语言
            to_lang: 目标语言
            
        返回:
            Optional[str]: 翻译结果
        """
        # 使用Google Translate无需API key的接口
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": self._get_lang_code(from_lang),
                "tl": self._get_lang_code(to_lang),
                "dt": "t",
                "q": tag
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        translated = data[0][0][0]
                        return translated
            return None
        except Exception as e:
            logger.warning(f"翻译API调用失败: {e}")
            return None
    
    def _get_lang_code(self, lang: str) -> str:
        """将内部语言代码转换为API需要的语言代码
        
        参数:
            lang: 内部语言代码
            
        返回:
            str: API语言代码
        """
        mapping = {"en": "en", "cn": "zh-CN", "jp": "ja"}
        return mapping.get(lang, "en")
    
    async def translate_tags(self, tags: List[str], to_lang: str = "cn") -> List[str]:
        """翻译标签列表
        
        参数:
            tags: 标签列表
            to_lang: 目标语言
            
        返回:
            List[str]: 翻译后的标签列表
        """
        result = []
        for tag in tags:
            # 自动检测每个标签的语言
            translated = await self.translate_tag(tag, None, to_lang)
            result.append(translated)
        return result
    
    async def expand_tags(self, tags: List[str]) -> List[str]:
        """
        扩展标签搜索范围，将标签展开为多语言版本
        
        Args:
            tags: 需要扩展的标签列表
            
        Returns:
            List[str]: 扩展后的标签列表，包含所有可能的翻译
        """
        if not tags:
            return []
        
        # 规范化标签格式
        normalized_tags = [tag.strip() for tag in tags if tag.strip()]
        if not normalized_tags:
            return []
        
        # 扩展集合
        expanded = set(normalized_tags)
        
        # 应用游戏名称映射
        for tag in normalized_tags:
            # 检查是否匹配游戏名称
            if tag.lower() in [game.lower() for game in self.game_name_mappings]:
                # 找到匹配的游戏，添加所有映射名称
                for game_name, aliases in self.game_name_mappings.items():
                    if tag.lower() == game_name.lower() or tag.lower() in [alias.lower() for alias in aliases]:
                        expanded.update(aliases)
                        break
            
            # 检查是否匹配角色别名
            if tag.lower() in [char.lower() for char in self.character_aliases]:
                # 找到匹配的角色，添加所有别名
                for char_name, aliases in self.character_aliases.items():
                    if tag.lower() == char_name.lower() or tag.lower() in [alias.lower() for alias in aliases]:
                        expanded.update(aliases)
                        break
        
        # 翻译所有标签
        for tag in list(expanded):
            # 获取所有可能的翻译
            translations = await self.get_tag_translations(tag)
            expanded.update(translations)
        
        # 转换为列表并去重
        result = list(expanded)
        return result

    async def get_tag_translations(self, tag: str) -> List[str]:
        """
        获取标签的所有可能翻译
        
        Args:
            tag: 需要翻译的标签
            
        Returns:
            List[str]: 该标签的所有翻译版本
        """
        if not tag or not tag.strip():
            return []
            
        translations = set()
        translations.add(tag.strip())
        
        # 检测源语言
        from_lang = detect_language(tag)
        
        # 尝试从映射中获取翻译
        if tag in self.tag_mapping[from_lang]:
            # 添加所有已知的翻译
            for target_lang, translated in self.tag_mapping[from_lang][tag].items():
                if translated and translated.strip():
                    translations.add(translated.strip())
        
        # 如果没有映射，尝试翻译到其他语言
        else:
            for to_lang in ["en", "jp", "cn"]:
                if from_lang != to_lang:
                    translated = await self.translate_tag(tag, from_lang, to_lang)
                    if translated and translated != tag and translated.strip():
                        translations.add(translated.strip())
        
        return list(translations)


_tag_translator_instance = None

def get_tag_translator():
    """获取标签翻译器实例（单例模式）"""
    global _tag_translator_instance
    if _tag_translator_instance is None:
        _tag_translator_instance = TagTranslator()
    return _tag_translator_instance