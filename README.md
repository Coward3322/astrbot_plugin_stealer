# 🌟 表情包小偷

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
![Python Version](https://img.shields.io/badge/Python-3.10.14%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen)](CONTRIBUTING.md)
[![Last Commit](https://img.shields.io/github/last-commit/nagatoquin33/astrbot_plugin_stealer)](https://github.com/nagatoquin33/astrbot_plugin_stealer/commits/main)

</div>

## 📑 目录

- [🌟 表情包小偷](#-表情包小偷)
  - [📑 目录](#-目录)
  - [📢 简介](#-简介)
  - [🚀 功能特点](#-功能特点)
  - [📦 安装方法](#-安装方法)
  - [🛠️ 快速上手](#️-快速上手)
  - [⚙️ 配置说明](#️-配置说明)
  - [📝 使用指令](#-使用指令)
  - [⚠️ 注意事项](#️-注意事项)
  - [📄 许可证](#-许可证)

## 📢 简介

我想仿照麦麦的表情包偷取做个娱乐性的插件的，于是就有了这个表情包小偷插件！

本插件可以自动偷取聊天中的图片，进行多模态理解与情绪分类，并在发送回复前按合适的概率追加一张匹配情绪的表情，提升互动体验。


## 主要功能
- 自动监听图片。
- 使用视觉/文本模型生成图片描述、标签与情绪分类。
- 在回复发送前追加 base64 图片，与文本同条消息链发出。
- 精简指令集，支持随机、按描述与按情绪检索与发送。

## 📦 安装方法

1. 确保已安装 AstrBot
2. 将插件复制到 AstrBot 的插件目录：`AstrBot/data/plugins`
3. 或在 Dashboard 插件中心直接安装
4. 重启 AstrBot 或使用热加载命令

## 🛠️ 快速上手

### 1. 模型配置

设置文本模型（用于情绪判断）：
```bash
meme set_text <provider_id>
```

设置视觉模型（用于图片分类）：
```bash
meme set_vision <provider_id>
```

> **注意**：视觉模型需要支持图片输入（如 Gemini, 豆包, qwen vl 等）

### 2. 功能开启

开启自动随聊表情：
```bash
meme auto_on
```

关闭自动随聊表情：
```bash
meme auto_off
```

### 3. 基本使用

随机发送表情包：
```bash
meme random 1
```

按情绪发送匹配的表情包：
```bash
meme emotion 开心
```

按描述/标签检索表情包：
```bash
meme find 可爱
```

查看当前插件状态：
```bash
meme status
```

## ⚙️ 配置说明

插件提供以下配置选项，可在后台或配置文件中设置：

| 配置项 | 类型 | 默认值 | 描述 |
|--------|------|--------|------|
| `auto_send` | bool | true | 是否自动随聊追加表情包 |
| `emoji_chance` | float | 0.4 | 触发表情动作的基础概率 |
| `max_reg_num` | int | 100 | 允许注册的最大表情数量 |
| `do_replace` | bool | true | 达到上限时是否替换旧表情 |
| `check_interval` | int | 10 | 扫描/清理/注册的轮询周期（分钟） |
| `steal_emoji` | bool | true | 允许自动从 data/emoji 注册新表情 |
| `content_filtration` | bool | false | 是否开启内容审核 |
| `filtration_prompt` | string | "符合公序良俗" | 内容审核提示词 |
| `emoji_only` | bool | true | 是否仅偷取聊天表情包（过滤普通图片/截图/长图） |
| `vision_provider_id` | string | null | 视觉模型提供商ID |
| `text_provider_id` | string | null | 文本模型提供商ID |

## 📝 使用指令

| 指令 | 描述 |
|------|------|
| `meme set_text <provider_id>` | 设置文本模型 |
| `meme set_vision <provider_id>` | 设置视觉模型 |
| `meme show_providers` | 查看当前模型配置 |
| `meme auto_on` | 开启自动随聊表情 |
| `meme auto_off` | 关闭自动随聊表情 |
| `meme random <num>` | 随机发送指定数量的表情包 |
| `meme emotion <emotion>` | 按情绪发送匹配的表情包 |
| `meme find <keyword>` | 按描述/标签检索表情包 |
| `meme status` | 查看当前插件状态 |
| `meme emoji_only <on/off>` | 切换仅偷取表情包模式 |

## ⚠️ 注意事项

- 本插件为本人的实验性插件（AI做的），若出现bug还请提交issue
- 开启视觉模型可能会比较消耗token，我也在找解决办法
- 建议根据实际情况调整表情包偷取的概率和数量
- 内容审核功能需要配合合适的提示词使用

## 📄 许可证

本项目基于 MIT 许可证开源。

[![GitHub license](https://img.shields.io/github/license/nagatoquin33/astrbot_plugin_stealer)](https://github.com/nagatoquin33/astrbot_plugin_stealer/blob/main/LICENSE)


