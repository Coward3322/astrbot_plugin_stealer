# astrbot_plugin_stealer

自动偷取、分类并在合适时机发送表情包的 AstrBot 插件。

## 概述
- 被动监听消息中的图片，保存到插件数据目录并进行情绪分类与标签生成。
- 在模型响应后按当前对话文本情绪，自动挑选并发送合适的表情包。
- 支持主动推送到绑定会话（通过别名），便于定时或外部触发发送。

## 功能
- 图片偷取与分类：`on_message` 钩子处理图片并入库，见 `data/plugins/astrbot_plugin_stealer/main.py:211`。
- 自动发送：`on_llm_response` 钩子在回复后尝试自动发送，见 `data/plugins/astrbot_plugin_stealer/main.py:231`。
- 主动推送：通过 `\meme bind`/`\meme push`/`\meme unbind` 管理会话别名与推送，见 `data/plugins/astrbot_plugin_stealer/main.py:296`、`:315`。
- 独立模型选择：支持分别配置视觉模型与文本模型，满足“主回复 + 视觉转述”生产模式，见 `data/plugins/astrbot_plugin_stealer/main.py:200`、`:205`。

## 安装与启用
1. 将插件目录置于 `data/plugins/astrbot_plugin_stealer`。
2. 启动 AstrBot 后在 WebUI 的插件页面启用本插件。

## 配置
插件会自动加载 `data/plugins/astrbot_plugin_stealer/_conf_schema.json` 并在 WebUI 提供配置表单：
- `enabled`：是否开启偷取表情包（默认 `true`）。
- `auto_send`：是否自动发送表情包（默认 `true`）。
- `vision_provider_id`：视觉模型 Provider（用于图片分类），支持 `_special: select_provider`。
- `text_provider_id`：文本模型 Provider（用于文本情绪判断），支持 `_special: select_provider`。

运行时会在初始化阶段读取上述配置并生效，见 `data/plugins/astrbot_plugin_stealer/main.py:76`。

## 指令
- `\meme on` / `\meme off`：开启/关闭偷取功能（`data/plugins/astrbot_plugin_stealer/main.py:241`）。
- `\meme auto_on` / `\meme auto_off`：开启/关闭自动发送（`data/plugins/astrbot_plugin_stealer/main.py:256`）。
- `\meme send <分类>`：在当前会话随机发送该分类的一张表情包（`data/plugins/astrbot_plugin_stealer/main.py:270`）。
- `\meme set_vision <provider_id>`：设置视觉模型 Provider（`data/plugins/astrbot_plugin_stealer/main.py:286`）。
- `\meme set_text <provider_id>`：设置文本模型 Provider（`data/plugins/astrbot_plugin_stealer/main.py:301`）。
- `\meme show_providers`：查看当前使用的视觉/文本模型（`data/plugins/astrbot_plugin_stealer/main.py:316`）。
- `\meme bind <alias>`：将当前会话绑定到别名（`data/plugins/astrbot_plugin_stealer/main.py:296`）。
- `\meme unbind <alias>`：取消别名绑定（`data/plugins/astrbot_plugin_stealer/main.py:305`）。
- `\meme push <分类> [alias]`：主动向当前或指定别名会话推送该分类的一张表情包（`data/plugins/astrbot_plugin_stealer/main.py:315`）。

## 工作流说明
- 图片入库与分类：
  - 监听图片，落盘到 `raw/` 与 `categories/<分类>/`（`data/plugins/astrbot_plugin_stealer/main.py:157`）。
  - 使用会话或配置指定的视觉模型进行多模态分类，见 `data/plugins/astrbot_plugin_stealer/main.py:128`。
  - 分类索引存储于 `index.json`（`data/plugins/astrbot_plugin_stealer/main.py:112`）。
- 文本情绪与自动发送：
  - 在 `on_llm_response` 后调用 `_classify_text_category`，按分类目录随机挑选并发送（`data/plugins/astrbot_plugin_stealer/main.py:171`、`:186`）。
- 主动消息：
  - 使用 `event.unified_msg_origin` 或别名映射的 UMO，通过 `self.context.send_message(umo, MessageChain)` 发送（`data/plugins/astrbot_plugin_stealer/main.py:333`）。

## 数据存储结构
插件持久化数据位于 `data/plugin_data/astrbot_plugin_stealer/`：
- `raw/`：原始图片备份
- `categories/<分类>/`：按分类存储的图片
- `index.json`：图片到分类与标签的索引
- `config.json`：运行态开关与分类列表
- `aliases.json`：别名到会话标识的映射

## 生产建议
- 若主回复模型不支持图片输入，请在配置中设置 `vision_provider_id` 为支持图片的模型（如 4o/Claude/Gemini Vision）；`text_provider_id` 可设置为主回复模型。
- 分类集合可按需要在配置文件或代码中扩展（默认包含：开心、搞怪、无语、伤心、愤怒、害羞、震惊、奸笑、哭泣、疑惑、尴尬、其它）。

## 依赖与规范
- 使用 AstrBot 提供的 Provider 接口进行 LLM/多模态调用：
  - 获取会话 Provider：`await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)`（`data/plugins/astrbot_plugin_stealer/main.py:200`）。
  - LLM 生成：`await self.context.llm_generate(chat_provider_id=prov_id, prompt=..., image_urls=[...])`（`data/plugins/astrbot_plugin_stealer/main.py:131`）。
- 避免使用 `requests`，遵循仓库的异步网络库与编码规范，提交前使用 `ruff` 格式化与检查。

## 许可证
遵循仓库主项目的开源许可。
