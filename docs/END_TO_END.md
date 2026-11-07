# 原生布局链路：计划、写入、核对与恢复

## 已验证范围

2026-11-07，在用户的 iPhone 17 Pro Max（iPhone18,2）、iOS 27.2（24B119）上完成一次 USB 试验：

1. 新采集当前设备状态与完整 App 清单。
2. 离线生成仅交换 Dock 中 Safari / 邮件位置的计划，并列出两个位置的变化。
3. 发送前重新核对设备、系统、布局、网格和 App 清单，保存原生快照与实际发送内容。
4. 发送一次 SpringBoard 字典布局；第一次完整读回即与目标结构一致。
5. 使用该次事务的原始快照恢复；第一次完整读回即与原结构一致。
6. 独立逐字段比较：恢复后的原生布局响应与写入前完全相等。
7. 用户确认：Dock、天气城市、FotMob 球队、组件位置与原有留白均正常。

只对这台设备和这次布局构成证据。未测试重启后恢复、设备断线后的实际恢复、
新建 Widget、修改组件尺寸或内容、真实智能叠放、不同机型及其他系统构建。
既有特殊实例标识、文件夹和组件在本次试验中均得到保留。

## 使用方式

```sh
source ./env.sh
python capture_snapshot.py capture --udid '<手机 UDID>'
python layout_tool.py plan-swap --capture '<采集目录>' --page 0 --indexes 1 2
python layout_tool.py review '<上一步生成的计划.plist>'
```

`page=0` 表示 Dock。第一版操作语言只支持在同一页面交换两个现有条目，不能注入任意对象。
计划使用 binary plist 保存原始数据，审阅结果给出该文件内容的 `plan_token`。
以下命令实际写入手机：

```sh
# 执行交换，核对结构成功后再恢复原始顺序。
python layout_tool.py trial --plan '<计划.plist>' --plan-token '<审阅输出的 token>'

# 也可以只应用；随后单独恢复这次应用的原始快照。
python layout_tool.py apply --plan '<计划.plist>' --plan-token '<token>'
python layout_tool.py restore --transaction '<apply 事务目录>' --plan-token '<token>'
```

计划绑定具体设备身份和源布局。设备、系统、App 清单或布局变更后，需重新采集和生成计划。
不要复用此前的旧计划猜测手机状态。

## 数据与执行边界

- plan/review 无设备访问；capture 是独立的只读程序。
- 计划中的 payload、差异和元数据必须能从源快照与操作重新计算出来，否则拒绝。
- 未知类型、重复实例、缺失 App 清单、无效页面容量和未验证组件尺寸会被拒绝。
- Widget 保留原始字段；单组件配置别名在需要时补为 uniqueIdentifier，变化会出现在审阅中。
- App 资源库通过完整 App 清单推断；241 条 ignored 是本次计划的推断结果，不能视为直接读取的资源库归属证明。
- 写入前在内置盘创建事务目录，保存原始布局、App 清单、身份、目标布局与 wire payload。
- 同一事务根目录中，同设备的事务互斥。每个事务最多发送一次布局。
- 发送异常之后仍做读回，不因“主机报错”推断手机没有写入。
- 核对包含所有页面、文件夹、组件实例和返回的配置标识；不会只看改动的 Dock。
- 读回省略配置标识时明确标为 inconclusive，不当作配置保持的证明。
- trial 只在应用结构已验证、无配置标识冲突时继续恢复。
- 若结果不确定，则保留证据并停止；不会在异常处理里重发或盲目覆盖。
- 当前 restore 仅支持已通过结构核对的 apply 事务，且要求手机仍处于该次读回的状态。
  断线或未匹配的结果需先检查事务证据；此版本没有宣称覆盖所有故障的恢复器。

## 结果与文件

默认数据目录位于 `$HOME/.local/share/iphone-layout-tool/`：

- `snapshots/`：原生只读采集。
- `plans/`：设备绑定的计划。
- `transactions/apply-*/`、`restore-*/`：每次发送的前后证据与 receipt.json。

`applied_structure_verified` 和 `restored_structure_verified` 只表示可读取的结构匹配。
它们不代表组件实际内容、像素位置或资源库精确归属已被自动验证。
`write_outcome_uncertain` 表示无法证明目标状态，不应再次运行旧计划。

本次人工检查单独记录在本机报告中，不修改自动检查的能力声明。
原生布局快照不等同于完整 iPhone 备份。

## 回归测试

```sh
python -m unittest discover -s tests -v
```

测试涵盖正常往返、CLI 计划/审阅/试验集成、过期计划、设备与清单变化、未知对象、
组件 ID 别名、配置缺失、发送后报错、断线、取消、损坏备份和后续编辑保护。
模拟设备测试不执行真实设备命令。真实结果另以上面的试验记录为依据。
