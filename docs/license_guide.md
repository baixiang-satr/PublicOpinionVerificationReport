# 许可证（一机一码）使用与签发指南

本工具采用**离线一机一码授权**：每个授权码绑定一台电脑，含到期时间，
使用 Ed25519 非对称签名（卖方持私钥签发，软件内嵌公钥验签），全程无需联网。

## 用户侧：如何激活

1. 首次启动软件会进入「软件授权激活」页（未激活时所有功能不可用）。
2. 页面显示**本机机器码**（形如 `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`），
   点击「复制机器码」，把它发送给供应商。
3. 供应商返回授权码（`POIR1.` 开头的一串文本）后，粘贴到输入框，点击「激活」。
4. 提示授权成功即进入正常界面；之后每次启动自动校验，无需重复激活。

注意事项：

- 授权码与本机绑定，**复制到其他电脑无法使用**（校验机器码不匹配）。
- 授权到期后软件会回到激活页，请联系供应商续期获取新授权码。
- **重装 Windows 系统或更换主板后机器码会变化**，视同换机，需重新获取授权码。
- 激活信息保存在当前 Windows 用户的
  `%LOCALAPPDATA%\PublicOpinionVerificationReport\license.dat`（DPAPI 加密），
  删除该文件即回到未激活状态。

## 卖方侧：如何签发授权码

卖方工具为 `tools/license_admin.py`（随源码提供，**不进安装包**）。

### 1. 生成密钥对（一次性）

```powershell
python tools/license_admin.py genkey --out-dir keys/
```

- `keys/license_private_key.pem` — **私钥，务必离线妥善保管，泄露=任何人可签发授权**。
- `keys/license_public_key.pem` — 公钥，将其内容写入
  `src/license/crypto.py` 的 `EMBEDDED_PUBLIC_KEY_PEM` 常量后重新打包发布。

仓库内 `keys/dev/` 与 `src/license/crypto.py` 当前内置的是**开发占位密钥对**，
仅供联调；正式对外发布前必须执行上述步骤替换为正式密钥对并重新打包。

### 2. 为客户签发授权码

客户发来机器码后：

```powershell
# 按天数（自签发时刻起 365 天）
python tools/license_admin.py issue -k keys/license_private_key.pem `
    -m XXXX-XXXX-XXXX-XXXX-XXXX-XXXX -n 某某单位 --days 365

# 或指定到期日（含当日，按 UTC 计）
python tools/license_admin.py issue -k keys/license_private_key.pem `
    -m XXXX-XXXX-XXXX-XXXX-XXXX-XXXX -n 某某单位 --expires 2027-01-01
```

输出的 `POIR1.…` 文本即为授权码，发送给客户即可。建议按输出的
`授权编号` 做好台账（哪个单位、哪台机器、何时到期）。

### 3. 查看 / 校验授权码

```powershell
python tools/license_admin.py inspect -c "POIR1...." -p keys/license_public_key.pem
```

可查看被授权方、机器码、签发/到期时间，并用公钥验签确认授权码未损坏。

## 技术说明

- 授权码格式：`POIR1.<base64url(payload_json)>.<base64url(Ed25519签名)>`；
  payload 含授权编号、被授权方、机器码、签发时间、到期时间、产品标识。
- 机器码 = Windows 注册表 `MachineGuid` 与 WMI 系统 UUID 加盐后
  SHA-256 的前 24 位 hex（96 bit，展示为带短横线分组）。
- 校验时机：启动时展示激活状态；抓取、导出、框选截图、平台登录等
  关键操作前由桥接层二次拦截（未激活返回 `LICENSE_REQUIRED`）。
- 模块位置：`src/license/`（models / crypto / fingerprint / manager），
  桥接方法见 `src/webui/bridge.py` 的 `license_status / license_activate /
  license_deactivate`。

## 安全边界（如实说明）

本方案防止的是**授权码被随意复制分享、一码多机**这一商业目标。
离线授权 + 本地分发的软件无法抵御有决心的逆向攻击者（可 patch 程序
跳过校验）；如需提高门槛，可在此基础上叠加字节码加密、关键校验混淆
等缓解手段，本期未实施。
