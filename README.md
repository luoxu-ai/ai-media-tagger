# AI 媒体标签工具

面向 Windows 的本地批量媒体处理工具。软件离线扫描图片中的人物，并为通过识别的图片副本写入固定 XMP 标签 `contains-synthetic-performer`；MP4 按业务规则直接处理。

## 主要功能

- 文件和文件夹递归导入，支持批量处理与进度显示。
- 人物检测采用 D-FINE、YuNet 和本地 ONNX 复核模型组合。
- 保留原文件，导出带 `_AI` 后缀的副本并验证标签写入结果。
- 已有标签、未检测到人物、处理失败等状态在列表中保留显示。
- 支持撤销标签、持久化处理日志、深浅色主题和在线版本检查。
- 用户电脑仅执行 CPU 推理，不要求独立显卡。

## 隐私

图片识别、媒体扫描、标签写入和日志记录均在用户电脑本地完成。软件不会自动上传图片、文件名或处理日志。联网功能仅包括用户启用的 GitHub 版本检查、更新下载，以及用户主动打开的反馈页面。

完整说明见 [PRIVACY.md](PRIVACY.md)。私有训练图片、冻结测试图片、评测清单和本地报告均被 `.gitignore` 排除，不进入公开仓库和发布包。

## 使用方式

1. 将文件或文件夹拖入窗口，或点击“选择文件”“选择文件夹”。
2. 点击“智能识别并导出”。
3. 选择保存目录，等待处理完成。

软件不会修改原图。目标目录存在同名文件时会生成新名称，不会覆盖已有文件。

## 质量保障

- 冻结测试集按 SHA-256 去重和锁定，图片内容变化会中止评测。
- 新模型必须满足：漏检不增加、误检不增加、无处理错误、速度不低于基线的 80%。
- 内部多分类复核器区分真人、手脚、宠物、纯产品、假人模特、文字装饰等类别；样本不足或标签冲突时拒绝训练。
- 用户界面仍然只显示最终人物检测结果，不增加人工审核步骤。

私有训练数据、冻结评测集及内部训练说明不进入公开仓库；公开仓库保留运行时模型、测试代码和可复现构建脚本。

## 本地构建

建议使用 Python 3.12 和项目虚拟环境：

```powershell
python -m venv .train_venv
.\.train_venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\prepare.ps1
.\build.ps1
```

运行测试：

```powershell
.\.train_venv\Scripts\python.exe -m pytest -q
```

GitHub Actions 会在 Windows 环境中使用锁定版本依赖重新运行测试、构建安装包并生成 SHA-256 校验文件。

## 开源与签名

本项目使用 Apache License 2.0。第三方组件与模型许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，签名和发布策略见 [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)。

当前正式安装包尚未获得可信数字签名。SignPath Foundation 免费签名申请因项目公开影响力暂时不足而未获批准；软件在线更新会先验证 GitHub Release 提供的 SHA-256，未签名安装包还会在安装前再次提示用户确认。

ExifTool 由 Phil Harvey 开发：[ExifTool 官方网站](https://exiftool.org/)。
