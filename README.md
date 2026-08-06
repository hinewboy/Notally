# 樱花便签 (Notally 二次开发版)

> [!IMPORTANT]
> 本项目是基于 **OmGodse/Notally** 的二次开发（fork）作品，原作者为 Om Godse。
> 原项目仓库：**https://github.com/OmGodse/Notally**（GPL v3 许可）
> 原项目在 Google Play 与 F-Droid 发布，本仓库仅为个人定制版本，与上游保持独立。

## 本项目的改动

- 应用名由 "Notally" 改为「**樱花便签**」
- 主题色改为樱花粉（#FF8FAB），日间/夜间模式同步调整
- 应用图标背景改为樱花粉
- **完整中文本地化**：补齐全部 51 个缺失翻译，设置选项全部跟随系统语言
- **云同步**：新增注册/登录模块，可上传/下载笔记到云端（notebook.978021.xyz），支持网页浏览与导出
- 其余功能与原版 Notally v6.2 一致

## 云同步

- 服务器：`https://notebook.978021.xyz`（FastAPI + SQLite，代码在 `server/` 目录）
- 功能：注册登录、笔记上传/下载、网页浏览、TXT/JSON 导出
- 部署：`server/notebook-sync.service`（systemd）+ `server/nginx-notebook.conf`（nginx 反代 + HTTPS）

## 原版功能（来自上游 Notally）

* 桌面小部件（Widgets）
* 提醒（Reminders）
* 自动备份（Auto backup）
* 笔记内搜索（Search within notes）
* 可调节文字大小
* 支持 Android 5.0 (Lollipop) 及以上设备
* APK 体积约 1.4 MB（解压后 1.8 MB）
* 笔记支持颜色标记、置顶、标签分类
* 支持插入图片（JPG, PNG, WEBP）
* 支持导出为 TXT、JSON、HTML、PDF（保留格式）
* 富文本：加粗、斜体、等宽、删除线
* 可点击链接：电话号码、邮箱、网址

## 构建

```bash
# 需要 JDK 21、Android SDK (compileSdk 36)
./gradlew assembleDebug
```

APK 输出路径：`app/build/outputs/apk/debug/app-debug.apk`

## 许可

GPL v3 — 与上游 Notally 一致。详见 [LICENSE.md](LICENSE.md)。
