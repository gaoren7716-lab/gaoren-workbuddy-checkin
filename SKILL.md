---
name: gaoren-workbuddy-checkin
display_name: WorkBuddy 自动签到（自研版）
description: WorkBuddy「Buddy 加油站」每日签到自动化 Skill，作者 gaoren 自研，MIT 许可，零第三方依赖（纯 Python 标准库，自带 AES-256-GCM 实现）。当用户说"每天自动签到 / 自动领 Buddy 加油站积分 / 现在签个到 / 帮我签一下 / 检查签到环境 / 猫猫旅行 / 派猫猫 / 签到通知 / Buddy 加油站"时使用。支持三条凭据通道（本机登录态解密 / 令牌文件 / 环境变量），带只读探测模式（--probe 绝不发起写请求）、幂等领取、猫猫旅行先领后派、脱敏日志。Windows 需客户端处于运行登录状态。
description_zh: WorkBuddy「Buddy 加油站」每日签到自动化 Skill，自研实现，MIT 许可。
description_en: Self-developed WorkBuddy Buddy Station daily check-in skill. MIT licensed, stdlib only.
version: 1.0.0
author: gaoren
license: MIT
agent_created: true
---

# WorkBuddy 自动签到（自研版）

把「Buddy 加油站」每天的 100 积分签到变成一条命令。本 Skill 的加密、密钥获取、接口调用全部为自研实现，只用 Python 标准库，不装任何第三方包。

> 本 Skill 与任何第三方同名技能**无代码关系**。接口事实来自客户端自身行为与本机实测；实现与文档均为原创，MIT 许可。

## 快速开始

```bash
# 1. 只读探测（不发起任何写请求，第一次建议先跑这个）
python scripts/wb_auto_checkin.py --probe

# 2. 正常签到（幂等：今天已签会自动跳过）
python scripts/wb_auto_checkin.py

# 3. 给 agent 用的结构化输出
python scripts/wb_auto_checkin.py --json

# 4. 离线自测（验��� AES-GCM 实现，不需要登录态）
python scripts/wb_auto_checkin.py --self-test
```

Python 3.8+ 即可，不需要 `pip install` 任何东西。

## 凭据通道（自动择一）

| 通道 | 来源 | 适用场景 | 前置条件 |
|---|---|---|---|
| **A** | 本机登录态解密 | Windows（本机实测） | 客户端正在运行且已登录 |
| **B** | `~/.workbuddy/gaoren-checkin/token.txt` | 客户端不开时 | 你自己放一份 accessToken |
| **C** | 环境变量 `GAOREN_ACCESS_TOKEN` | 临时/CI | 同上 |

**通道 A 的工作方式**：客户端 5.6.2 起把登录态里的 accessToken 换成 AES-256-GCM 信封，加密密钥只存在于运行中的客户端内存里、不落盘。本 Skill 从客户端进程内存中取出该密钥（用信封自带的 `keyId` 做 SHA-256 校验，拿到的一定是对的密钥），在本地解密，全程不写盘、不打印。

- 密钥来源优先级：环境变量 `GAOREN_ATREST_KEY` → 磁盘密钥块（个别版本）→ 进程内存
- 进程内存扫描按工作集从大到小、多线程并行，实测 8 秒内命中
- 只在 Windows 有效，且脚本与客户端必须同一用户运行

## 命令与开关

| 开关 | 作用 |
|---|---|
| `--probe` | 只读：只查状态，**不发任何写请求**，用于首次验证环境 |
| `--no-travel` | 只签到，不碰猫猫旅行 |
| `--no-depart` | 只领取已到达的旅行奖励，不发起新的派遣 |
| `--json` | 输出 JSON，方便 agent 解析 |
| `--self-test` | 离线跑加密自测（对照 Node crypto 生成的权威向量） |

环境变量：`GAOREN_LOGIN_STATE`（登录态路径）、`GAOREN_CHECKIN_LOG`（日志路径）、`GAOREN_CLIENT_PROCS`（自定义进程名，逗号分隔）、`GAOREN_VERBOSE=1`（打印扫描进度）。

## 输出与退出码

- 退出码 `0` 成功（含「今日已签到，跳过」这种正常幂等）
- 退出码 `1` 接口或网络错误
- 退出码 `3` 拿不到凭据（客户端没开 / 没有 token.txt / 环境变量没设）
- 日志默认 `~/.workbuddy/gaoren-checkin/checkin.log`，单行结构化，**不含 token 明文与前后缀**

```
2026-10-07 15:12:44 | status=ok | action=skip_already_signed | credit=0 | streak=8 | balance=800 | src=登录态（解密，密钥来自客户端进程内存）
```

## 接口（自己抓的，不是抄文档）

以登录态里的 `auth.domain` 为准，**不要硬编码**：

| 用途 | 方法 | 路径 |
|---|---|---|
| 查状态（只读） | POST | `https://<domain>/v2/billing/meter/checkin-activity-status` |
| 领取签到 | POST | `https://<domain>/v2/billing/meter/daily-checkin` |
| 旅行状态 | GET | `https://www.workbuddy.cn/activity/growth/buddy/travel/status` |
| 领取旅行奖励 | POST | `https://www.workbuddy.cn/activity/growth/buddy/travel/claim` |
| 派遣旅行 | POST | `https://www.workbuddy.cn/activity/growth/buddy/travel/depart`（body `{"location_id":1}`） |

> 旅行接口的域名是 `www.workbuddy.cn`、路径**不带 `/v2`**，与签到接口不是同一个服务，写错必 404。

字段与错误码、加密信封格式、AAD 构造等细节见 `references/api-notes.md`（里面也记了几个实测踩过的坑）。

## 排错

| 现象 | 原因与处理 |
|---|---|
| 退出码 3，提示取不到密钥 | 客户端没运行或不是同一用户 → 先打开 WorkBuddy 登录；或用通道 B/C |
| 提示 `authTag 校验失败` | 换密钥了（客户端重启/换账号）→ 重跑即可；持续失败请跑 `--self-test` |
| 领取成功但余额看着没变 | 领取成功时接口打印的余额是**领取前**的值，一律以 `total_credits` 或加油站面板为准 |
| `week_checkin_days` 变小 | 按自然周统计，周一归零重算，不是掉签；看 `streak_days` |
| 旅行显示「空闲」但领不到 | 「空闲」只描述 Buddy 在不在路上，不描述当日次数配额，看 `daily_limit_reached` |
| 状态接口 401 | accessToken 过期，客户端重新登录一次即可刷新 |

## 边界（不做的事）

- 不碰、不改、不上传 WorkBuddy 本体文件；只读取登录态
- 不打印、不落盘任何 token 明文或前后缀
- 不做系统级常驻、不装 Electron、不注册开机项
- 不代替用户做任何账号相关的其他操作
- 活动规则与积分发放以服务端为准，本 Skill 只是调用官方接口

MIT License，见 `LICENSE`。
