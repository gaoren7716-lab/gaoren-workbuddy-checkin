# 版本兼容记录

作者：gaoren ｜ 这份文件的意义：**客户端一改版，先来这里查**。

## 怎么用这份表

出现下面任一情况，先跑 `python scripts/wb_auto_checkin.py --doctor`，再对照本表：

| doctor 输出 | 含义 | 处理 |
|---|---|---|
| 通道 D 可用 | 客户端 RPC 正常，优先用它 | 无需处理 |
| 通道 D 不可用但 A 可用 | RPC 不可用，退回登录态解密 | 检查客户端是否登录 |
| 信封 `suite=1` | 登录态仍是 AES-256-GCM | 正常 |
| 信封 suite 非 1 | **客户端换了加密方案** | 按下方「格式变更」处理 |
| 状态接口不可达 | 接口路径或域名变了 | 按下方「接口变更」处理 |

## 已验证的组合

| 客户端版本 | 通道 A（登录态） | 通道 D（RPC） | 备注 |
|---|---|---|---|
| 5.7.6 | ✅ AES-256-GCM 信文理解密成功 | ✅ 握手 + http.fetch 正常 | 2026-10-07 实测 |

## 历史变更与教训

### 5.6.2 —— 登录态改为加密（影响通道 A）

客户端对 `auth.accessToken` 启用 AtRestEncryption，`auth.accessToken` 从明文 JWT 变成
`{"$wbEncrypted":1,"envelope":"<base64>"}` 的 AES-256-GCM 信封，加密密钥只在运行中的
客户端内存里、不落盘。

**影响**：任何"读明文 JSON 拿 token"的旧方案直接失效。
**本 Skill 的应对**：通道 A 自研实现信封解密（`wb_crypto.py`），并用信封自带的 `keyId`
做 SHA-256 校验确认密钥正确；更推荐直接用通道 D，完全不碰凭据。

### 5.4.5 —— GUI 无障碍树失效（别人的坑，记在这里提醒）

Electron 37 / Chromium 138 的 renderer 无障碍树不渲染，导致 UI 自动化方案全线失效，
直到 5.4.7 才修复；同时入口文案从「签到领积分」改成「Buddy加油站」。

**本 Skill 不受影响**（走接口不依赖界面文案），这也是选接口直签而非 GUI 自动化的原因之一。

### 接口变更的通用信号

以下任一出现，说明接口或协议变了，请提 issue 或自行修复后升版本：

- doctor 里「状态接口可达」变成不可达
- 某一步的 status 变成 `error` 且 code 非 0/10001
- 通道 D 握手返回 `E_PROTOCOL_MISMATCH`（协议版本变化，本 Skill 固定 protocol=1）
- 通道 D 返回 `E_TICKET_INVALID`（ticket 机制变了，重新读 endpoint.json 确认字段）

## 格式变更时怎么改

- **信封变了**：改 `wb_crypto.py` 的 `build_field_aad()` / `open_envelope()`，
  先用 `--self-test` 与 Node crypto 交叉验证再改主流程
- **RPC 协议变了**：改 `wbipc_client.py`；协议要点都写在文件头注释里（含帧格式、
  握手字段、HMAC 构造），不用重新逆向
- **接口路径变了**：改 `wb_auto_checkin.py` 里的常量与 `api()` 调用
