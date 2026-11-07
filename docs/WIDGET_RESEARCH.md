# Widget 布局写入研究

> 本文是首次写入前的研究记录。后续已经在用户设备完成一次原生写入与恢复；当前实测范围见 [链路记录](END_TO_END.md)。

研究日期：2026-11-07。范围：公开源代码审阅、本机已安装库检查、既有 iPhone 快照离线检查。
没有连接手机、调用布局写入、运行外部项目的安装脚本或启动外部服务。

## 结论和证据级别

存在值得实施验证的 SpringBoard 字典写入路线。现有 cfgutil 简化格式不适合作为小组件编辑器的内部数据模型。
这不是 iPhone 17 Pro Max / iOS 27.2 写入兼容性结论。

| 问题 | 证据 | 本项目当前状态 |
|---|---|---|
| 读取小组件对象 | 本机原始快照含 5 个中号组件 | 已真机确认 |
| 字典形式 setIconState | 两个外部项目有具体发送/构造代码 | 已审阅代码，未在用户手机测试 |
| 配置标识关联 | 外部代码处理 selected/unique 标识；天气组件有对应字段 | 有匹配证据，未验证配置保持 |
| 智能叠放 | 外部数据模型支持 elements | 用户快照没有 elements，不能验证 |
| 已有留白 | 外部项目报告 USB 列表不含坐标，用户导出也没有坐标 | 无法从现有快照恢复 |
| 电脑新建的留白 | 外部代码使用页面 metadata 和 fixedLocations | 尚未实现或验证 |
| 新建小组件、修改内容或尺寸 | 本次没有找到足以保证行为的证据 | 不纳入第一版写入目标 |

## 1. 从 cfgutil 转向保留对象的内部模型

