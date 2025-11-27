import asyncio
import json
import os
import random
import shutil
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.star_tools import StarTools


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
        if self.config_path.exists():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.enabled = bool(cfg.get("enabled", True))
                self.auto_send = bool(cfg.get("auto_send", True))
                cats = cfg.get("categories")
                if isinstance(cats, list) and cats:
                    self.categories = cats
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        else:
            await self._persist_config()
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        if self.alias_path and not self.alias_path.exists():
            self.alias_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

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
        except Exception as e:
            logger.error(f"读取插件配置失败: {e}")

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

    async def _classify_image(self, event: AstrMessageEvent, file_path: str) -> tuple[str, list[str]]:
        """调用多模态模型对图片进行情绪分类与标签抽取。

        Args:
            event: 当前消息事件，用于获取 provider 配置。
            file_path: 本地图片路径。

        Returns:
            (category, tags): 类别与标签列表。
        """
        try:
            prov_id = await self._pick_vision_provider(event)
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
            return cat, tags
        except Exception as e:
            logger.error(f"视觉分类失败: {e}")
            return "其它", []

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
        text = event.get_message_str()
        category = await self._classify_text_category(event, text)
        cat_dir = self.base_dir / "categories" / category
        if not cat_dir.exists():
            return
        files = [p for p in cat_dir.iterdir() if p.is_file()]
        if not files:
            return
        pick = random.choice(files)
        yield event.image_result(pick.as_posix())

    async def _classify_text_category(self, event: AstrMessageEvent, text: str) -> str:
        """调用文本模型判断文本情绪并映射到插件分类。"""
        try:
            prov_id = await self._pick_text_provider(event)
            prompt = "请基于这段文本的情绪选择一个类别: 开心、搞怪、无语、伤心、愤怒、害羞、震惊、奸笑、哭泣、疑惑、尴尬、其它。只返回类别名称。文本: " + text
            resp = await self.context.llm_generate(chat_provider_id=prov_id, prompt=prompt)
            txt = resp.completion_text.strip()
            for c in self.categories:
                if c in txt:
                    return c
            return "其它"
        except Exception:
            return "其它"

    async def _pick_vision_provider(self, event: AstrMessageEvent) -> str:
        if self.vision_provider_id:
            return self.vision_provider_id
        return await self.context.get_current_chat_provider_id(event.unified_msg_origin)

    async def _pick_text_provider(self, event: AstrMessageEvent) -> str:
        if self.text_provider_id:
            return self.text_provider_id
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
                cat, tags = await self._classify_image(event, path)
                stored = await self._store_image(path, cat)
                idx = await self._load_index()
                idx[stored] = {"category": cat, "tags": tags}
                await self._save_index(idx)
            except Exception as e:
                logger.error(f"处理图片失败: {e}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """在 LLM 响应后尝试自动发送合适的表情包。"""
        async for _ in self._maybe_send_meme(event):
            yield _

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

    @meme.command("bind")
    async def bind(self, event: AstrMessageEvent, alias: str = ""):
        if not alias:
            yield event.plain_result("请提供别名")
            return
        aliases = await self._load_aliases()
        aliases[alias] = event.unified_msg_origin
        await self._save_aliases(aliases)
        yield event.plain_result(f"已绑定别名 {alias}")

    @meme.command("unbind")
    async def unbind(self, event: AstrMessageEvent, alias: str = ""):
        if not alias:
            yield event.plain_result("请提供别名")
            return
        aliases = await self._load_aliases()
        if alias in aliases:
            del aliases[alias]
            await self._save_aliases(aliases)
            yield event.plain_result("已取消绑定")
        else:
            yield event.plain_result("别名不存在")

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
        chain = MessageChain().file_image(pick.as_posix())
        await self.context.send_message(umo, chain)
