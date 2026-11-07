# 本地开发环境

Python 虚拟环境放在内置盘，避免仓库所在 ExFAT 卷的 AppleDouble (`._*`) 文件干扰依赖扫描。

## 启用环境

在仓库目录运行：

```sh
source ./env.sh
python --version
python -m pip check
pymobiledevice3 usbmux list
```

`IPHONE_LAYOUT_VENV` 可覆盖默认环境路径 `$HOME/.local/share/iphone-layout-tool/venv`。

## 重建环境

```sh
python3 -m venv "$HOME/.local/share/iphone-layout-tool/venv"
source ./env.sh
python -m pip install -r requirements.txt
```

`requirements.txt` 固定直接依赖版本；`requirements-lock.txt` 记录本次 macOS / Python 3.12 安装的完整依赖版本。

## 连接手机

1. 使用支持数据传输的 USB 线连接 iPhone 和 Mac。
2. 解锁手机；若 Mac 提示允许配件连接，选择允许。
3. 若 iPhone 提示“信任此电脑”，点信任并在手机上输入密码。
4. 运行 `pymobiledevice3 usbmux list` 检查设备发现。

设备发现成功不等于已完成信任配对，也不等于已验证布局读写兼容性。
当前只读调研阶段不需要先开启开发者模式，也不需要监管或抹掉设备。

## Apple Configurator

从 Mac App Store 安装 Apple Configurator：
https://apps.apple.com/app/id1037126344

安装完成后检查：

```sh
"/Applications/Apple Configurator.app/Contents/MacOS/cfgutil" help
```

Configurator 用于后续调查写入能力；读取布局可以先用 pymobiledevice3。
暂不运行 `push_layout.sh`：现有转换器尚不能无损保留小组件、智能叠放和空位。

## 数据存放

真机导出数据放在 `layouts/`（已被 Git 忽略）。原始布局导出是调研材料，不代表已经具备完整恢复能力。

## 下一次只读采集

新的原生快照采集器已独立于旧转换/推送流程，使用说明见
[下一次连接手机](docs/NEXT_PHONE_CONNECTION.md)。先运行 `python capture_snapshot.py doctor` 做离线检查。
默认真机数据存放在内置盘 `$HOME/.local/share/iphone-layout-tool/snapshots/`，不会加入本仓库。
