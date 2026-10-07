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

## 三条凭据通道

| 通道 | 来源 | 条件 |
|---|---|---|
| A | 本机登录态解密（Windows） | 客户端正在运行且已登录 |
| B | `~/.workbuddy/gaoren-checkin/token.txt` | 你自己提供 accessToken |
| C | 环境变量 `GAOREN_ACCESS_TOKEN` | 同上 |

客户端 5.6.2 起把登录态里的 token 换成了 AES-256-GCM 信封，加密密钥只在运行中的客户端内存里。本项目在本地取出并解密，**全程不写盘、不打印**。

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

## 开发与验证

```bash
python scripts/wb_auto_checkin.py --self-test   # 离线加密自测
node scripts/gen_vectors.js > scripts/test_vectors.py   # 重新生成测试向量
```

测试向量由 Node 内置 crypto 生成，是本项目的标准答案来源——不要手改。

## 免责声明

本项目只做接口调用与本地解密，不修改服务端任何数据。使用者需自行确保用法符合所在平台的服务条款。
