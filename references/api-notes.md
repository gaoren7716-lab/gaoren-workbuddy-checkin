# 接口与加密实现笔记（自研过程记录）

作者：gaoren ｜ 本文记录的是**本机实测 + 客户端自身实现**得到的事实，以及自研 AES-GCM 时踩过的坑。
公开接口事实不构成他人代码的复制；实现与代码均为原创。

## 1. 登录态与加密信封（客户端 5.6.2+）

登录态文件位置：

- Windows：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
- macOS：`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/`
- Linux：`~/.config/CodeBuddyExtension/Data/Public/auth/`

5.6.2 起 `auth.accessToken` 不再是明文 JWT，而是两层结构：

```json
{ "auth": { "accessToken": { "$wbEncrypted": 1, "envelope": "<base64>" } } }
```

`envelope` 解 base64 后是 JSON：

```json
{ "suite": 1, "keyId": "<16 位小写 hex>", "nonce": "<base64 12B>",
  "authTag": "<base64 16B>", "ciphertext": "<base64>" }
```

密钥派生：

```
atRestSecretKey = 44 字符 base64 字符串（客户端运行时持有，不落盘）
key    = SHA256(atRestSecretKey 的 UTF-8 字节)[:32]
keyId  = SHA256(key)[:8] 的小写 hex（16 位）
```

**keyId 是自研实现的救命设计**：拿到候选密钥先算 keyId 与信封比对，匹配才尝试解密，等于用 64 位校验先把 99.999% 的错误候选挡掉，也保证了「匹配到的密钥一定是对的」。

AAD 构造（54 字节，与客户端一致）：

```
"WB-AAD\0"
+ 0x01                                  版本
+ uint32be(5) + "WBEV1"                 标准格式 id（field 框架）
+ uint32be(6) + "sym-v1"                套件
+ uint32be(1)                            keyId 计数
+ uint32be(16) + keyId(hex 字符串)      keyId
+ 0x02                                   framing = field
+ 0x00                                   sequence 缺失占位
+ 0x00                                   final 缺失占位
```

（`uint32be(n) + s` 即长度前缀编码，n 为字节长度。）

计数器：GCM 数据块从 `inc32(J0)` 开始，即第一个数据块用 `nonce || 0x00000002`；`J0 = nonce || 0x00000001` 只用于算 tag。

密钥获取顺序：环境变量 `GAOREN_ATREST_KEY` → 登录态目录下可能存在的密钥块 → **运行中客户端的进程内存**（仅 Windows，需同用户）。第三种是主路径，客户端按设计不落盘。

## 2. 自研 AES-256-GCM 踩过的三个坑

这部分是本 Skill 最有价值的地方——三个 bug 都不会报异常，只会安静地算出错误结果。

### 坑 1：AAD 补齐必须在拼接之前

GHASH 的输入是 `A‖pad(A)‖C‖pad(C)‖[len(A)]64‖[len(C)]64`。
如果把 `A‖C‖长度块` 拼在一起再统一补齐，**只要 AAD 长度不是 16 的倍数，后续所有块全错**。

现场表现很迷惑：空 AAD 和 16 字节对齐的 AAD 全部通过，只有真实信封（54 字节 AAD）失败——因为只有不对齐的输入才会暴露这个 bug。

### 坑 2：GF(2^128) 乘法的位序不能反

```python
# 正确：X 从最高位开始（x>>(127-i)）
for i in range(128):
    if (x >> (127 - i)) & 1: z ^= v
    v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
```

写成从最低位开始（`x >> i`）不会报错，只会得到另一个值——小数据量测试可能照样「看起来对」。

### 坑 3：不要相信记忆里的测试向量

本 Skill 的自测向量全部由 `gen_vectors.js`（Node 内置 crypto）现场生成后写入 `test_vectors.py`，来源可复现。
开发过程中曾因记错 NIST 向量的密钥长度（AES-128 vs AES-256）浪费大量时间——**用可信来源生成向量，比背常量可靠得多**。

## 3. 接口字段与坑（实测）

状态接口返回里值得注意的字段：

| 字段 | 含义 | 坑 |
|---|---|---|
| `today_checked_in` | 今日是否已签 | 幂等判定以此为准 |
| `streak_days` | 连续天数 | 判断断没断签只看它 |
| `total_credits` | 真实余额 | **复数**；早期版本用过单数 `total_credit` |
| `week_checkin_days` | 本周已签天数 | 按自然周统计，**周一归零重算**，不是掉签 |
| `is_streak_day` / `streak_bonus_credit` | 连续奖励标记 | 截至 2026-10-07 连续 8 天实测均为 `false` / `0`，网传「第 7 天 1000 分」在本期活动中不成立 |
| `end_time` | 活动截止 | 本期为 `2026-10-15 23:59:59` |

其他实测结论：

1. **领取成功时接口打印的余额是领取前的值**。连看 6 天，每天打印值都精确等于当天领取前的余额。核对积分一律用 `total_credits` 或加油站面板。
2. **「空闲（可派遣）」不代表还能领奖**。同一天两次查询状态文案完全相同，一次有奖励一次为 0，差别在 `daily_limit_reached`。
3. **旅行奖励不进 `total_credits`**。8 天 × 100 与余额精确吻合，而旅行奖励另算。写收益预期时别把它加进签到积分。
4. 已签到时接口返回 HTTP 400 + `code: 10001`（`msg: 今天已签到，请明天再来`），这是**幂等正常态**，不是错误。

## 4. 接口约定

- 基础地址取登录态里的 `auth.domain`（本机为 `www.codebuddy.cn`），**不要硬编码**
- 旅行接口域名是 `www.workbuddy.cn`，路径不带 `/v2`
- 请求头：`Authorization: Bearer <token>` + `Content-Type: application/json`
- 派遣前置条件：`state == "idle"` 且 `daily_limit_reached` 为假，任一不满足就完全不发写请求
