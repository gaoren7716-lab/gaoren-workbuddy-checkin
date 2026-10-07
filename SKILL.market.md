---
slug: gaoren-workbuddy-checkin
displayName: WorkBuddy 自动签到（自研版·零凭据）
name: gaoren-workbuddy-checkin
display_name: WorkBuddy 自动签到（自研版·零凭据）
display_name_en: WorkBuddy Auto Check-in (Zero-Credential)
summary: WorkBuddy 加油站每日签到 + 每月连签 + 猫猫旅行。零凭据设计：只通过 WorkBuddy 客户端自身的本机 RPC 通道发请求，脚本不读登录态、不解密 token、不落盘任何文件。纯 Python 标准库，MIT 许可。
version: 1.2.2
author: gaoren
license: MIT
homepage: https://github.com/gaoren7716-lab/gaoren-workbuddy-checkin
category: 自动化
tags:
  - 签到
  - 自动化
  - Buddy加油站
  - 连签
  - 积分
  - 猫猫旅行
  - 零凭据
  - 隐私
  - 零依赖
description: WorkBuddy「Buddy 加油站」每日签到自动化，作者 gaoren 自研，MIT 许可，零第三方依赖。当用户说"每天自动签到 / 自动领 Buddy 加油站积分 / 现在签个到 / 帮我签一下 / 检查签到环境 / 连签 / 每月连签 / 猫猫旅行 / 派猫猫 / Buddy加油站"时使用。零凭据设计：请求经 WorkBuddy 客户端自身的本机 RPC 通道转发，鉴权由客户端附加，脚本全程不接触账号凭据；带 doctor 环境自检、--probe 只读探测、合法状态集结果契约、活动结束自动识别。
description_zh: WorkBuddy 加油站每日签到与连签、旅行自动化。零凭据，纯标准库，MIT。
description_en: Zero-credential WorkBuddy check-in, monthly streak and travel skill. MIT, stdlib only.
agent_created: true
---

# WorkBuddy 自动签到（自研版·零凭据 v1.2.1）

一条命令办完每日积分：**签到 → 每月连签 → 猫猫旅行**，全部幂等。

> **本市场分发版 = 零凭据精简版**，只有 5 个文件。它不读登录态、不解密 token、
> 不写任何文件、不做进程内存操作。需要「客户端没开时也能跑」的兜底通道，请用
> [GitHub 完整版](https://github.com/gaoren7716-lab/gaoren-workbuddy-checkin)（含凭据模块，
> 需自行评估风险后再用）。

## 零凭据是什么意思

WorkBuddy 桌面端运行时，会在本机开一个 IPC 服务（Windows 命名管道 / POSIX unix socket），
并用**它自己的登录态**代发 HTTP 请求。本 Skill 只做一件事：连上这个本机通道，
发送「相对路径 + JSON body」，**鉴权头由客户端自己附加**。

所以本 Skill 的能力边界是：

| 它做 | 它不做 |
|---|---|
| 读 `~/.workbuddy/wbipc/endpoint.json`（本机 IPC 通道地址 + 会话 ticket） | ❌ 不读 `workbuddy-desktop.info` 登录态 |
| 连本机命名管道 / unix socket | ❌ 不解密任何 token |
| 调官方接口：签到、连签、旅行 | ❌ 不向客户端之外的任何服务器发数据 |
| 进程内计算、stdout 输出 | ❌ 不写任何文件（日志默认关闭，需要时用 `GAOREN_CHECKIN_LOG` 显式开启） |
| | ❌ 不启动子进程、不执行 shell 命令、不修改系统任何文件 |

## 前置条件

**WorkBuddy 桌面端必须处于运行且已登录状态。** 这是唯一硬性依赖——
本版不读取任何凭据，客户端不开就没有可用的授权通道。

## 用法

```bash
# 0. 第一次先自检
python scripts/wb_auto_checkin.py --doctor

# 1. 只读探测：只查状态，绝不发起任何写请求
python scripts/wb_auto_checkin.py --probe

# 2. 正常执行：签到 + 连签查看 + 旅行先领后派
python scripts/wb_auto_checkin.py

# 3. 结构化输出（给 Agent 用）
python scripts/wb_auto_checkin.py --json
```

| 开关 | 作用 |
|---|---|
| `--doctor` | 环境自检：通道可用性、接口可达、活动有效期、可兑换档位 |
| `--probe` | 只读探测，不发写请求 |
| `--redeem` | 兑换连签档位（**消费型**，默认不做） |
| `--lottery` | 连登抽奖（**有随机性**，默认不做） |
| `--no-streak` / `--no-travel` / `--no-depart` | 跳过对应步骤 |
| `--json` | 结构化输出 |

## 结果契约（给 Agent）

`--json` 输出三段即可判断，不要读源码、不要展开推理：

1. `ok` 是否 `true`
2. `steps[].status` 是否都落在合法状态集内
3. `anomalies` 是否 `[]`

| 步骤 | 合法状态 |
|---|---|
| `checkin` | `success` / `already_checked` / `skipped` |
| `streak` | `ready` / `redeemed` / `locked` / `skipped` |
| `lottery` | `drawn` / `no_chance` / `skipped` |
| `travel` | `departed` / `arrived_claimed` / `claimed` / `traveling` / `idle_limit` / `skipped` |

`traveling`、`idle_limit` 是**正常终态**，不是失败。落在合法集外的状态会被改写为 `error` 并记入 `anomalies`。

## 退出码

`0` 成功（含幂等跳过）｜`1` 接口或网络错误｜`3` 通道不可用（通常是客户端没运行）

## 排错

| 现象 | 处理 |
|---|---|
| 退出码 3 | 打开 WorkBuddy 客户端并确认已登录，再跑 `--doctor` |
| 报 `E_TICKET_INVALID` / `E_PROTOCOL_MISMATCH` | 客户端版本更新导致通道协议变化，见 `references/compat.md` |
| 领取成功但余额看着没变 | 领取成功时接口返回的余额是**领取前**的值，以 `total_credits` 或加油站面板为准 |
| 旅行「空闲」却领不到 | `arrived` 才领；`idle` 且 `daily_limit_reached=true` 表示今天不能再派 |
| 活动结束 | 识别 `active=false` 后跳过领取并正常退出，不报错 |

## 边界

- 不修改 WorkBuddy 本体或系统任何文件
- 不执行 shell 命令、不启动子进程、不装常驻程序
- **不替你消费**：兑换与抽奖需显式加 `--redeem` / `--lottery`
- 积分规则以服务端为准，本 Skill 只做接口调用

MIT License，见 `LICENSE.md`。
