# iPhone Home Screen Layout Tool

从 Mac 读取 iPhone 桌面，离线生成变更计划，通过 USB 应用并核对结果，再恢复原始布局。
目前是命令行原型，第一条真机链路已跑通；可视化编辑器和 LLM 方案导入尚未实现。

## 当前验证结果

2026-11-07，在 iPhone 17 Pro Max、iOS 27.2（24B119）上完成：

**原生采集 → 计划与差异 → 交换 Dock 的 Safari / 邮件 → 全桌面读回核对 → 恢复 → 再核对。**

恢复后的原生布局响应与试验前逐字段完全相等。用户检查并确认 Dock、组件内容、位置和留白正常。
这证明一次具体试验成功，不保证所有机型、组件或故障场景。完整记录与边界见 [END_TO_END.md](docs/END_TO_END.md)。

## 现有能力

- 只读采集：显式指定受信任的 USB 手机，保存原生 binary plist、设备身份、App 清单及网格指标。
- 离线计划：当前支持一次同页条目交换；保留原始对象，不允许注入新组件或任意 wire payload。
- 差异审阅：显示具体位置变化、组件字段转换、资源库推断和计划 token。
- 原生写入：使用 SpringBoard 字典结构，独立于旧 cfgutil 转换器。
- 写前检查：核对具体设备、系统构建、源布局、App 清单和网格；创建事务快照。
- 写后核对：检查所有页面和可读的组件标识，不只检查 Dock。每次事务最多发送一次。
- 恢复：支持恢复已通过结构核对的应用事务；有后续编辑时拒绝覆盖。
- 离线回归：覆盖正常往返、过期/损坏计划、设备变化、组件 ID、断线、超时和取消等。

## 环境

本项目使用 Python 3.12，设备依赖固定在 `requirements.txt`。
已有本机环境可运行：

```sh
source ./env.sh
python capture_snapshot.py doctor
```

环境安装与 ExFAT 外置盘注意事项见 [ENVIRONMENT.md](ENVIRONMENT.md)。
独立虚拟环境和真机数据默认放在内置盘，不修改系统 Python。
原生链路不需要安装 Apple Configurator；它仅用于保留的历史 cfgutil 路线。

## 只读采集

```sh
python capture_snapshot.py devices
python capture_snapshot.py capture --udid '<目标手机的 UDID>'
```

采集器只连接 USB，不自动创建信任配对，没有写入或恢复入口。
默认数据存放在 `$HOME/.local/share/iphone-layout-tool/snapshots/`。
格式 3 在已测设备上是压缩分组的列表，不能当作真实屏幕坐标。详见 [采集说明](docs/NEXT_PHONE_CONNECTION.md)。

## 生成和审阅计划（离线）

```sh
python layout_tool.py plan-swap --capture '<采集目录>' --page 0 --indexes 1 2
python layout_tool.py review '<生成的计划.plist>'
```

`page=0` 是 Dock，其他页面从 1 开始；条目序号从 0 开始。
计划带完整源对象、明确操作和设备身份，审阅输出 `plan_token`。
更改任何数据都需要重新生成/审阅计划。

## 执行试验（会写入手机）

```sh
python layout_tool.py trial --plan '<计划.plist>' --plan-token '<审阅输出的 token>'
```

trial 应用变更并核对，成功后恢复原始顺序并再次核对。
也支持分开执行 `apply` / `restore`，具体命令、记录位置与状态解释见 [链路说明](docs/END_TO_END.md)。

发送报错不等于手机没变化：工具会读回判断，不会因报错直接重发。
读回无法证明结果时，状态为 `write_outcome_uncertain`，保留事务证据并停止。
当前自动恢复只覆盖已成功核对的应用；没有宣称覆盖所有失败状态的恢复能力。

## 限制与下一阶段

- 现有操作语言只有一次同页 swap，多页移动与文件夹编辑还需扩展。
- 组件创建、尺寸/内容编辑和真实智能叠放尚未真机测试。
- 原生读取不提供原有留白的可靠坐标。已有留白可能受到整套布局导入影响，即使只改 Dock。
- 自动核对不能证明组件实际内容和像素位置；本次试验另由用户确认。
- 资源库由完整 App 清单推断，不能将结构核对成功当作资源库精确归属证明。
- 布局快照不是完整 iPhone 备份；重启后的恢复和故障恢复尚未实机测试。

LLM 接入设计见 [LLM_DESIGN_INTEGRATION.md](docs/LLM_DESIGN_INTEGRATION.md)。
方向是“设计视图 → 结构化对象引用 → 本地校验 → 差异预览 → 设备计划”，目前没有调用外部模型或上传应用清单。

## 测试与文件

```sh
python -m unittest discover -s tests -v
```

| 文件 | 用途 |
|---|---|
| `capture_snapshot.py` | 独立只读采集与离线环境检查 |
| `layout_snapshot.py` | 原生对象存储、实例识别与摘要 |
| `layout_plan.py` | 计划构造、格式转换、约束与读回比较 |
| `layout_transaction.py` | USB 写入、事务快照、读回核对与受约束恢复 |
| `layout_tool.py` | plan-swap / review / apply / trial / restore 命令行入口 |
| `convert_layout.py` | 旧 cfgutil 转换与离线检查；遇到无法表示的对象时拒绝 |
| `push_layout.sh` | 历史 cfgutil 推送脚本，不用于当前 Widget 链路 |

原项目的历史背景保存在 [LEGACY_CFGUTIL.md](docs/LEGACY_CFGUTIL.md)。
协议研究来源见 [WIDGET_RESEARCH.md](docs/WIDGET_RESEARCH.md)；私有接口不构成 Apple 的兼容性承诺。
