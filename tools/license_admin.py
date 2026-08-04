"""卖方离线授权工具：密钥生成 / 授权码签发 / 授权码查看。

典型流程（卖方操作，不随安装包分发）::

    # 1. 一次性生成密钥对（私钥离线保管，公钥写入 src/license/crypto.py）
    python tools/license_admin.py genkey --out-dir keys/

    # 2. 客户发来机器码后签发授权码
    python tools/license_admin.py issue --private keys/license_private_key.pem \
        --machine XXXX-XXXX-XXXX-XXXX-XXXX-XXXX --name 某某单位 --days 365

    # 3. 查看授权码内容（可选验签）
    python tools/license_admin.py inspect --code "POIR1...." --public keys/license_public_key.pem
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import typer

from src.license.crypto import (
    LicenseBadSignature,
    LicenseCodeMalformed,
    encode_code,
    generate_keypair,
    inspect_code,
    verify_code,
)
from src.license.fingerprint import is_valid_short_code, normalize_short_code
from src.license.models import LicensePayload

app = typer.Typer(help="舆情验证报告工具 — 离线授权码签发工具（卖方专用）")

_PRIVATE_NAME = "license_private_key.pem"
_PUBLIC_NAME = "license_public_key.pem"


@app.command()
def genkey(
    out_dir: Path = typer.Option(Path("keys"), "--out-dir", "-o", help="密钥输出目录"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的密钥文件"),
) -> None:
    """生成 Ed25519 密钥对。私钥务必离线保管，绝不随安装包分发。"""

    private_path = out_dir / _PRIVATE_NAME
    public_path = out_dir / _PUBLIC_NAME
    if not force and (private_path.exists() or public_path.exists()):
        raise typer.BadParameter(f"{out_dir} 已存在密钥文件，使用 --force 覆盖。")
    private_pem, public_pem = generate_keypair()
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_text(private_pem, encoding="ascii")
    public_path.write_text(public_pem, encoding="ascii")
    typer.echo(f"私钥（离线保管）: {private_path}")
    typer.echo(f"公钥            : {public_path}")
    typer.echo("请将公钥内容写入 src/license/crypto.py 的 EMBEDDED_PUBLIC_KEY_PEM。")


@app.command()
def issue(
    private: Path = typer.Option(..., "--private", "-k", help="卖方私钥 PEM 路径"),
    machine: str = typer.Option(..., "--machine", "-m", help="客户机器码（24 位 hex，可带短横线）"),
    name: str = typer.Option(..., "--name", "-n", help="被授权方名称"),
    days: int | None = typer.Option(None, "--days", "-d", help="有效天数（与 --expires 二选一）"),
    expires: str | None = typer.Option(None, "--expires", "-e", help="到期日 YYYY-MM-DD（含当日）"),
    license_id: str | None = typer.Option(None, "--license-id", help="授权编号（默认自动生成）"),
    product: str = typer.Option("poir", "--product", help="产品标识"),
) -> None:
    """为指定机器码签发授权码。"""

    if not private.is_file():
        raise typer.BadParameter(f"私钥文件不存在：{private}")
    if not is_valid_short_code(machine):
        raise typer.BadParameter("机器码必须是 24 位十六进制字符（可带短横线/空格）。")
    expires_at = _resolve_expiry(days, expires)
    now = datetime.now(tz=timezone.utc)
    payload = LicensePayload(
        license_id=license_id or f"POIR-{now:%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
        licensee=name,
        machine_id=normalize_short_code(machine),
        issued_at=now,
        expires_at=expires_at,
        product=product,
    )
    code = encode_code(payload, private.read_text(encoding="ascii"))
    typer.echo("授权码（发送给客户）:")
    typer.echo(code)
    typer.echo(f"授权编号: {payload.license_id}  到期: {expires_at.date().isoformat()}")


@app.command()
def inspect(
    code: str = typer.Option(..., "--code", "-c", help="授权码"),
    public: Path | None = typer.Option(None, "--public", "-p", help="可选：公钥 PEM，用于验签"),
) -> None:
    """解码授权码查看内容；提供公钥时同时验签。"""

    try:
        if public is not None:
            payload = verify_code(code, public.read_text(encoding="ascii"))
            typer.echo("验签: 通过")
        else:
            payload = inspect_code(code)
            typer.echo("验签: 未执行（未提供公钥）")
    except LicenseCodeMalformed as error:
        raise typer.BadParameter(f"授权码格式无效：{error}") from error
    except LicenseBadSignature as error:
        raise typer.BadParameter(f"验签失败：{error}") from error
    typer.echo(f"授权编号: {payload.license_id}")
    typer.echo(f"被授权方: {payload.licensee}")
    typer.echo(f"机器码  : {payload.machine_id}")
    typer.echo(f"签发时间: {payload.issued_at.isoformat()}")
    typer.echo(f"到期时间: {payload.expires_at.isoformat()}")
    typer.echo(f"产品    : {payload.product}")
    typer.echo(f"状态    : {'已过期' if payload.is_expired() else '有效'}")


def _resolve_expiry(days: int | None, expires: str | None) -> datetime:
    if (days is None) == (expires is None):
        raise typer.BadParameter("--days 与 --expires 必须且只能提供一个。")
    if days is not None:
        if days <= 0:
            raise typer.BadParameter("--days 必须为正整数。")
        return datetime.now(tz=timezone.utc) + timedelta(days=days)
    try:
        day = datetime.strptime(expires or "", "%Y-%m-%d")
    except ValueError as error:
        raise typer.BadParameter("--expires 格式应为 YYYY-MM-DD。") from error
    # 到期日含当日：有效期至次日 0 点（UTC）
    return day.replace(tzinfo=timezone.utc) + timedelta(days=1)


if __name__ == "__main__":
    app()
