# 下一次连接手机：只读采集

离线准备目标：采集工具可在没有手机的情况下验证，下一次插线用于获取真实原生响应。
本阶段不整理或推送桌面，不测试恢复；也不接受 Xcode 许可或开启开发者模式。

## 连接前的离线检查

在项目目录运行：

```sh
source ./env.sh
python capture_snapshot.py doctor
python -m unittest discover -s tests -v
```

`doctor` 只导入和检查已安装库的 API，不发现、连接或操作手机。
通过说明代码能调用本机接口，不代表 iOS 27.2 的每个读取接口已成功实测。

## 连接后

1. 用支持数据传输的 USB 线连接之前那台 iPhone，解锁并保持屏幕点亮。
2. 保持桌面布局不变；采集期间不要安装 App、拖动图标或切换组件配置。
3. 若尚未完成信任关系，在 Finder/手机上自行完成“信任此电脑”。采集器禁止自动配对。
4. 查看本地 USB 设备列表：

```sh
python capture_snapshot.py devices
```

5. 将列表中目标手机的 UDID 明确填入命令；工具不会自动猜测设备，也不会切换到 Wi-Fi：

```sh
python capture_snapshot.py capture --udid '<目标手机的 UDID>'
```

操作由同一项目中的工具完成，不需安装或启动其他开源项目。默认每次读取超时为 20 秒。
单步状态会显示在终端；读取失败不会重试或触发写入。

## 保存内容

默认目录：`$HOME/.local/share/iphone-layout-tool/snapshots/<时间戳>_<随机后缀>/`。
位于内置盘，避免当前仓库所在 ExFAT 卷的元数据和权限差异。
每次创建新目录，文件按原子替换方式落盘，不覆盖以前的采集。

| 文件 | 用途 |
|---|---|
| identity-before.plist / identity-after.plist | 前后核对 UDID、机型、系统和构建版本；只保存选定身份字段 |
| layout-v2.plist / layout-v2-after.plist | 保存原生格式 2 读取结果，核对采集期间响应是否变化 |
| layout-v3.plist | 格式 3 原生响应参考；设备可能不支持，错误响应也尽量留存 |
| apps.plist | 请求所有应用类型，并启用占位应用返回；保留原始元数据和隐藏标记 |
| metrics.plist | 设备报告的网格指标；不支持时记录失败 |
| manifest.json | 每步结果、文件哈希、组件概览、工具版本和限制 |

布局、App 清单和设备信息留在本地。程序不上传数据，也不导出配对证书或私钥。
完整响应使用 binary plist，保留 datetime、bytes、未知对象字段。摘要不替代原始文件。
这仍然是研究快照，不是完整手机备份，也不是可推送布局。

## 结果解释

- `complete`：关键读取成功，前后设备身份和布局响应一致；格式 3 / 网格指标仍可能有单独的失败记录。
- `changed_during_capture`：关键读取完成，但前后响应有变化，需核对后重新采集。
- `partial`：关键读取失败；已经采到的原始文件仍保留，错误记录在 manifest 中。
- `interrupted`：采集被取消，保留已采集内容。强制结束进程可能仅留下 `in_progress`。

`ready_for_device_write` 在这一阶段始终为 false。
即使前后列表相同，也没有验证留白坐标、隐藏页面、组件内容或 App 资源库精确归属。
下一步是对照原生响应、完整 App 清单与手机画面，决定字典写入草案的生成规则。
