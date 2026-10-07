# gaoren-workbuddy-checkin

WorkBuddy「Buddy 加油站」每日签到自动化 —— **自研实现，版权归作者本人，MIT 许可**。

- 作者：gaoren
- 许可证：MIT（见 `LICENSE`）
- 依赖：**零第三方依赖**，只用 Python 标准库（含自研的 AES-256-GCM）
- 平台：Windows（主）/ macOS / Linux（需用令牌文件通道）

## 这是什么

一条命令把每天的 100 积分签到搞定，并且**不依赖客户端弹窗模拟**，直接调官方接口。

```bash
python scripts/wb_auto_checkin.py --probe   # 只读探测，先验证环境
python scripts/wb_auto_checkin.py           # 签到（幂等，已签自动跳过）
```

## 与第三方技能的关系

**没有代码关系。** 本项目从零实现了：

- AES-256-GCM（含 GHASH、密钥扩展、CTR）——纯 Python，已用 Node crypto 生成的权威向量交叉验证
- 客户端 AtRestEncryption 信封的解析与 AAD 构造
- Windows 进程内存密钥提取（`ReadProcessMemory` + `keyId` 校验，多线程并行）
- 签到 / 状态 / 猫猫旅行三组接口的调用与幂等处理

接口地址、字段名等**事实性信息**来自客户端自身行为与本机实测（属于互操作所需的事实），实现代码与文档均为原创。

## 安装

把仓库放到技能目录（这样 WorkBuddy 才能识别为技能）：

```
Windows:  C:\Users\<你>\.workbuddy\skills\gaoren-workbuddy-checkin\
macOS:    ~/.workbuddy/skills/gaoren-workbuddy-checkin/
```

或直接下载 Release 里的 zip 解压到同一位置。

## 四条通道（v1.1.0 起默认走 D）

| 通道 | 来源 | 凭据接触 | 条件 | 实测 |
|---|---|---|---|---|
| **D** | 客户端本机 RPC（wbipc 命名管道） | **零** | 客户端运行中 | 全流程 2.4~3.4s |
| A | 本机登录态解密 | 读并解密 accessToken | 客户端运行中 | ~8s |
| C | 环境变量 `GAOREN_ACCESS_TOKEN` | 自备 | — | 同 A |
| B | `~/.workbuddy/gaoren-checkin/token.txt` | 自备 | — | 同 A |

**通道 D 是本项目 v1.1.0 的重点**：WorkBuddy 桌面端运行时会在本机开一个 IPC 服务，
用**它自己的登录态**代发请求，脚本只发相对路径 + JSON body，**完全不接触凭据**，
也因此不受登录态加密格式改版影响。协议由本项目独立逆向并实现（`wbipc_client.py`）。

通道 A 是备用：客户端 5.6.2 起把登录态换成 AES-256-GCM 信封，密钥只在客户端内存里，
本项目自研实现解密（`wb_crypto.py`），全程不写盘、不打印。

## 三步业务（全部幂等）

| 步骤 | 做什么 | 默认 |
|---|---|---|
| `checkin` | 每日 100 积分签到 | 执行 |
| `streak` | 每月连签档位（查看可兑换档位） | 只读查看 |
| `travel` | 猫猫旅行先领后派 | 执行 |
| `lottery` | 连登抽奖 | 需 `--lottery` |

兑换（`--redeem`）与抽奖（`--lottery`）默认**不执行**——它们是消费型/随机性动作，
替你默认做不合适。

## doctor：改版后第一件事

```bash
python scripts/wb_auto_checkin.py --doctor
```

一次检查完：客户端 RPC 通道、登录态信封格式、状态/连签接口可达性、活动有效期、
当前可兑换档位。客户端改版后先跑它，能第一时间发现问题而不是静默失效。
版本兼容记录见 `references/compat.md`。

## 每日自动化

在 WorkBuddy 里建自动化任务，每天 09:00 执行：

```
python <技能目录>/scripts/wb_auto_checkin.py
```

