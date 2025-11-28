# AstrBot 表情包插件


## 简介
我想仿照麦麦的表情包偷取做个娱乐性的插件的，于是就有了
本插件可以自动偷取聊天中的图片，进行多模态理解与情绪分类，并在发送回复前按合适的概率追加一张匹配情绪的表情，提升互动体验。

## 主要功能
- 自动监听图片。
- 使用视觉/文本模型生成图片描述、标签与情绪分类。
- 在回复发送前追加 base64 图片，与文本同条消息链发出。
- 精简指令集，支持随机、按描述与按情绪检索与发送。

## 快速上手
1. 将插件目录放入 `AstrBot/data/plugins` 或在 Dashboard 插件中心安装。
2. 在后台配置或通过指令设置：
   - `meme set_text <provider_id>` 设置文本模型
   - `meme set_vision <provider_id>` 设置视觉模型
   - `meme auto_on` 开启自动随聊表情
3. 使用指令：
   - `meme random 1` 随机发送
   - `meme emotion 开心` 按情绪发送
   - `meme find 可爱` 按描述/标签检索
   - `meme status` 查看当前状态

## 注意事项
- 本插件为本人的实验性插件（ai做的），若出现bug还请提交issue
- 开启了视觉模型可能会比较消耗token，我也在找解决办法

## 配置与参数
  - `auto_send`：自动随聊追加开关
  - `emoji_chance`：自动追加概率（0.0–1.0）
  - `max_reg_num`：图片上限（超出按用量与时间淘汰）
  - `do_replace`：达到上限时是否淘汰旧图片
  - `check_interval`：扫描系统表情目录周期（分钟）
  - `steal_emoji`：是否启用系统表情目录自动入库
  - `content_filtration` / `filtration_prompt`：合规审核参数
  - `vision_provider_id` / `text_provider_id`：模型提供商选择


