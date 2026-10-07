---
slug: gaoren-workbuddy-checkin
displayName: WorkBuddy 自动签到（自研版·完整版）
name: gaoren-workbuddy-checkin
display_name: WorkBuddy 自动签到（自研版）
display_name_en: WorkBuddy Auto Check-in (Own Build)
summary: WorkBuddy 加油站每日签到 + 每月连签 + 猫猫旅行。自研实现，纯 Python 标准库（自写 AES-256-GCM 与客户端 RPC 协议），零第三方依赖，MIT 许可。四条通道自动择一，默认走客户端本机 RPC 零凭据；带 doctor 自检、只读探测与结果契约。
version: 1.2.0
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
  - Windows
  - 隐私
  - 零依赖
description: WorkBuddy「Buddy 加油站」每日签到自动化 Skill，作者 gaoren 自研，MIT 许可，零第三方依赖。当用户说"每天自动签到 / 自动领 Buddy 加油站积分 / 现在签个到 / 帮我签一下 / 检查签到环境 / 连签 / 每月连签 / 猫猫旅行 / 派猫猫 / Buddy加油站"时使用。四条通道自动择一（客户端本机 RPC 零凭据 / 登录态解密 / 环境变量 / 令牌文件），三步业务（签到、连签、旅行），带 doctor 自检、--probe 只读探测、合法状态集结果契约。
description_zh: WorkBuddy 加油站每日签到与连签、旅行自动化，自研实现，MIT 许可。
description_en: Self-developed WorkBuddy Buddy Station check-in, monthly streak and travel skill. MIT, stdlib only.
agent_created: true
---

# WorkBuddy 自动签到（自研版 v1.1.0）

一条命令办完每日积分：**签到 → 每月连签 → 猫猫旅行**。全部幂等，可重复执行。
纯 Python 标准库实现（含自写的 AES-256-GCM 与客户端 RPC 协议），不装任何第三方包。

> 本 Skill 与任何第三方同名技能**无代码关系**。接口事实来自客户端自身行为与本机实测；实现与文档均为原创，MIT 许可。

## 快速开始

```bash
# 0. 第一次先自检（改版后也先跑这个）
python scripts/wb_auto_checkin.py --doctor

# 1. 只读探测：只查状态，绝不发起任何写请求
python scripts/wb_auto_checkin.py --probe

# 2. 正常执行：签到 + 连签查看 + 旅行
python scripts/wb_auto_checkin.py

# 3. 给 agent 用的结构化输出
python scripts/wb_auto_checkin.py --json

# 4. 离线加密自测（不需要登录态）
python scripts/wb_auto_checkin.py --self-test
```

## 四条通道（自动择一，优先级从高到低）

| 通道 | 来源 | 凭据接触 | 适用场景 | 实测耗时 |
|---|---|---|---|---|
| **D** | 客户端本机 RPC（wbipc 命名管道） | **零** | 客户端在运行（Windows 首选） | 全流程 **2.4~3.4s** |
| **A** | 本机登录态解密 | 读并解密 accessToken | 通道 D 不可用时 | 全流程 ~8s |
| **C** | 环境变量 `GAOREN_ACCESS_TOKEN` | 自备 | 临时 / CI | 同 A |
| **B** | `~/.workbuddy/gaoren-checkin/token.txt` | 自备 | 客户端不开 | 同 A |

**通道 D 是什么**：WorkBuddy 桌面端运行时会在本机开一个 IPC 服务（`~/.workbuddy/wbipc/endpoint.json` 记录命名管道与 ticket），客户端用**自己的登录态**代发 HTTP 请求，鉴权头由它自动附加。本 Skill 只发送相对路径 + JSON body，**完全接触不到凭据**，也因此不受登录态加密格式改版影响。协议细节见 `references/api-notes.md`。

用 `--channel D|A|C|B` 可强制指定通道（排障用）。

## 命令与开关

| 开关 | 作用 |
|---|---|
| `--doctor` | 环境自检：通道、信封格式、接口可达、活动有效期、连签档位 |
| `--probe` | 只读探测，**不发任何写请求** |
| `--redeem` | 兑换可用的连签档位（**消费型**，默认不做） |
| `--lottery` | 执行连登抽奖（**有随机性**，默认不做） |
| `--no-streak` / `--no-travel` | 跳过对应步骤 |
| `--no-depart` | 只领取已到达的旅行奖励，不发起新派遣 |
| `--channel` | 指定通道 |
| `--json` | 结构化输出 |
| `--self-test` | 离线加密自测 |

环境变量：`GAOREN_HOME`、`GAOREN_CHECKIN_LOG`、`GAOREN_LOGIN_STATE`、`GAOREN_ACCESS_TOKEN`、`GAOREN_AUTH_DOMAIN`、`WORKBUDDY_CONFIG_DIR`、`GAOREN_VERBOSE=1`。

## 结果契约（给 Agent 用）

`--json` 输出结构固定，三项判断即可，**不要展开推理、不要读源码**：

1. `ok` 是否为 `true`
2. `steps[].status` 是否都落在合法状态集内
3. `anomalies` 是否为 `[]`

| 步骤 | 合法状态 |
|---|---|
| `checkin` | `success` / `already_checked` / `skipped` |
| `streak` | `ready` / `redeemed` / `locked` / `skipped` |
| `lottery` | `drawn` / `no_chance` / `skipped` |
| `travel` | `departed` / `arrived_claimed` / `claimed` / `traveling` / `idle_limit` / `skipped` |

`traveling` / `idle_limit` 是**正常终态**（猫在途、今日次数已用完），不是失败。
落在合法集之外的状态会被脚本改写为 `error` 并记入 `anomalies`。

## 退出码与日志

`0` 成功（含幂等跳过）｜`1` 接口/网络错误｜`3` 无可用通道
日志默认 `~/.workbuddy/gaoren-checkin/checkin.log`，单行结构化，**不含 token 明文与前后缀**。

## 排错

| 现象 | 处理 |
|---|---|
| 退出码 3 | 先跑 `--doctor`；通道 D 需要客户端在运行，否则用通道 B/C |
| doctor 报「信封 suite≠1」 | 客户端换了加密方案，先更新本 Skill（见 `references/compat.md`） |
| HTTP 401 | accessToken 失效，打开客户端刷新一次 |
| 领取成功但余额看着没变 | 领取成功时接口返回的余额是**领取前**的值，以 `total_credits` 或加油站面板为准 |
| 旅行显示「空闲」却领不到 | 看 `state`：`arrived` 才领；`idle` 时 `daily_limit_reached=true` 表示今天不能再派，不是已领完 |
| 活动结束 | 脚本识别 `active=false` 后跳过领取并正常退出，不会报错 |

## 边界（不做的事）

- 不修改 WorkBuddy 本体文件；通道 A 只**读取**登录态
- 不打印、不落盘任何 token 明文或前后缀；通道 D 更是全程不接触凭据
- 不做系统级常驻、不装 Electron、不注册开机项
- **不替你消费**：档位兑换与抽奖都需要显式加 `--redeem` / `--lottery`
- 不删除任何服务端数据，只做领取类操作

MIT License，见 `LICENSE.md`。
