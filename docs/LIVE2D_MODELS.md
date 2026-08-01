# 获取、制作与导入 Live2D 模型

HatTip Lab 不内置 Live2D 模型。你需要自己获取或制作，然后右键 → "导入自己的模型" 即可。

---

## 🛒 购买模型

### 推荐平台

| 平台 | 特点 | 价格区间 | 链接 |
|------|------|---------|------|
| **nizima** | 最大 Live2D 市场，日系/acg 为主，可按格式/价格筛选 | ¥30-500+ | https://nizima.com |
| **Booth** | 同人/独立创作者，便宜且风格多样 | ¥10-200 | https://booth.pm/zh-cn/browse/Live2D |
| **Live2D 官方商店** | 官方授权，质量最高 | ¥100-1000+ | https://shop.live2d.com |
| **FANBOX / Patreon** | 画师赞助页，月付可得模型 | ¥30-100/月 | 搜 "Live2D FANBOX" |

### 筛选建议

搜模型时注意文件格式，HatTip Lab 支持：
- `.model3.json` — **Cubism 3/4/5**（推荐，质量好）
- `.model.json` — Cubism 2.1（旧格式，也能用）
- ZIP 包直接导入

nizima 上勾选 **Cubism 4.0** 即可。买之前看商品页是否有 `.model3.json` 或 `.moc3` 文件。

### 便宜好用的模型示例

| 模型 | 平台 | 参考价 |
|------|------|--------|
| しずく (Shizuku) | 官方免费 | ¥0 |
| 白上フブキ風 | Booth | ~¥50 |
| アオイ (Aoi) | nizima | ~¥80 |

---

## 🆓 免费模型

| 来源 | 说明 | 链接 |
|------|------|------|
| **Live2D 官方示例** | Cubism SDK 附带的 Haru、Hiyori、Mark 等 | https://www.live2d.com/en/learn/sample/ |
| **nizima 免费区** | 部分创作者提供免费模型 | https://nizima.com 搜 "無料" |
| **Booth 免费** | 搜索 "Live2D 無料" 或 "フリー" | https://booth.pm |

> ⚠️ 免费模型也需要遵守各自的使用条款。部分禁止商用或需署名。

---

## 🎨 自己制作

### 工具

| 工具 | 用途 | 价格 |
|------|------|------|
| **Live2D Cubism Editor** | 绑骨骼、做动作、导出 | 免费版够用（PRO 付费） |
| Photoshop / Clip Studio | 画分层 PSD | 付费 |
| Krita | 免费替代 PS | 免费 |
| ComfyUI + SD | AI 辅助生成角色图 | 免费（你有） |

### 最小制作流程

```
1. 画分层 PSD（头发前/脸/眼睛/嘴/身体/头发后）
2. 导入 Cubism Editor → 绑骨骼
3. 做参数（AngleX/Y/Z、EyeOpen、MouthOpen 等）
4. 做动作（眨眼、呼吸、说话口型）
5. 导出 → .model3.json + .moc3 + 贴图
6. 右键 → 导入 HatTip Lab
```

新手第一次约 4-8 小时，熟手 2 小时。

---

## ⚡ 快捷方式：GIF/APNG 桌宠

如果不想做 Live2D，HatTip Lab 的 GIF 模式可以直接用：

```
ComfyUI 生成角色 → 视频模型做 2-3 秒循环动画 → 导出 APNG
```

替换 `web/assets/pet-placeholder.gif` 即可。

---

## 📥 导入步骤

1. 右键角色 → "导入自己的模型"
2. 选择 `.model3.json` / `.model.json` / ZIP
3. 在模型下拉框中选择
4. 点击 "使用 Live2D"

模型文件会复制到 `%LOCALAPPDATA%\HatTipLab\models\`。

---

## 🔧 安装 Cubism Core

1. 从 [Live2D 官方 SDK](https://www.live2d.com/download/cubism-sdk/) 下载 Web SDK
2. 解压后找到 `live2dcubismcore.min.js`
3. 右键 → "安装 Cubism Core" → 选择该文件

Cubism Core 只复制到你的本地用户目录，不会上传或随程序分发。