[Homescreen 的 device.py](https://github.com/kats1123/homescreen-mcp/blob/main/homescreen/device.py)
提供 `to_root_archive` 和 `_modern`：将列表读取结果转换为含 `buttonBar`、`iconLists` 和 `ignored` 的字典。
普通图标按显示标识引用；组件保留对象结构。代码还显式补回配置实例标识。
本文只概述实现原理；具体转换规则、栈和未知类型必须另行验证，不能直接照搬成已验证的写入器。

[其 README 的测试范围](https://github.com/kats1123/homescreen-mcp#requirements)
报告了 iPhone 17 Pro Max / iOS 27.0.1 和 iOS 27.2 模拟器。这是作者报告，不能等同于用户设备验证。

对项目的影响：旧 README 的“setIconState 已失效”是历史论断，不应再作为当前能力边界。

## 2. 配置关联有具体线索，但验证标准应更严格

[Rejig 的 widget-ids 补丁](https://github.com/rejig-app/rejig/blob/main/patches/widget-ids.patch)
将单组件返回的 `userSelectedElementIdentifier` 用作缺失的 `uniqueIdentifier`，避免配置失去关联。
栈里的 selected 字段另有含义，不能把单组件规则套到栈上。

用户快照中的天气组件有 selected 标识，但没有 unique 标识；其余四个组件均没有这两个字段。
缺失不证明它们没有配置，也不能凭空生成 UUID 当作已保留配置。

建议验证结果区分三种：匹配、不匹配、信息不足。读回省略标识时归为信息不足，
不能把“字段没返回”当成“配置已经验证一致”。天气城市、FotMob 选定球队等仍需在屏幕检查。

## 3. App 资源库不能由桌面快照单独重建

[ios-homescreen 的 state.py](https://github.com/YakupEmreYerli/ios-homescreen/blob/main/src/ios_homescreen/state.py)
构造 `ignored` 时依赖已安装 App 清单，以避免未列出的 App 被补回桌面。
当前用户快照没有这份清单，因此不生成可推送载荷，更不能随意填 `ignored=[]`。

下一次采集应包含已卸载但保留数据的占位应用、系统 App 和隐藏标记。
本机 pymobiledevice3 11.50.0 的 InstallationProxyService.get_apps 支持 `show_placeholders=True`。
隐藏系统服务、网页快捷方式和特殊图标要分别核对，不能只做简单的字符串差集。

## 4. 发送成功与布局验证是两回事

[ios-homescreen 的 device.py](https://github.com/YakupEmreYerli/ios-homescreen/blob/main/src/ios_homescreen/device.py)
采用带超时的底层 plist 发送，之后以新连接读回验证，避免无限等待回复。
本机已安装的 pymobiledevice3 `set_icon_state` 会等待一个前缀响应；直接调用前必须处理超时和不确定状态。

外部项目在等待响应、忽略字段和标识比较上的策略并不完全相同，不能把任一实现当成协议保证。
下一版必须明确：超时不是未写入；读回失败是状态未知，不能盲目重试或自动恢复。
恢复同样是一次写入，要重新确认设备身份、当前状态和备份的适用性。

## 5. 留白和空间预览

[Rejig 的留白补丁](https://github.com/rejig-app/rejig/blob/main/patches/pinned-gaps.patch)
提供页面固定位置的实现线索。该补丁主要针对 App/网页图标；不要据此宣称 Widget 也能精确定位。

[Rejig 的限制记录](https://github.com/rejig-app/rejig#limits)
指出手机原有空位可能在首次写入时消失，即便只改 Dock；读回顺序检查也可能通过。
因此“最小逻辑修改”不等于“只影响这几个屏幕位置”。此问题在用户真机尚未复现。

产品应区分原始顺序和用户确认的空间布局：没有坐标证据时，预览只能标成重排推测，不能标成原样还原。
屏幕截图可以辅助确认位置；对于隐藏页、显示缩放或组件尺寸仍需核对。

## 6. 下一阶段实现与采集顺序

1. 建立保留所有原始字段的内部模型；稳定引用优先使用实例的显示标识，不能只用父 App bundle ID。
2. 增加只读采集器：显式指定 USB 和 UDID；保存格式 2 / 3 的原生响应、设备身份、网格指标及完整 App 清单。
   使用 binary plist 保存 datetime/bytes；JSON 仅作为查看副本。格式 3 是否提供额外信息须真机查看。
3. 构造仅供离线审阅的写入草案，标明资源库推断、组件标识变换和未验证坐标；没有清单或身份就拒绝构造。
4. 离线回归：重复 App 实例、同 App 多组件、栈、配置别名、未知类型、网页图标、隐藏和占位 App。
5. 真机试验前重新读取、记录截图和源指纹；确认试验范围，再验证写回及恢复。
   先保持组件类型、内容和尺寸，只研究移动已有对象；不在本轮启用自动写入。

## 7. 已完成的本机检查和限制

- 用户快照逐字段检查：5 个中号组件、4 个显示标识与 bundle ID 不同的特殊 App 图标、3 个文件夹。
- 原始快照 SHA-256 在离线检查前后保持一致；设备证据另存于被 Git 忽略的 layouts/ 目录。
- JSON 转 binary plist 再读回相等。但 JSON 已将日期等类型转为字符串；此练习无法恢复原生类型，不能作为无损设备备份。
- 外部源代码下载到内置盘独立研究目录，没有作为依赖安装或执行。
- GitHub git 传输未连接成功；API 提交查询被限流。未取得提交 SHA，故来源为研究时下载的 main 版本，
  已记录文件 SHA-256 方便核对；正式复用之前需固定提交并审查许可。
- 模拟器检查被 Xcode 未接受许可阻止，本轮没有接受许可或宣称完成模拟器验证。

## 来源和复用

上述项目为独立开源实现，不是 Apple 对私有协议的保证。本轮未复制它们的代码进入产品。
若之后复用源码，需固定版本并保留相应许可声明；pymobiledevice3 的 GPL 许可也应在分发设计时一并处理。
本研究没有建立新的写入功能，现有转换保护继续生效。
