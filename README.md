# AI 媒体标签工具

一款面向 Windows 的离线桌面工具，为 JPG、JPEG、PNG 和 MP4 文件写入 XMP
`dc:Subject` 标签：

```text
contains-synthetic-performer
```

项目由 `luoxu-ai` 维护，以 Apache License 2.0 开源。

[隐私政策](PRIVACY.md) · [代码签名政策](CODE_SIGNING_POLICY.md) ·
[模型说明](MODEL_CARD.md)

## 主要功能

- 拖入文件或文件夹，递归收集 JPG、JPEG、PNG、MP4，自动忽略其他格式。
- 普通导出：为勾选文件生成带 `_AI` 后缀的副本并写入标签。
- 智能识别：图片离线检测人脸、人物、侧身和背影；MP4 按业务规则直接标记。
- 写入后重新读取 XMP 进行验证，不覆盖已有文件。
- 支持中文用户名、中文路径和中文文件名。
- 单实例运行、列表快捷删除、处理日志和 SHA-256 发布校验。

## 重要的数据安全说明

智能识别导出会先在原目录生成 `_AI` 文件，完成标签复核后再尝试将原文件送入
回收站；如果回收站操作不可用，当前版本可能永久删除原文件。因此，请先使用
副本测试，并自行备份重要素材。未检测到人物或处理失败的文件会保留原文件。

人物识别模型不是合规判断的替代品，可能误检或漏检，输出结果仍应由使用者复核。

## 源码结构

```text
qt_app.py             Qt 桌面界面与任务流程
core.py               文件收集、ExifTool 调用、导出与验证
person_detector.py    离线人物与人脸检测
models/               程序实际使用的 ONNX 模型
assets/               应用图标
tests/                自动化测试
prepare.ps1           创建环境并下载 ExifTool
build.ps1             构建 Windows EXE
```

训练图片、人工标注数据、商品素材和内部测试报告不属于开源仓库，也不是构建应用
所必需的内容。

## 本地运行

建议使用 64 位 Windows 10/11 与 Python 3.11 或 3.12。在 PowerShell 中执行：

```powershell
.\prepare.ps1
.\.venv\Scripts\python.exe .\qt_app.py
```

`prepare.ps1` 会从官方发布源下载 ExifTool，并安装 `requirements.txt` 中声明的
Python 依赖。

## 构建 EXE

```powershell
.\build.ps1
```

构建结果位于 `dist` 目录。自行构建的未签名 EXE 可能触发 Windows SmartScreen；
这不代表代码签名或信誉已经建立。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

涉及 ExifTool 的集成测试需要先运行 `prepare.ps1`。

## 第三方组件

本项目使用 D-FINE、YuNet/OpenCV Zoo、OpenCV、ExifTool、PySide6、ONNX Runtime、
NumPy 和 Pillow。详细来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

本项目源代码采用 [Apache License 2.0](LICENSE) 发布。

## 代码签名状态

项目当前发布物尚未获得可信代码签名，正在准备 SignPath Foundation 开源项目
申请。请仅从本仓库的 Releases 页面获取官方发布物，并核对随附的 SHA-256。
