import asyncio
import json
import os
import random
import shutil
import base64
import hashlib
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


@register("astrbot_plugin_stealer", "Coward332", "自动偷取并分类表情包，在合适时机发送", "1.0.0")
class StealerPlugin(Star):
    """表情包偷取与发送插件。

    功能：
    - 监听消息中的图片并自动保存到插件数据目录
    - 使用当前会话的多模态模型进行情绪分类与标签生成
    - 建立分类索引，支持自动与手动在合适时机发送表情包
    """
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.enabled = True
        self.auto_send = True
        self.base_dir: Path | None = None
        self.plugin_config = config
        self.categories = [
            "开心",
            "搞怪",
            "无语",
            "伤心",
            "愤怒",
            "害羞",
            "震惊",
            "奸笑",
            "哭泣",
            "疑惑",
            "尴尬",
            "其它",
        ]
        self.index_path: Path | None = None
        self.config_path: Path | None = None
        self.vision_provider_id: str | None = None
        self.text_provider_id: str | None = None
        self.alias_path: Path | None = None
        self.backend_tag: str = "emoji_stealer"
        self.emoji_chance: float = 0.4
        self.max_reg_num: int = 100
        self.do_replace: bool = True
        self.check_interval: int = 10
        self.steal_emoji: bool = True
        self.content_filtration: bool = False
        self.filtration_prompt: str = "符合公序良俗"
        self._scanner_task: asyncio.Task | None = None
        self.desc_cache_path: Path | None = None
        self.emotion_cache_path: Path | None = None
        self._desc_cache: dict[str, str] = {}
        self._emotion_cache: dict[str, str] = {}

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
                self.enabled = bool(cfg.get("enabled", True))
                self.auto_send = bool(cfg.get("auto_send", True))
                cats = cfg.get("categories")
                if isinstance(cats, list) and cats:
                    self.categories = cats
                bt = cfg.get("backend_tag")
                if isinstance(bt, str) and bt:
                    self.backend_tag = bt
                ec = cfg.get("emoji_chance")
                if isinstance(ec, (int, float)):
                    self.emoji_chance = float(ec)
                mrn = cfg.get("max_reg_num")
                if isinstance(mrn, int):
                    self.max_reg_num = mrn
                dr = cfg.get("do_replace")
                if isinstance(dr, bool):
                    self.do_replace = dr
                ci = cfg.get("check_interval")
                if isinstance(ci, int):
                    self.check_interval = ci
                se = cfg.get("steal_emoji")
                if isinstance(se, bool):
                    self.steal_emoji = se
                cf = cfg.get("content_filtration")
                if isinstance(cf, bool):
                    self.content_filtration = cf
                fp = cfg.get("filtration_prompt")
                if isinstance(fp, str) and fp:
                    self.filtration_prompt = fp
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
            except Exception:
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

        # 从插件配置读取模型选择
        try:
            if self.plugin_config:
                # 布尔开关
                enabled = self.plugin_config.get("enabled")
                auto_send = self.plugin_config.get("auto_send")
                if isinstance(enabled, bool):
                    self.enabled = enabled
                if isinstance(auto_send, bool):
                    self.auto_send = auto_send
                vpid = self.plugin_config.get("vision_provider_id")
                tpid = self.plugin_config.get("text_provider_id")
                self.vision_provider_id = str(vpid) if vpid else None
                self.text_provider_id = str(tpid) if tpid else None
                btag = self.plugin_config.get("backend_tag")
                if isinstance(btag, str) and btag:
                    self.backend_tag = btag
                ec2 = self.plugin_config.get("emoji_chance")
                if isinstance(ec2, (int, float)):
                    self.emoji_chance = float(ec2)
                mrn2 = self.plugin_config.get("max_reg_num")
                if isinstance(mrn2, int):
                    self.max_reg_num = mrn2
                dr2 = self.plugin_config.get("do_replace")
                if isinstance(dr2, bool):
                    self.do_replace = dr2
                ci2 = self.plugin_config.get("check_interval")
                if isinstance(ci2, int):
                    self.check_interval = ci2
                se2 = self.plugin_config.get("steal_emoji")
                if isinstance(se2, bool):
                    self.steal_emoji = se2
                cf2 = self.plugin_config.get("content_filtration")
                if isinstance(cf2, bool):
                    self.content_filtration = cf2
                fp2 = self.plugin_config.get("filtration_prompt")
                if isinstance(fp2, str) and fp2:
                    self.filtration_prompt = fp2
        except Exception as e:
            logger.error(f"读取插件配置失败: {e}")

        if self._scanner_task is None:
            self._scanner_task = asyncio.create_task(self._scanner_loop())

    async def terminate(self):
        """插件销毁生命周期钩子。"""
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
        }
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    async def _load_index(self) -> dict:
        """加载分类索引文件。

        Returns:
            dict: 键为文件路径，值为包含 category 与 tags 的字典。
        """
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path else {}
        except Exception:
            return {}

    async def _save_index(self, idx: dict):
        """保存分类索引文件。"""
        if not self.index_path:
            return
        self.index_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    async def _load_aliases(self) -> dict:
        try:
            return json.loads(self.alias_path.read_text(encoding="utf-8")) if self.alias_path else {}
        except Exception:
            return {}

    async def _save_aliases(self, aliases: dict):
        if not self.alias_path:
            return
        self.alias_path.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")

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
            desc = self._desc_cache.get(h)
            if not desc:
                prov_id = await self._pick_vision_provider(event)
                if not prov_id:
                    return "其它", [], "", "其它"
                prompt1 = "请为这张图片生成简洁且准确的详细描述，不要包含无关信息。"
                resp1 = await self.context.llm_generate(
                    chat_provider_id=prov_id,
                    prompt=prompt1,
                    image_urls=[f"file:///{os.path.abspath(file_path)}"],
                )
                desc = resp1.completion_text.strip()
                if desc:
                    self._desc_cache[h] = desc
                    if self.desc_cache_path:
                        try:
                            self.desc_cache_path.write_text(json.dumps(self._desc_cache, ensure_ascii=False), encoding="utf-8")
                        except Exception:
                            pass
            emotion = self._emotion_cache.get(h)
            if not emotion:
                prov_text = await self._pick_text_provider(event)
                if not prov_text:
                    prov_text = prov_id
                prompt2 = "基于以下描述选择一个情感类别: 开心、搞怪、无语、伤心、愤怒、害羞、震惊、奸笑、哭泣、疑惑、尴尬、其它。只返回类别。描述: " + desc
                resp2 = await self.context.llm_generate(chat_provider_id=prov_text, prompt=prompt2)
                emotion = resp2.completion_text.strip()
                if emotion:
                    self._emotion_cache[h] = emotion
                    if self.emotion_cache_path:
                        try:
                            self.emotion_cache_path.write_text(json.dumps(self._emotion_cache, ensure_ascii=False), encoding="utf-8")
                        except Exception:
                            pass
            prov_id = await self._pick_vision_provider(event)
            if not prov_id:
                return "其它", [], desc or "", "其它"
            prompt = '请将这张表情包图片按情绪类别进行分类，类别从: 开心、搞怪、无语、伤心、愤怒、害羞、震惊、奸笑、哭泣、疑惑、尴尬、其它 中选择一个，并给出3到5个标签词，只返回JSON，如 {"category":"开心","tags":["可爱","微笑"]}.'
            resp = await self.context.llm_generate(
                chat_provider_id=prov_id,
                prompt=prompt,
                image_urls=[f"file:///{os.path.abspath(file_path)}"],
            )
            txt = resp.completion_text.strip()
            cat = "其它"
            tags: list[str] = []
            try:
                data = json.loads(txt)
                c = str(data.get("category", "")).strip()
                if c:
                    cat = c if c in self.categories else "其它"
                t = data.get("tags", [])
                if isinstance(t, list):
                    tags = [str(x) for x in t][:8]
            except Exception:
                for c in self.categories:
                    if c in txt:
                        cat = c
                        break
            emo = emotion if emotion in self.categories else cat
            return cat, tags, desc or "", emo
        except Exception as e:
            logger.error(f"视觉分类失败: {e}")
            return "其它", [], "", "其它"

    async def _compute_hash(self, file_path: str) -> str:
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return ""

    async def _file_to_base64(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""

    async def _filter_image(self, event: AstrMessageEvent | None, file_path: str) -> bool:
        try:
            if not self.content_filtration:
                return True
            prov_id = await self._pick_vision_provider(event)
            if not prov_id:
                return True
            prompt = "根据以下审核准则判断图片是否符合: " + self.filtration_prompt + "。只返回是或否。"
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
        shutil.copyfile(src_path, raw_dest)
        cat_dir = self.base_dir / "categories" / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        cat_dest = cat_dir / f"{name}{ext}"
        shutil.copyfile(src_path, cat_dest)
        return cat_dest.as_posix()

    async def _maybe_send_meme(self, event: AstrMessageEvent):
        """根据用户文本情绪选择分类，并随机发送一张该分类表情包。"""
        if not self.auto_send or not self.base_dir:
            return
        try:
            if random.random() >= float(self.emoji_chance):
                return
        except Exception:
            pass
        text = event.get_message_str()
        category = await self._classify_text_category(event, text)
        cat_dir = self.base_dir / "categories" / category
        if not cat_dir.exists():
            return
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            return
        pick = random.choice(files)
        idx = await self._load_index()
        rec = idx.get(pick.as_posix())
        if isinstance(rec, dict):
            rec["usage_count"] = int(rec.get("usage_count", 0)) + 1
            rec["last_used"] = int(asyncio.get_event_loop().time())
            idx[pick.as_posix()] = rec
            await self._save_index(idx)
        b64 = await self._file_to_base64(pick.as_posix())
        yield event.make_result().base64_image(b64)

    async def _classify_text_category(self, event: AstrMessageEvent, text: str) -> str:
        """调用文本模型判断文本情绪并映射到插件分类。"""
        try:
            prov_id = await self._pick_text_provider(event)
            prompt = "请基于这段文本的情绪选择一个类别: 开心、搞怪、无语、伤心、愤怒、害羞、震惊、奸笑、哭泣、疑惑、尴尬、其它。只返回类别名称。文本: " + text
            if prov_id is None:
                return "其它"
            resp = await self.context.llm_generate(chat_provider_id=str(prov_id), prompt=prompt)
            txt = resp.completion_text.strip()
            for c in self.categories:
                if c in txt:
                    return c
            return "其它"
        except Exception:
            return "其它"

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
    async def on_message(self, event: AstrMessageEvent):
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
                        os.remove(path)
                    except Exception:
                        pass
                    continue
                cat, tags, desc, emotion = await self._classify_image(event, path)
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
                        os.remove(f.as_posix())
                    except Exception:
                        pass
                    continue
                cat, tags, desc, emotion = await self._classify_image(None, f.as_posix())
                stored = await self._store_image(f.as_posix(), cat)
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
                    os.remove(f.as_posix())
                except Exception:
                    pass
                await self._enforce_capacity(idx)
                await self._save_index(idx)
            except Exception:
                continue

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
                        os.remove(rp)
                except Exception:
                    pass
                if rp in idx:
                    del idx[rp]
        except Exception:
            return

    

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """在 LLM 响应后尝试自动发送合适的表情包。"""
        return

    @filter.on_decorating_result()
    async def before_send(self, event: AstrMessageEvent):
        if not self.auto_send or not self.base_dir:
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        try:
            if random.random() >= float(self.emoji_chance):
                return
        except Exception:
            pass
        text = event.get_message_str()
        category = await self._classify_text_category(event, text)
        cat_dir = self.base_dir / "categories" / category
        if not cat_dir.exists():
            return
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            return
        pick = random.choice(files)
        idx = await self._load_index()
        rec = idx.get(pick.as_posix())
        if isinstance(rec, dict):
            rec["usage_count"] = int(rec.get("usage_count", 0)) + 1
            rec["last_used"] = int(asyncio.get_event_loop().time())
            idx[pick.as_posix()] = rec
            await self._save_index(idx)
        b64 = await self._file_to_base64(pick.as_posix())
        result.base64_image(b64)

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

    @meme.command("send")
    async def meme_send(self, event: AstrMessageEvent, category: str = ""):
        """手动发送指定分类的一张随机表情包。"""
        if not self.base_dir:
            return
        cat = category or "其它"
        cat_dir = self.base_dir / "categories" / cat
        if not cat_dir.exists():
            yield event.plain_result("分类不存在")
            return
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            yield event.plain_result("该分类暂无表情包")
            return
        pick = random.choice(files)
        idx = await self._load_index()
        rec = idx.get(pick.as_posix())
        if isinstance(rec, dict):
            rec["usage_count"] = int(rec.get("usage_count", 0)) + 1
            rec["last_used"] = int(asyncio.get_event_loop().time())
            idx[pick.as_posix()] = rec
            await self._save_index(idx)
        yield event.image_result(pick.as_posix())

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

    @meme.command("set_tag")
    async def set_tag(self, event: AstrMessageEvent, tag: str = ""):
        """设置后台标识。"""
        if not tag:
            yield event.plain_result("请提供后台标识字符串")
            return
        self.backend_tag = tag
        try:
            if self.plugin_config is not None:
                self.plugin_config["backend_tag"] = tag
                self.plugin_config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
        await self._persist_config()
        yield event.plain_result(f"已设置后台标识: {tag}")

    @meme.command("status")
    async def status(self, event: AstrMessageEvent):
        """显示当前偷取状态与后台标识。"""
        st_on = "开启" if self.enabled else "关闭"
        st_auto = "开启" if self.auto_send else "关闭"
        idx = await self._load_index()
        yield event.plain_result(
            f"偷取: {st_on}\n自动发送: {st_auto}\n后台标识: {self.backend_tag}\n已注册数量: {len(idx)}\n概率: {self.emoji_chance}\n上限: {self.max_reg_num}\n替换: {self.do_replace}\n周期: {self.check_interval}min\n自动偷取: {self.steal_emoji}\n审核: {self.content_filtration}"
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
            emo = str(v.get("emotion", v.get("category", "其它")))
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
        return (p, str(v.get("desc", "")), str(v.get("emotion", v.get("category", "其它"))))

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
        return (p, str(v.get("desc", "")), str(v.get("emotion", v.get("category", "其它"))))

    @meme.command("random")
    async def meme_random(self, event: AstrMessageEvent, count: str = "1"):
        try:
            n = int(count)
        except Exception:
            n = 1
        items = await self.get_random_paths(n)
        if not items:
            yield event.plain_result("暂无表情包")
            return
        path, d, emo = items[0]
        b64 = await self._file_to_base64(path)
        yield event.make_result().base64_image(b64)

    @meme.command("find")
    async def meme_find(self, event: AstrMessageEvent, description: str = ""):
        if not description:
            yield event.plain_result("请提供描述")
            return
        item = await self.get_by_description_path(description)
        if not item:
            yield event.plain_result("未匹配到表情包")
            return
        path, d, emo = item
        b64 = await self._file_to_base64(path)
        yield event.make_result().base64_image(b64)

    @meme.command("emotion")
    async def meme_emotion(self, event: AstrMessageEvent, emotion: str = ""):
        if not emotion:
            yield event.plain_result("请提供情感标签")
            return
        item = await self.get_by_emotion_path(emotion)
        if not item:
            yield event.plain_result("未匹配到表情包")
            return
        path, d, emo = item
        b64 = await self._file_to_base64(path)
        yield event.make_result().base64_image(b64)

    

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
        cat = category or "其它"
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
        await self.context.send_message(umo, chain)
