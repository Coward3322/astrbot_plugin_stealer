import asyncio
import base64
import copy
import hashlib
import json
import os
import random
import shutil
from functools import lru_cache, wraps
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    # 可选依赖，用于通过图片尺寸/比例进行快速过滤，未安装时自动降级
    from PIL import Image as PILImage  # type: ignore[import]
except Exception:  # pragma: no cover - 仅作为兼容分支
    PILImage = None


@register("astrbot_plugin_stealer", "nagatoquin33", "自动偷取并分类表情包，在合适时机发送", "1.0.0")
class StealerPlugin(Star):
    """表情包偷取与发送插件。

    功能：
    - 监听消息中的图片并自动保存到插件数据目录
    - 使用当前会话的多模态模型进行情绪分类与标签生成
    - 建立分类索引，支持自动与手动在合适时机发送表情包
    """
    
    # 常量定义
    BACKEND_TAG = "emoji_stealer"
    DEFAULT_FILTRATION_PROMPT = "符合公序良俗"
    
    # 提示词常量
    IMAGE_FILTER_PROMPT = "根据以下审核准则判断图片是否符合: {filtration_rule}。只返回是或否。"
    TEXT_EMOTION_PROMPT_TEMPLATE = """请基于这段文本的情绪选择一个最匹配的类别: {categories}。
请使用&&emotion&&格式返回，例如&&happy&&、&&sad&&。
只返回表情标签，不要添加任何其他内容。文本: {text}"""
    
    # 缓存清理阈值
    _CACHE_MAX_SIZE = 1000  # 每个缓存的最大条目数
    
    # 情绪分类列表（英文标签）
    CATEGORIES = [
        "happy",
        "neutral",
        "sad",
        "angry",
        "shy",
        "surprised",
        "smirk",
        "cry",
        "confused",
        "embarrassed",
    ]
    
    # 情绪类别映射 - 类属性，避免重复创建
    _EMOTION_MAPPING = {
        # Chinese -> English canonical labels
        "开心": "happy",
        "高兴": "happy",
        "快乐": "happy",
        "喜悦": "happy",
        "大笑": "happy",
        "无语": "neutral",
        "郁闷": "neutral",
        "无奈": "neutral",
        "平静": "neutral",
        "一般般": "neutral",
        "一般": "neutral",
        "难过": "sad",
        "伤心": "sad",
        "悲伤": "sad",
        "沮丧": "sad",
        "生气": "angry",
        "愤怒": "angry",
        "暴怒": "angry",
        "恼火": "angry",
        "发火": "angry",
        "害羞": "shy",
        "腼腆": "shy",
        "害臊": "shy",
        "害羞脸红": "shy",
        "脸红": "shy",
        "羞涩": "shy",
        "不好意思": "shy",
        "震惊": "surprised",
        "惊讶": "surprised",
        "吓到": "surprised",
        "吃惊": "surprised",
        "惊呆": "surprised",
        "奸笑": "smirk",
        "坏笑": "smirk",
        "窃笑": "smirk",
        "偷笑": "smirk",
        "调皮": "smirk",
        "得意": "smirk",
        "哭泣": "cry",
        "哭": "cry",
        "落泪": "cry",
        "流泪": "cry",
        "泪目": "cry",
        "疑惑": "confused",
        "迷茫": "confused",
        "困惑": "confused",
        "疑问": "confused",
        "迷惑": "confused",
        "尴尬": "embarrassed",
        "难堪": "embarrassed",
        "难为情": "embarrassed",
        "窘迫": "embarrassed",
        
        # English aliases to canonical labels
        "joy": "happy",
        "joyful": "happy",
        "smile": "happy",
        "glad": "happy",
        "cheerful": "happy",
        "content": "happy",
        "unhappy": "sad",
        "upset": "sad",
        "depressed": "sad",
        "mad": "angry",
        "annoyed": "angry",
        "furious": "angry",
        "bashful": "shy",
        "timid": "shy",
        "amazed": "surprised",
        "astonished": "surprised",
        "shocked": "surprised",
        "shock": "surprised",
        "grin": "smirk",
        "sneer": "smirk",
        "snicker": "smirk",
        "giggle": "smirk",
        "wink": "smirk",
        "tearful": "cry",
        "sobbing": "cry",
        "crying": "cry",
        "puzzled": "confused",
        "bewildered": "confused",
        "perplexed": "confused",
        "baffled": "confused",
        "flustered": "embarrassed",
        "disconcerted": "embarrassed",
        "ashamed": "embarrassed",
    }
    
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.enabled = True
        self.auto_send = True
        self.base_dir: Path | None = None
        self.plugin_config = config
        # 默认情绪分类（英文标签，避免字符兼容性问题）
        # 语义对应关系：happy(开心)、neutral(无语/平静)、sad(伤心)、angry(愤怒)、
        # shy(害羞)、surprised(震惊)、smirk(奸笑/坏笑)、cry(哭泣)、
        # confused(疑惑)、embarrassed(尴尬)
        self.categories = self.CATEGORIES
        self.index_path: Path | None = None
        self.config_path: Path | None = None
        self.vision_provider_id: str | None = None
        self.text_provider_id: str | None = None
        self.alias_path: Path | None = None
        self.backend_tag: str = self.BACKEND_TAG
        self.emoji_chance: float = 0.4
        self.max_reg_num: int = 100
        self.do_replace: bool = True
        self.check_interval: int = 10
        self.steal_emoji: bool = True
        self.content_filtration: bool = False
        self.filtration_prompt: str = self.DEFAULT_FILTRATION_PROMPT
        self._scanner_task: asyncio.Task | None = None
        
        # 情绪类别映射引用（保留以保持向后兼容）
        self._EMOTION_MAPPING = self.__class__._EMOTION_MAPPING
        self.desc_cache_path: Path | None = None
        self.emotion_cache_path: Path | None = None
        self._desc_cache: dict[str, str] = {}
        self._emotion_cache: dict[str, str] = {}
        self.emoji_only: bool = True  # 仅偷取表情包开关
        


    def _update_config_from_dict(self, config_dict: dict):
        """从配置字典更新插件配置。"""
        try:
            # 配置字段映射表：键名 -> (属性名, 类型, 额外处理函数)
            config_mapping = {
                "enabled": ("enabled", bool, None),
                "auto_send": ("auto_send", bool, None),
                "emoji_chance": ("emoji_chance", (int, float), lambda x: float(x)),
                "max_reg_num": ("max_reg_num", int, None),
                "do_replace": ("do_replace", bool, None),
                "check_interval": ("check_interval", int, None),
                "steal_emoji": ("steal_emoji", bool, None),
                "content_filtration": ("content_filtration", bool, None),
                "filtration_prompt": ("filtration_prompt", str, lambda x: x if x else self.DEFAULT_FILTRATION_PROMPT),
                "emoji_only": ("emoji_only", bool, None),
            }
            
            # 处理普通配置项
            for config_key, (attr_name, expected_type, processor) in config_mapping.items():
                if config_key in config_dict:
                    value = config_dict[config_key]
                    if isinstance(value, expected_type):
                        if processor:
                            value = processor(value)
                        setattr(self, attr_name, value)
            
            # 特殊处理categories（需要映射和去重）
            cats = config_dict.get("categories")
            if isinstance(cats, list) and cats:
                # 兼容旧版本配置，将中文/旧标签映射为英文情绪标签，并移除已废弃分类
                mapped: list[str] = []
                for c in cats:
                    norm = self._normalize_category(str(c))
                    if norm and norm in self.categories and norm not in mapped:
                        mapped.append(norm)
                if mapped:
                    self.categories = mapped
        except Exception as e:
            logger.error(f"更新配置失败: {e}")

    async def initialize(self):
        """初始化插件数据目录与配置。

        创建 raw、categories 目录并加载/写入 config 与 index 文件。
        """
        self.base_dir = StarTools.get_data_dir()
        (self.base_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "categories").mkdir(parents=True, exist_ok=True)
        for c in self.categories:
            (self.base_dir / "categories" / c).mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self.config_path = self.base_dir / "config.json"
        self.alias_path = self.base_dir / "aliases.json"
        self.desc_cache_path = self.base_dir / "desc_cache.json"
        self.emotion_cache_path = self.base_dir / "emotion_cache.json"
        if self.config_path.exists():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                self._update_config_from_dict(cfg)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        else:
            await self._persist_config()
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        if self.alias_path and not self.alias_path.exists():
            self.alias_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        if self.desc_cache_path and self.desc_cache_path.exists():
            try:
                self._desc_cache = json.loads(self.desc_cache_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载描述缓存失败: {e}")
                self._desc_cache = {}
        else:
            if self.desc_cache_path:
                self.desc_cache_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        if self.emotion_cache_path and self.emotion_cache_path.exists():
            try:
                self._emotion_cache = json.loads(self.emotion_cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._emotion_cache = {}
        else:
            if self.emotion_cache_path:
                self.emotion_cache_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

        # 移除了侵入式的人格修改功能，使用非侵入式的表情标签提取方式

        # 从插件配置读取模型选择
        try:
            if self.plugin_config:
                self._update_config_from_dict(self.plugin_config)
                # 读取模型ID配置（仅在plugin_config中可用）
                vpid = self.plugin_config.get("vision_provider_id")
                tpid = self.plugin_config.get("text_provider_id")
                self.vision_provider_id = str(vpid) if vpid else None
                self.text_provider_id = str(tpid) if tpid else None
        except Exception as e:
            logger.error(f"读取插件配置失败: {e}")

        if self._scanner_task is None:
            self._scanner_task = asyncio.create_task(self._scanner_loop())

    async def terminate(self):
        """插件销毁生命周期钩子。清理任务。"""

        # 取消后台扫描任务
        try:
            if self._scanner_task is not None:
                self._scanner_task.cancel()
        except Exception as e:
            logger.error(f"取消扫描任务失败: {e}")

        return

    async def _persist_config(self):
        """持久化插件运行配置到配置文件。"""
        if not self.config_path:
            return
            
        payload = {
            "enabled": self.enabled,
            "auto_send": self.auto_send,
            "categories": self.categories,
            "backend_tag": self.backend_tag,
            "emoji_chance": self.emoji_chance,
            "max_reg_num": self.max_reg_num,
            "do_replace": self.do_replace,
            "check_interval": self.check_interval,
            "steal_emoji": self.steal_emoji,
            "content_filtration": self.content_filtration,
            "filtration_prompt": self.filtration_prompt,
            "emoji_only": self.emoji_only,
        }
        
        try:
            def sync_persist_config():
                self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            
            await asyncio.to_thread(sync_persist_config)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    async def _load_index(self) -> dict:
        """加载分类索引文件。

        Returns:
            dict: 键为文件路径，值为包含 category 与 tags 的字典。
        """
        if not self.index_path:
            return {}
        
        try:
            def sync_load_index():
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            
            return await asyncio.to_thread(sync_load_index)
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return {}

    async def _save_index(self, idx: dict):
        """保存分类索引文件。"""
        if not self.index_path:
            return
        
        try:
            def sync_save_index():
                self.index_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
            
            await asyncio.to_thread(sync_save_index)
        except Exception as e:
            logger.error(f"保存索引文件失败: {e}")

    async def _load_aliases(self) -> dict:
        if not self.alias_path:
            return {}
            
        try:
            def sync_load_aliases():
                return json.loads(self.alias_path.read_text(encoding="utf-8"))
            
            return await asyncio.to_thread(sync_load_aliases)
        except Exception as e:
            logger.error(f"加载别名失败: {e}")
            return {}

    async def _save_aliases(self, aliases: dict):
        if not self.alias_path:
            return
            
        try:
            def sync_save_aliases():
                self.alias_path.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
            
            await asyncio.to_thread(sync_save_aliases)
        except Exception as e:
            logger.error(f"保存别名文件失败: {e}")



    def _normalize_category(self, raw: str | None) -> str:
        """将模型返回的情绪类别规范化到内部英文标签。

        - 将中文情绪词映射为英文标签
        - 兼容旧配置中的“搞怪/其它”等泛用标签
        - 对常见同义词做映射
        """
        if not raw:
            return "neutral"
        text = str(raw).strip()

        if text in self.categories:
            return text

        # 旧分类兼容
        if text == "搞怪":
            # 搞怪类通常是调皮/夸张，可映射到 smirk / happy
            return "smirk" if "smirk" in self.categories else "happy"
        if text in {"其它", "其他", "其他表情", "其他情绪"}:
            return "neutral" if "neutral" in self.categories else self.categories[0]

        # 同义词 / 近义词映射（中文与英文别名）
        if text in self._EMOTION_MAPPING and self._EMOTION_MAPPING[text] in self.categories:
            return self._EMOTION_MAPPING[text]

        # 通过包含关系粗略匹配
        for key, val in self._EMOTION_MAPPING.items():
            if key in text and val in self.categories:
                return val

        for cat in self.categories:
            if cat in text:
                return cat

        # 默认回退
        return "neutral" if "neutral" in self.categories else self.categories[0]

    def _is_likely_emoji_by_metadata(self, file_path: str) -> bool:
        """基于文件大小与图像尺寸做一次启发式过滤，减少明显非表情图片。

        这里只做“明显不是表情包”的快速排除，避免误删正常表情：
        - 非常大的文件（>2MB）且分辨率较高时更像是照片/长图
        - 长宽比极端（>4:1）时更像长截图/漫画页
        - 过小的图片也直接排除
        """
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = 0

        # 特大图一般不是聊天用表情
        if size and size > 2 * 1024 * 1024:
            return False

        if PILImage is not None:
            try:
                with PILImage.open(file_path) as im:
                    width, height = im.size

                if width <= 0 or height <= 0:
                    return False

                long_side = max(width, height)
                short_side = min(width, height)

                # 过长的长图 / 截图
                aspect = long_side / short_side if short_side > 0 else 0
                if aspect > 4.0:
                    return False

                # 过小或超大分辨率都视为非典型表情
                if long_side < 40:
                    return False

                if long_side > 2048:
                    return False

            except Exception:
                # 无法读取时不做强制判定，交给后续多模态模型处理
                return True

        return True

    async def _classify_image(self, event: AstrMessageEvent | None, file_path: str) -> tuple[str, list[str], str, str]:
        """调用多模态模型对图片进行情绪分类与标签抽取。

        Args:
            event: 当前消息事件，用于获取 provider 配置。
            file_path: 本地图片路径。

        Returns:
            (category, tags, desc, emotion): 类别、标签、详细描述、情感标签。
        """
        try:
            h = await self._compute_hash(file_path)

            # 获取视觉模型
            prov_id = await self._pick_vision_provider(event)
            if not prov_id:
                return "其它", [], "", "其它"

            # 仅在启用表情包过滤时进行判断
            if self.emoji_only:
                # 先用元数据做一次快速过滤，明显不是表情图片的直接跳过
                if not self._is_likely_emoji_by_metadata(file_path):
                    return "非表情包", [], "", "非表情包"

                # 再使用多模态模型严格判断是否为表情包
                emoji_prompt = (
                    "你是聊天表情审核助手，请判断这张图片是否为聊天表情包"
                    "（emoji/meme/sticker），仅返回“是”或“否”，不要添加任何其他内容。"
                    "表情包通常具有以下特征："
                    "1）尺寸相对较小，主要用于聊天中快速表达情绪或态度；"
                    "2）画面主体清晰突出，通常集中在人物/卡通形象/动物或简洁抽象图案上；"
                    "3）可能包含少量文字、夸张表情或动作来强化情绪表达；"
                    "4）常以方图或接近方图的比例出现（宽高比通常在1:2到2:1之间）；"
                    "5）风格简洁明了，能在短时间内传达情绪。"
                    "以下情况一律回答“否”："
                    "- 风景照、生活照片、人像摄影等写实类图片"
                    "- 完整漫画页、长截图（高度远大于宽度）"
                    "- 聊天记录截图、社交媒体界面截图"
                    "- 宣传海报、商业广告、产品图片"
                    "- 电脑/手机壁纸（通常尺寸较大且内容复杂）"
                    "- 含大量说明文字的信息图、流程图、文档截图"
                    "- 视频帧截图、电影/动漫截图（非专门制作的表情）"
                    "- 像素极低或严重模糊无法识别内容的图片"
                )
                emoji_resp = await self.context.llm_generate(
                    chat_provider_id=prov_id,
                    prompt=emoji_prompt,
                    image_urls=[f"file:///{os.path.abspath(file_path)}"],
                )
                emoji_result = emoji_resp.completion_text.strip()
                is_emoji = ("是" in emoji_result) or ("yes" in emoji_result.lower())

                # 如果不是表情包，返回特定标识
                if not is_emoji:
                    return "非表情包", [], "", "非表情包"

            desc = self._desc_cache.get(h)
            if not desc:
                prompt1 = (
                    "请为这张表情包图片生成简洁、准确、具体的详细描述，10-30字左右，不要包含无关信息。"
                    "描述要求："
                    "1. 明确说明图片的主要内容（人物/动物/物体/风格）"
                    "2. 详细描述表情特征（如嘴角上扬、眼睛弯成月牙、脸红、流泪、皱眉、瞪眼等）"
                    "3. 准确写出角色的具体情绪（必须从以下列表选择：开心、害羞、哭泣、愤怒、无语、震惊、困惑、尴尬、奸笑、平静）"
                    "4. 避免使用模糊词汇如“搞怪”、“有趣”、“其他”等，必须选择具体情绪词"
                    "例如：\"一个卡通猫角色，眼睛弯成月牙，嘴角上扬，露出开心的笑容\""
                )
                resp1 = await self.context.llm_generate(
                    chat_provider_id=prov_id,
                    prompt=prompt1,
                    image_urls=[f"file:///{os.path.abspath(file_path)}"],
                )
                desc = resp1.completion_text.strip()
                if desc:
                    self._desc_cache[h] = desc
                    
                    # 清理描述缓存，保持在阈值以下
                    if len(self._desc_cache) > self._CACHE_MAX_SIZE:
                        # 只保留最新的条目
                        keys_to_keep = list(self._desc_cache.keys())[-self._CACHE_MAX_SIZE:]
                        self._desc_cache = {k: self._desc_cache[k] for k in keys_to_keep}
                    
                    if self.desc_cache_path:
                        try:
                            def sync_save_desc_cache():
                                self.desc_cache_path.write_text(json.dumps(self._desc_cache, ensure_ascii=False), encoding="utf-8")
                            
                            await asyncio.to_thread(sync_save_desc_cache)
                        except Exception as e:
                            logger.error(f"保存描述缓存失败: {e}")
            emotion = self._emotion_cache.get(h)
            if not emotion:
                prov_text = await self._pick_text_provider(event)
                if not prov_text:
                    prov_text = prov_id
                prompt2 = (
                    "Based on the following description, choose ONE emotion word in English "
                    "from this exact list: happy, neutral, sad, angry, shy, surprised, smirk, cry, confused, embarrassed. "
                    "You must select the emotion that best matches the overall feeling described. "
                    "If multiple emotions are mentioned, choose the most prominent one. "
                    "Only return the single emotion word, with no other text, punctuation, or explanations. "
                    "Examples:"
                    "- Description: A cartoon cat with eyes curved into crescents and an upward smile, looking happy"
                    "  Response: happy"
                    "- Description: An anime girl with red cheeks, looking down shyly"
                    "  Response: shy"
                    "- Description: A character with tears streaming down their face, looking sad"
                    "  Response: cry"
                    "Description: " + desc
                )
                resp2 = await self.context.llm_generate(chat_provider_id=prov_text, prompt=prompt2)
                emotion = resp2.completion_text.strip()
                if emotion:
                     self._emotion_cache[h] = emotion
                     
                     # 清理情绪缓存，保持在阈值以下
                     if len(self._emotion_cache) > self._CACHE_MAX_SIZE:
                         # 只保留最新的条目
                         keys_to_keep = list(self._emotion_cache.keys())[-self._CACHE_MAX_SIZE:]
                         self._emotion_cache = {k: self._emotion_cache[k] for k in keys_to_keep}
                     
                     if self.emotion_cache_path:
                         try:
                             def sync_save_emotion_cache():
                                 self.emotion_cache_path.write_text(json.dumps(self._emotion_cache, ensure_ascii=False), encoding="utf-8")
                             
                             await asyncio.to_thread(sync_save_emotion_cache)
                         except Exception as e:
                             logger.error(f"保存情绪缓存失败: {e}")
            
            prompt = (
                '你是专业的表情包情绪分类师，请严格按照以下要求处理：'
                '1. 观察图片内容，根据表情、动作、氛围判断主要情绪'
                '2. 从以下英文情绪标签中选择唯一最匹配的：happy, neutral, sad, angry, shy, surprised, smirk, cry, confused, embarrassed'
                '3. 同时提取2-5个能描述图片特征的关键词标签（如cute, smile, blush, tear, angry等）'
                '4. 必须返回严格的JSON格式，包含category和tags两个字段'
                '5. category字段为选择的情绪标签，tags字段为提取的关键词数组'
                '6. 如果是二次元/动漫/卡通角色，必须根据表情实际情绪分类'
                '7. 不要添加任何JSON之外的内容，确保JSON可以被程序直接解析'
                '错误示例（不要这样做）：'
                '- \"我认为这张图片的情绪是happy，标签有cute, smile\"'
                '- {category:happy, tags:[\"cute\",\"smile\"]}'
                '正确示例：'
                '- {\"category\":\"happy\",\"tags\":[\"cute\",\"smile\",\"cartoon\"]}'
                '- {\"category\":\"shy\",\"tags\":[\"blush\",\"anime\",\"girl\"]}'
                '- {\"category\":\"cry\",\"tags\":[\"tear\",\"sad\",\"cartoon\"]}'
            )
            resp = await self.context.llm_generate(
                chat_provider_id=prov_id,
                prompt=prompt,
                image_urls=[f"file:///{os.path.abspath(file_path)}"],
            )
            txt = resp.completion_text.strip()
            cat = "无语"
            tags: list[str] = []
            try:
                data = json.loads(txt)
                c = str(data.get("category", "")).strip()
                if c:
                    cat = self._normalize_category(c)
                t = data.get("tags", [])
                if isinstance(t, list):
                    tags = [str(x) for x in t][:8]
            except Exception:
                for c in self.categories:
                    if c in txt:
                        cat = c
                        break
            emo = self._normalize_category(emotion) if emotion else cat
            cat = self._normalize_category(cat)
            return cat, tags, desc or "", emo
        except Exception as e:
            logger.error(f"视觉分类失败: {e}")
            fallback = "无语" if "无语" in self.categories else self.categories[0]
            return fallback, [], "", fallback

    async def _compute_hash(self, file_path: str) -> str:
        try:
            def sync_compute_hash():
                with open(file_path, "rb") as f:
                    data = f.read()
                return hashlib.sha256(data).hexdigest()
            return await asyncio.to_thread(sync_compute_hash)
        except Exception:
            return ""

    async def _file_to_base64(self, path: str) -> str:
        try:
            def sync_file_to_base64():
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            return await asyncio.to_thread(sync_file_to_base64)
        except Exception:
            return ""

    async def _filter_image(self, event: AstrMessageEvent | None, file_path: str) -> bool:
        try:
            if not self.content_filtration:
                return True
            prov_id = await self._pick_vision_provider(event)
            if not prov_id:
                return True
            prompt = self.IMAGE_FILTER_PROMPT.format(filtration_rule=self.filtration_prompt)
            resp = await self.context.llm_generate(
                chat_provider_id=prov_id,
                prompt=prompt,
                image_urls=[f"file:///{os.path.abspath(file_path)}"],
            )
            txt = resp.completion_text.strip()
            return ("是" in txt) or ("符合" in txt) or ("yes" in txt.lower())
        except Exception:
            return True

    async def _store_image(self, src_path: str, category: str) -> str:
        """将图片保存到 raw 与分类目录，并返回分类目录保存路径。"""
        if not self.base_dir:
            return src_path
        name = f"{int(asyncio.get_event_loop().time()*1000)}_{random.randint(1000,9999)}"
        ext = os.path.splitext(src_path)[1] or ".jpg"
        raw_dest = self.base_dir / "raw" / f"{name}{ext}"
        cat_dir = self.base_dir / "categories" / category
        cat_dest = cat_dir / f"{name}{ext}"
        
        try:
            def sync_store_image():
                # 同步部分
                cat_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_path, raw_dest)
                shutil.copyfile(src_path, cat_dest)
                return cat_dest.as_posix()
            
            return await asyncio.to_thread(sync_store_image)
        except Exception as e:
            logger.error(f"保存图片失败: {e}")
            return src_path

    def _is_in_parentheses(self, text: str, index: int) -> bool:
        """判断字符串中指定索引位置是否在括号内。
        
        支持圆括号()和方括号[]。
        """
        parentheses_count = 0
        bracket_count = 0
        
        for i in range(index):
            if text[i] == '(':
                parentheses_count += 1
            elif text[i] == ')':
                parentheses_count -= 1
            elif text[i] == '[':
                bracket_count += 1
            elif text[i] == ']':
                bracket_count -= 1
        
        return parentheses_count > 0 or bracket_count > 0

    async def _classify_text_category(self, event: AstrMessageEvent, text: str) -> str:
        """调用文本模型判断文本情绪并映射到插件分类。"""
        try:
            prov_id = await self._pick_text_provider(event)
            
            # 使用插件原有的分类体系构建提示词，要求输出&&emotion&&格式
            categories_str = ", ".join(self.categories)
            prompt = self.TEXT_EMOTION_PROMPT_TEMPLATE.format(
            categories=categories_str,
            text=text
        )
            
            if prov_id is None:
                return ""
                
            resp = await self.context.llm_generate(chat_provider_id=str(prov_id), prompt=prompt)
            txt = resp.completion_text.strip()
            
            import re
            # 提取&&emotion&&格式的内容
            match = re.search(r'&&([^&&]+)&&', txt)
            if match:
                emotion = match.group(1).strip()
            else:
                # 如果没有&&格式，直接使用返回值
                emotion = txt
            
            # 使用插件内置的_normalize_category方法进行类别映射
            normalized_category = self._normalize_category(emotion)
            return normalized_category if normalized_category in self.categories else ""
            
        except Exception as e:
            logger.error(f"文本情绪分类失败: {e}")
            return ""

    async def _extract_emotions_from_text(self, event: AstrMessageEvent | None, text: str) -> tuple[list[str], str]:
        """从文本中提取情绪关键词，本地提取不到时使用 LLM。

        支持的形式：
        - 形如 &&开心&& 的显式标记
        - 直接出现的类别关键词（如“开心”“害羞”“哭泣”等），按出现顺序去重
        - 本地提取不到时调用 LLM 进行情绪分类
        
        返回：
        - 提取到的情绪列表
        - 清理掉情绪标记和情绪词后的文本
        
        优化出处：参考 astrbot_plugin_meme_manager 插件的 resp 方法实现
        """
        if not text:
            return [], text

        import re
        
        res: list[str] = []
        seen: set[str] = set()
        cleaned_text = str(text)
        valid_categories = set(self.categories)
        
        # 1. 处理显式包裹标记：&&情绪&&
        hex_pattern = r"&&([^&&]+)&&"
        matches = list(re.finditer(hex_pattern, cleaned_text))

        # 收集所有匹配项，避免索引偏移问题
        temp_replacements = []
        for match in matches:
            original = match.group(0)
            emotion = match.group(1).strip()
            norm_cat = self._normalize_category(emotion)
            
            if norm_cat and norm_cat in valid_categories:
                temp_replacements.append((original, norm_cat))
            else:
                temp_replacements.append((original, ""))  # 非法或未知情绪静默移除

        # 保持原始顺序替换
        for original, emotion in temp_replacements:
            cleaned_text = cleaned_text.replace(original, "", 1)
            if emotion and emotion not in seen:
                seen.add(emotion)
                res.append(emotion)
        
        # 2. 处理直接出现的英文情绪词（直接匹配分类）
        for cat in self.categories:
            if cat in seen:
                continue
                
            # 使用边界匹配确保是完整单词
            pattern = rf'\b{re.escape(cat)}\b'
            matches = list(re.finditer(pattern, cleaned_text, re.IGNORECASE))
            
            # 检查是否有括号外的匹配
            has_external_match = False
            for match in matches:
                if not self._is_in_parentheses(cleaned_text, match.start()):
                    has_external_match = True
                    break
            
            if has_external_match:
                seen.add(cat)
                res.append(cat)
                
                # 注意：不再移除英文情绪词，保留原始文本的完整性
                # 只提取情绪，不修改文本内容
        
        # 3. 处理直接出现的中文情绪词（使用统一的EMOTION_MAPPING）
        # 按长度排序，优先处理长的情绪词
        sorted_cn_emotions = sorted(self._EMOTION_MAPPING.keys(), key=len, reverse=True)
        
        for cn_emotion in sorted_cn_emotions:
            en_emotion = self._EMOTION_MAPPING[cn_emotion]
            if en_emotion in valid_categories and en_emotion not in seen:
                positions = []
                start = 0
                
                # 收集所有匹配位置
                while True:
                    pos = cleaned_text.find(cn_emotion, start)
                    if pos == -1:
                        break
                    positions.append(pos)
                    start = pos + 1
                
                # 检查是否有括号外的匹配
                external_positions = [pos for pos in positions if not self._is_in_parentheses(cleaned_text, pos)]
                
                if external_positions:
                    seen.add(en_emotion)
                    res.append(en_emotion)
                    
                    # 注意：不再移除中文情绪词，保留原始文本的完整性
                    # 只提取情绪，不修改文本内容
        
        # 清理多余的空格
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

        # 本地提取不到情绪时，调用 LLM 进行分类
        if not res and event:
            llm_emotion = await self._classify_text_category(event, cleaned_text)
            if llm_emotion and llm_emotion in valid_categories:
                res.append(llm_emotion)

        return res, cleaned_text

    async def _pick_vision_provider(self, event: AstrMessageEvent | None) -> str | None:
        if self.vision_provider_id:
            return self.vision_provider_id
        if event is None:
            return None
        return await self.context.get_current_chat_provider_id(event.unified_msg_origin)

    async def _pick_text_provider(self, event: AstrMessageEvent | None) -> str | None:
        if self.text_provider_id:
            return self.text_provider_id
        if event is None:
            return None
        return await self.context.get_current_chat_provider_id(event.unified_msg_origin)

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent, *args, **kwargs):
        """消息监听：偷取消息中的图片并分类存储。"""
        if not self.enabled:
            return
        imgs = []
        for comp in event.get_messages():
            if isinstance(comp, Image):
                imgs.append(comp)
        for img in imgs:
            try:
                path = await img.convert_to_file_path()
                ok = await self._filter_image(event, path)
                if not ok:
                    try:
                        await asyncio.to_thread(os.remove, path)
                    except Exception as e:
                         logger.error(f"删除文件失败: {e}")
                    continue
                cat, tags, desc, emotion = await self._classify_image(event, path)
                # 如果分类为"非表情包"，跳过存储
                if cat == "非表情包" or emotion == "非表情包":
                    try:
                        await asyncio.to_thread(os.remove, path)
                    except Exception as e:
                        logger.error(f"删除文件失败: {e}")
                    continue
                stored = await self._store_image(path, cat)
                idx = await self._load_index()
                idx[stored] = {
                    "category": cat,
                    "tags": tags,
                    "backend_tag": self.backend_tag,
                    "created_at": int(asyncio.get_event_loop().time()),
                    "usage_count": 0,
                    "desc": desc,
                    "emotion": emotion,
                }
                await self._save_index(idx)
            except Exception as e:
                logger.error(f"处理图片失败: {e}")

    async def _scanner_loop(self):
        while True:
            try:
                await asyncio.sleep(max(1, int(self.check_interval)) * 60)
                if not self.steal_emoji:
                    continue
                await self._scan_register_emoji_folder()
            except Exception:
                continue

    async def _scan_register_emoji_folder(self):
        try:
            base = Path(get_astrbot_data_path()) / "emoji"
            base.mkdir(parents=True, exist_ok=True)
            files = []
            for p in base.iterdir():
                if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                    files.append(p)
            if not files:
                return
            idx = await self._load_index()
            for f in files:
                try:
                    ok = await self._filter_image(None, f.as_posix())
                    if not ok:
                        try:
                            await asyncio.to_thread(os.remove, f.as_posix())
                        except Exception:
                            pass
                        continue
                    cat, tags, desc, emotion = await self._classify_image(None, f.as_posix())
                    # 如果分类为"非表情包"，跳过存储
                    if cat == "非表情包" or emotion == "非表情包":
                        try:
                            await asyncio.to_thread(os.remove, f.as_posix())
                        except Exception:
                            pass
                        continue
                    stored = await self._store_image(f.as_posix(), cat)
                    # 检查_store_image是否成功保存（返回的路径不等于源路径）
                    if stored != f.as_posix():
                        idx[stored] = {
                            "category": cat,
                            "tags": tags,
                            "backend_tag": self.backend_tag,
                            "created_at": int(asyncio.get_event_loop().time()),
                            "usage_count": 0,
                            "desc": desc,
                            "emotion": emotion,
                        }
                        try:
                            await asyncio.to_thread(os.remove, f.as_posix())
                        except Exception as e:
                            logger.error(f"删除源文件失败: {e}")
                    else:
                        logger.error(f"保存图片失败，源文件未删除: {f.as_posix()}")
                except Exception as e:
                    logger.error(f"处理文件失败: {f.as_posix()}, 错误: {e}")
            # 在处理完所有文件后再检查容量和保存索引
            await self._enforce_capacity(idx)
            await self._save_index(idx)
        except Exception as e:
            logger.error(f"扫描注册表情文件夹失败: {e}")

    async def _enforce_capacity(self, idx: dict):
        try:
            if len(idx) <= int(self.max_reg_num):
                return
            if not self.do_replace:
                return
            items = []
            for k, v in idx.items():
                c = int(v.get("usage_count", 0)) if isinstance(v, dict) else 0
                t = int(v.get("created_at", 0)) if isinstance(v, dict) else 0
                items.append((k, c, t))
            items.sort(key=lambda x: (x[1], x[2]))
            remove_count = len(idx) - int(self.max_reg_num)
            for i in range(remove_count):
                rp = items[i][0]
                try:
                    if os.path.exists(rp):
                        await asyncio.to_thread(os.remove, rp)
                except Exception:
                    pass
                if rp in idx:
                    del idx[rp]
        except Exception:
            return

    



    @filter.on_decorating_result()
    async def before_send(self, event: AstrMessageEvent):
        if not self.auto_send or not self.base_dir:
            return
        result = event.get_result()
        # 只在有文本结果时尝试匹配表情包
        if result is None:
            return
        try:
            chance = float(self.emoji_chance)
            # 兜底保护，防止配置错误导致永远/从不触发
            if chance <= 0:
                logger.debug(f"表情包自动发送概率为0，未触发图片发送")
                return
            if chance > 1:
                chance = 1.0
            if random.random() >= chance:
                logger.debug(f"表情包自动发送概率检查未通过 ({chance}), 未触发图片发送")
                return
        except Exception:
            logger.error(f"解析表情包自动发送概率配置失败，未触发图片发送")
            pass
        
        logger.debug(f"表情包自动发送概率检查通过，开始处理图片发送")
        
        # 文本仅用于本地规则提取情绪关键字，不再请求额外的 LLM
        text = result.get_plain_text() or event.get_message_str()
        if not text or not text.strip():
            logger.debug(f"没有可处理的文本内容，未触发图片发送")
            return
            
        emotions, cleaned_text = await self._extract_emotions_from_text(event, text)
        if not emotions:
            logger.debug(f"未从文本中提取到情绪关键词，未触发图片发送")
            return
            
        logger.debug(f"提取到情绪关键词: {emotions}")
        
        # 目前只取第一个识别到的情绪类别
        category = emotions[0]
        cat_dir = self.base_dir / "categories" / category
        if not cat_dir.exists():
            logger.debug(f"情绪'{category}'对应的图片目录不存在，未触发图片发送")
            return
            
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            logger.debug(f"情绪'{category}'对应的图片目录为空，未触发图片发送")
            return
            
        logger.debug(f"从'{category}'目录中找到 {len(files)} 张图片")
        pick = random.choice(files)
        idx = await self._load_index()
        rec = idx.get(pick.as_posix())
        if isinstance(rec, dict):
            rec["usage_count"] = int(rec.get("usage_count", 0)) + 1
            rec["last_used"] = int(asyncio.get_event_loop().time())
            idx[pick.as_posix()] = rec
            await self._save_index(idx)
        # 创建新的结果对象并更新内容
        new_result = event.make_result().set_result_content_type(result.result_content_type)
        
        # 添加除了Plain文本外的其他组件
        for comp in result.chain:
            if not isinstance(comp, Plain):
                new_result.chain.append(comp)
        
        # 添加清除标签后的文本
        if cleaned_text.strip():
            new_result.message(cleaned_text.strip())
        
        # 添加图片
        b64 = await self._file_to_base64(pick.as_posix())
        new_result.base64_image(b64)
        
        # 设置新的结果对象
        event.set_result(new_result)

    @filter.command_group("meme")
    def meme(self):
        """meme 指令组。"""
        pass

    @meme.command("on")
    async def meme_on(self, event: AstrMessageEvent):
        """开启偷表情包功能。"""
        self.enabled = True
        try:
            if self.plugin_config is not None:
                self.plugin_config["enabled"] = True
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result("已开启偷表情包")

    @meme.command("off")
    async def meme_off(self, event: AstrMessageEvent):
        """关闭偷表情包功能。"""
        self.enabled = False
        try:
            if self.plugin_config is not None:
                self.plugin_config["enabled"] = False
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result("已关闭偷表情包")

    @meme.command("auto_on")
    async def auto_on(self, event: AstrMessageEvent):
        """开启自动发送功能。"""
        self.auto_send = True
        try:
            if self.plugin_config is not None:
                self.plugin_config["auto_send"] = True
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result("已开启自动发送")

    @meme.command("auto_off")
    async def auto_off(self, event: AstrMessageEvent):
        """关闭自动发送功能。"""
        self.auto_send = False
        try:
            if self.plugin_config is not None:
                self.plugin_config["auto_send"] = False
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result("已关闭自动发送")



    @meme.command("set_vision")
    async def set_vision(self, event: AstrMessageEvent, provider_id: str = ""):
        if not provider_id:
            yield event.plain_result("请提供视觉模型的 provider_id")
            return
        self.vision_provider_id = provider_id
        try:
            if self.plugin_config is not None:
                self.plugin_config["vision_provider_id"] = provider_id
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result(f"已设置视觉模型: {provider_id}")

    @meme.command("set_text")
    async def set_text(self, event: AstrMessageEvent, provider_id: str = ""):
        if not provider_id:
            yield event.plain_result("请提供主回复文本模型的 provider_id")
            return
        self.text_provider_id = provider_id
        try:
            if self.plugin_config is not None:
                self.plugin_config["text_provider_id"] = provider_id
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result(f"已设置文本模型: {provider_id}")

    @meme.command("show_providers")
    async def show_providers(self, event: AstrMessageEvent):
        vp = self.vision_provider_id or "当前会话"
        tp = self.text_provider_id or "当前会话"
        yield event.plain_result(f"视觉模型: {vp}\n文本模型: {tp}")



    @meme.command("emoji_only")
    async def meme_emoji_only(self, event: AstrMessageEvent, enable: str = ""):
        """切换是否仅偷取表情包模式。"""
        if enable.lower() in ["on", "开启", "true"]:
            self.emoji_only = True
            try:
                if self.plugin_config is not None:
                    self.plugin_config["emoji_only"] = True
                    self.plugin_config.save_config()
            except Exception as e:
                logger.error(f"保存插件配置失败: {e}")
            await self._persist_config()
            yield event.plain_result("已开启仅偷取表情包模式")
        elif enable.lower() in ["off", "关闭", "false"]:
            self.emoji_only = False
            try:
                if self.plugin_config is not None:
                    self.plugin_config["emoji_only"] = False
                    self.plugin_config.save_config()
            except Exception as e:
                logger.error(f"保存插件配置失败: {e}")
            await self._persist_config()
            yield event.plain_result("已关闭仅偷取表情包模式")
        else:
            status = "开启" if self.emoji_only else "关闭"
            yield event.plain_result(f"当前仅偷取表情包模式: {status}")

    @meme.command("status")
    async def status(self, event: AstrMessageEvent):
        """显示当前偷取状态与后台标识。"""
        st_on = "开启" if self.enabled else "关闭"
        st_auto = "开启" if self.auto_send else "关闭"
        st_emoji_only = "开启" if self.emoji_only else "关闭"
        idx = await self._load_index()
        yield event.plain_result(
            f"偷取: {st_on}\n自动发送: {st_auto}\n仅偷取表情包: {st_emoji_only}\n已注册数量: {len(idx)}\n概率: {self.emoji_chance}\n上限: {self.max_reg_num}\n替换: {self.do_replace}\n周期: {self.check_interval}min\n自动偷取: {self.steal_emoji}\n审核: {self.content_filtration}"
        )

    async def get_count(self) -> int:
        idx = await self._load_index()
        return len(idx)

    async def get_info(self) -> dict:
        idx = await self._load_index()
        return {
            "current_count": len(idx),
            "max_count": self.max_reg_num,
            "available_emojis": len(idx),
        }

    async def get_emotions(self) -> list[str]:
        idx = await self._load_index()
        s = set()
        for v in idx.values():
            if isinstance(v, dict):
                emo = v.get("emotion")
                if isinstance(emo, str) and emo:
                    s.add(emo)
        return sorted(list(s))

    async def get_descriptions(self) -> list[str]:
        idx = await self._load_index()
        res = []
        for v in idx.values():
            if isinstance(v, dict):
                d = v.get("desc")
                if isinstance(d, str) and d:
                    res.append(d)
        return res

    async def _load_all_records(self) -> list[tuple[str, dict]]:
        idx = await self._load_index()
        return [(k, v) for k, v in idx.items() if isinstance(v, dict) and os.path.exists(k)]

    async def get_random_paths(self, count: int | None = 1) -> list[tuple[str, str, str]]:
        recs = await self._load_all_records()
        if not recs:
            return []
        n = max(1, int(count or 1))
        pick = random.sample(recs, min(n, len(recs)))
        res = []
        for p, v in pick:
            d = str(v.get("desc", ""))
            emo = str(v.get("emotion", v.get("category", self.categories[0] if self.categories else "开心")))
            res.append((p, d, emo))
        return res

    async def get_by_emotion_path(self, emotion: str) -> tuple[str, str, str] | None:
        recs = await self._load_all_records()
        cands = []
        for p, v in recs:
            emo = str(v.get("emotion", v.get("category", "")))
            tags = v.get("tags", [])
            if emotion and (emotion == emo or (isinstance(tags, list) and emotion in [str(t) for t in tags])):
                cands.append((p, v))
        if not cands:
            return None
        p, v = random.choice(cands)
        return (p, str(v.get("desc", "")), str(v.get("emotion", v.get("category", self.categories[0] if self.categories else "开心"))))

    async def get_by_description_path(self, description: str) -> tuple[str, str, str] | None:
        recs = await self._load_all_records()
        cands = []
        for p, v in recs:
            d = str(v.get("desc", ""))
            if description and description in d:
                cands.append((p, v))
        if not cands:
            for p, v in recs:
                tags = v.get("tags", [])
                if isinstance(tags, list):
                    if any(str(description) in str(t) for t in tags):
                        cands.append((p, v))
        if not cands:
            return None
        p, v = random.choice(cands)
        return (p, str(v.get("desc", "")), str(v.get("emotion", v.get("category", self.categories[0] if self.categories else "开心"))))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme.command("push")
    async def push(self, event: AstrMessageEvent, category: str = "", alias: str = ""):
        if not self.base_dir:
            return
        umo = event.unified_msg_origin
        if alias:
            aliases = await self._load_aliases()
            if alias in aliases:
                umo = aliases[alias]
            else:
                yield event.plain_result("别名不存在")
                return
        cat = category or (self.categories[0] if self.categories else "happy")
        cat_dir = self.base_dir / "categories" / cat
        if not cat_dir.exists():
            yield event.plain_result("分类不存在")
            return
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            yield event.plain_result("该分类暂无表情包")
            return
        pick = random.choice(files)
        b64 = await self._file_to_base64(pick.as_posix())
        chain = MessageChain().base64_image(b64)
        # 统一使用yield返回结果，保持交互体验一致
        yield event.result_with_message_chain(chain)

