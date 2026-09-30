# 第三方组件

发布包里包含以下第三方组件，各自按其许可证分发。本项目自己的代码与资源按 AGPL-3.0（仅第 3 版）发布，见 `LICENSE`，附加条款见 `TERMS_OF_SERVICE.md` 第 2 条。它们不适用于下面这些组件。

| 组件 | 版本 | 许可证 | 源码 | 包里的许可证文本 |
|---|---|---|---|---|
| [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)（MFAA 包） | v2.16.2 | GPL-3.0 | [v2.16.2](https://github.com/SweetSmellFox/MFAAvalonia/tree/v2.16.2) | `LICENSE-MFAA` |
| [MXU](https://github.com/MistEO/MXU)（MXU 包） | v2.6.1 | AGPL-3.0 | [v2.6.1](https://github.com/MistEO/MXU/tree/v2.6.1) | `LICENSE-MXU` |
| [MaaFramework](https://github.com/MaaXYZ/MaaFramework)（界面与 Agent 共用的动态库，来自 maafw 包） | 5.14.0 | LGPL-3.0 | [v5.14.0](https://github.com/MaaXYZ/MaaFramework/tree/v5.14.0) | `python/Lib/site-packages/maafw-*.dist-info/` |
| Python 嵌入式运行时 | 3.14 | PSF-2.0 | [python.org](https://www.python.org/downloads/source/) | `python/LICENSE.txt` |
| Python 依赖：numpy、opencv-python-headless、PyYAML、RapidFuzz、requests 等 | 见 `python/Lib/site-packages` | 各自的许可证 | PyPI | `python/Lib/site-packages/*.dist-info/` |
| OCR 模型 PP-OCRv6 small：[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 官方模型，[MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) 转换为 ONNX | MaaCommonAssets `dabcd46` | Apache-2.0（PaddleOCR）；MIT（MaaCommonAssets） | 见链接 | `resource/model/ocr/LICENSE-*.txt` |
| 软件图标 `docs/ui/icon.png`、`docs/ui/logo.ico`（MFAA 包里还有一份 `Assets/logo.ico`），取自《BanG Dream! It's MyGO!!!!!》动画官网 | — | 版权归 ©BanG Dream! Project，不是开源授权，见 `TERMS_OF_SERVICE.md` 2.5 | [原图](https://anime.bang-dream.com/mygo/wordpress/wp-content/themes/mygo_v1/assets/images/common/apple-touch-icon-180x180.png) | — |

界面程序与本项目的 Agent 是分开的程序，通过 MaaFramework 的 Agent 协议通信。

MaaFramework 动态库未经修改。按 LGPL-3.0 的要求，可以把它替换成自己编译的同版本库：MFAA 包在 `runtimes/win-x64/native/`，MXU 包在 `maafw/`，Agent 用的在 `python/Lib/site-packages/maa/bin/`。