电脑不全天开机时可设多时点补签（9 / 12 / 15 / 18 / 21 点各一次），脚本幂等，重复执行不会重复领取。

## 安全设计

- 只读登录态，不改、不上传 WorkBuddy 任何本体文件
- token 只在内存中使用；日志与输出里没有明文，也没有前后缀
- 密钥靠信封自带的 `keyId`（SHA-256 校验）确认，匹配才解密
- 无 Electron、无常驻进程、无系统级安装
- 想先看它要干什么，跑 `--probe`：只读模式，绝不发起写请求

## 上架 SkillHub（让其他 WorkBuddy 用户能搜到）

平台：<https://skillhub.cn> ｜ 发布入口：<https://skillhub.cn/dashboard/publish>

上架前必须由**作者本人**完成（我无法代办）：

1. 登录 skillhub.cn
2. 完成**实名认证**（腾讯云人脸核身；平台原文：「实名后方可发布 Skill」）——这是硬门槛
3. 在发布页提交技能，可选三种方式：**网页可视化发布 / CLI 一键推送 / Agent 自动化部署**

审核流程（平台原文）：**Skill 强制安全准入**——上架前必须通过三线并行的安全审核流水线：

| 审核线 | 内容 |
|---|---|
| 内容合规过滤 | 关键词过滤 + AI 语义理解 |
| 科恩实验室 | 深度漏洞扫描 |
| 云鼎实验室 | AI 模型安全评估 |

「全部通过才能自动上架，任一不通即刻拒绝。」另有 100% 自动内容审核（违规与低质内容识别）。

### 本仓库为上架准备好的材料

| 项 | 状态 |
|---|---|
| `SKILL.md` frontmatter（name / display_name / 中英描述 / version / author / license / category） | ✅ 齐全 |
| `_icon.png` 512×512 技能图标 | ✅ 附（可用 `scripts/make_icon.py` 重新生成） |
| MIT `LICENSE` | ✅ |
| README 与使用说明 | ✅ |
| 零第三方依赖、纯标准库 | ✅ 利于安全扫描通过 |
| 版本号 v1.0.0 | ✅ |

审核要看的重点是「这个技能会碰什么」——本项目的答案写在 README 与 `SKILL.md` 的「边界（不做的事）」里：只读登录态、不改本体、token 不落盘不打印、无常驻进程、无外发数据到第三方。

## 开发与验证

```bash
python scripts/wb_auto_checkin.py --self-test   # 离线加密自测
node scripts/gen_vectors.js > scripts/test_vectors.py   # 重新生成测试向量
```

测试向量由 Node 内置 crypto 生成，是本项目的标准答案来源——不要手改。

## 免责声明

本项目只做接口调用与本地解密，不修改服务端任何数据。使用者需自行确保用法符合所在平台的服务条款。

## v1.2.0：市场分发版与完整版的区别

| | 市场分发版（SkillHub） | 完整版（本仓库 / GitHub） |
|---|---|---|
| 文件数 | 6 | 12 |
| 通道 | 仅客户端 RPC（零凭据） | 客户端 RPC + 登录态解密 + 令牌文件/环境变量 |
| 读登录态 | ❌ 不读 | ✅ 读并解密（备用兜底） |
| 进程内存操作 | ❌ 无 | ✅ 仅 Windows 用于取运行时密钥 |
| 写文件 | ❌ 不写 | ✅ 可选日志 |
| 前置条件 | 客户端必须运行 | 客户端没开时可用令牌兜底 |

市场版由 `build_market.py` 从本仓库源码**真实裁剪**生成（不是另写一份，也不是代码伪装）：
它删掉 `wb_credentials.py`、`wb_crypto.py`，并移除 doctor 的登录态检查与文件日志能力，
让分发版在静态层面就不具备这些能力。裁剪清单见该脚本开头的 docstring。

若安全扫描对市场版仍不通过，可对照平台内同形态技能 `@user_8783fdd1/workbuddy-auto-checkin`。
申诉材料见 `SkillHub-申诉材料-gaoren-workbuddy-checkin.md`。
