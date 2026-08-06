# 便签 (Notally 二次开发版)

> [!IMPORTANT]
> 本项目是基于 **OmGodse/Notally** 的二次开发（fork）作品，原作者为 Om Godse。
> 原项目仓库：**https://github.com/OmGodse/Notally**（GPL v3 许可）
> 原项目在 Google Play 与 F-Droid 发布，本仓库仅为个人定制版本，与上游保持独立。

## 本项目的改动

- 应用名由 "Notally" 改为「**便签**」
- 主题色改为锤子便签风格红色（#D0021B），日间/夜间模式同步调整
- 应用图标背景改为红色系
- 其余功能与原版 Notally v6.2 一致

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
