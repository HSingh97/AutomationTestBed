"""Fresh device backup via GUI (temp storage) with session cache for restore."""

from __future__ import annotations

import shlex
import tempfile
import time
from pathlib import Path
from typing import Any

from utils.net_utils import normalize_ip
from utils.regression_flows import _navigate_to_flashops

_SESSION_BACKUPS: dict[str, Path] = {}


def _cache_key(role: str, host: str = "") -> str:
    role_u = str(role).upper()
    host_n = normalize_ip(str(host).split("/")[0]) if host else ""
    return f"{role_u}:{host_n or 'default'}"


def get_session_backup_path(role: str = "BTS", host: str = "") -> Path | None:
    """Return cached fresh backup for this role/host, if still on disk."""
    path = _SESSION_BACKUPS.get(_cache_key(role, host))
    if path is None:
        # Any host-specific key for role (e.g. BTS:10.0.0.1 vs BTS:default)
        role_u = str(role).upper()
        for key, candidate in _SESSION_BACKUPS.items():
            if key.startswith(f"{role_u}:") and candidate.is_file():
                path = candidate
                break
    if path is None or not path.is_file():
        return None
    return path


def register_session_backup(role: str, host: str, path: Path) -> None:
    _SESSION_BACKUPS[_cache_key(role, host)] = path.resolve()


def temp_backup_path(role: str, *, prefix: str = "ubr") -> Path:
    stamp = int(time.time())
    return Path(tempfile.gettempdir()) / f"{prefix}_{str(role).lower()}_{stamp}.tar.gz"


def resolve_restore_archive_path(
    profile: dict[str, Any],
    *,
    role: str = "bts",
    cfg: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """
    Prefer session fresh backup over repo archives (config/BTS.tar.gz).
    Returns None when stale archives are disallowed and no fresh backup exists.
    """
    cfg = cfg or profile.get("ip_tests", {}) or {}
    cached = get_session_backup_path(role)
    if cached is not None:
        return cached

    require_fresh = bool(cfg.get("ip15_require_fresh_backup", True))
    allow_stale = bool(cfg.get("allow_stale_backup_archive", False))
    if require_fresh and not allow_stale:
        return None

    explicit = str(cfg.get("backup_archive_path", "")).strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute() and repo_root:
            path = repo_root / path
        return path if path.is_file() else None

    recovery = profile.get("recovery", {}) or {}
    link_rec = profile.get("testbed", {}).get("link_recovery", {}) or {}
    role_l = str(role).lower()
    if role_l == "cpe":
        rel = recovery.get("cpe_restore_archive") or link_rec.get("cpe_archive")
    else:
        rel = recovery.get("bts_restore_archive") or link_rec.get("bts_archive")
    if not rel or not repo_root:
        return None
    path = repo_root / str(rel)
    return path if path.is_file() else None


async def _navigate_flashops_backup_tab(gui_page) -> None:
    await _navigate_to_flashops(gui_page)
    for selector in (
        "a:has-text('Backup')",
        "li:has-text('Backup') a",
        'xpath=//*[@id="maincontent"]/div/div/ul/li[3]/a',
        "a[href*='/flashops/backup']",
    ):
        tab = gui_page.locator(selector).first
        if await tab.is_visible(timeout=1500):
            await tab.click()
            await gui_page.wait_for_load_state("networkidle")
            await gui_page.wait_for_timeout(800)
            return
    base = (gui_page.url or "").rstrip("/")
    if "/backup" not in base:
        await gui_page.goto(f"{base}/backup", timeout=30000)
        await gui_page.wait_for_load_state("networkidle")
        await gui_page.wait_for_timeout(800)


def gui_host_from_page(gui_page, fallback: str = "") -> str:
    """Use the host from the live LuCI session URL (not SSH LAN IP)."""
    try:
        from urllib.parse import urlparse

        host = urlparse(gui_page.url or "").hostname or ""
        if host:
            return normalize_ip(host)
    except Exception:
        pass
    return normalize_ip(str(fallback).split("/")[0]) if fallback else ""


def _looks_like_sysupgrade_backup(data: bytes) -> bool:
    if len(data) < 256:
        return False
    if data[:2] == b"\x1f\x8b":
        return True
    return b"ustar" in data[:2048] or b"etc/config" in data[:4096]


async def _download_backup_via_http(gui_page, dest: Path) -> bool:
    """LuCI backup endpoint with authenticated browser session."""
    base = (gui_page.url or "").split("/cgi-bin/luci", 1)[0]
    paths = (
        "/cgi-bin/luci/admin/system/flashops/backup",
        "/cgi-bin/luci/admin/system/flashops/download",
        "/cgi-bin/luci/admin/system/flashops/backup/",
        "/cgi-bin/luci/admin/system/flashops/backup/download",
    )
    for path in paths:
        url = f"{base}{path}"
        try:
            resp = await gui_page.request.get(url, timeout=120000)
            if resp.status != 200:
                continue
            body = await resp.body()
            if not _looks_like_sysupgrade_backup(body):
                continue
            dest.write_bytes(body)
            return dest.is_file() and dest.stat().st_size >= 256
        except Exception:
            continue
    return False


async def _download_backup_via_form(gui_page, dest: Path) -> bool:
    """Submit LuCI flashops backup form (Senao: Generate/Download on Backup tab)."""
    await _navigate_flashops_backup_tab(gui_page)
    form = gui_page.locator("form[action*='backup'], form[action*='flashops']").first
    if await form.count() == 0:
        return False
    try:
        async with gui_page.expect_download(timeout=120000) as dl_info:
            submit = form.locator(
                "input[type='submit'], input[value*='Backup' i], input[value*='Generate' i], button"
            ).first
            if await submit.is_visible(timeout=2000):
                await submit.click()
            else:
                await form.evaluate("f => f.submit()")
        download = await dl_info.value
        await download.save_as(str(dest))
        if dest.is_file():
            data = dest.read_bytes()
            if _looks_like_sysupgrade_backup(data):
                return True
            dest.unlink(missing_ok=True)
    except Exception:
        pass
    return False


async def _click_backup_download(gui_page, dest: Path) -> bool:
    """Click LuCI backup control or HTTP-download backup archive."""
    if await _download_backup_via_http(gui_page, dest):
        return True
    if await _download_backup_via_form(gui_page, dest):
        return True
    await _navigate_flashops_backup_tab(gui_page)
    backup_selectors = (
        "input[value*='Generate' i]",
        "input[value*='Backup' i]",
        "button:has-text('Backup')",
        "button:has-text('Generate')",
        "a:has-text('Download backup')",
        "a:has-text('Download')",
        "input[type='submit']",
    )
    for selector in backup_selectors:
        btn = gui_page.locator(selector).first
        if not await btn.is_visible(timeout=1500):
            continue
        try:
            async with gui_page.expect_download(timeout=120000) as dl_info:
                await btn.click()
            download = await dl_info.value
            await download.save_as(str(dest))
            if dest.is_file() and _looks_like_sysupgrade_backup(dest.read_bytes()):
                return True
            dest.unlink(missing_ok=True)
        except Exception:
            continue

    base = (gui_page.url or "").rsplit("/cgi-bin/luci/", 1)[0]
    for suffix in (
        "/cgi-bin/luci/admin/system/flashops/backup",
        "/cgi-bin/luci/admin/system/flashops/download",
    ):
        try:
            async with gui_page.expect_download(timeout=90000) as dl_info:
                await gui_page.goto(f"{base}{suffix}", timeout=90000)
            download = await dl_info.value
            await download.save_as(str(dest))
            if dest.is_file() and _looks_like_sysupgrade_backup(dest.read_bytes()):
                return True
            dest.unlink(missing_ok=True)
        except Exception:
            continue
    return False


async def take_gui_device_backup(
    gui_page,
    *,
    device_creds: dict[str, str],
    gui_ip: str,
    role: str = "BTS",
    min_bytes: int = 1024,
    notes: list[str] | None = None,
    force: bool = False,
) -> Path:
    """
    Management → Upgrade/Reset → Backup → download archive to temp dir.
    Cached per session so restore reuses the same file (not config/BTS.tar.gz).
    """
    target = normalize_ip(str(gui_ip).split("/")[0])
    if gui_page is not None:
        page_host = gui_host_from_page(gui_page, target)
        if page_host:
            target = page_host
    if not force:
        cached = get_session_backup_path(role, target)
        if cached is not None and cached.stat().st_size >= min_bytes:
            if notes is not None:
                notes.append(f"backup: reusing session temp {cached}")
            return cached

    from utils.gui_login import login_if_needed

    await login_if_needed(
        gui_page,
        target,
        device_creds,
        wait_ms=3000,
        skip_recovery=True,
    )
    await _navigate_flashops_backup_tab(gui_page)

    dest = temp_backup_path(role)
    if dest.exists():
        dest.unlink()

    if not await _click_backup_download(gui_page, dest):
        raise RuntimeError(f"GUI backup download failed on {target} (no backup button/download)")

    size = dest.stat().st_size
    if size < min_bytes:
        raise RuntimeError(f"GUI backup too small ({size} bytes): {dest.name}")

    from utils.ip_link_recovery import inspect_backup_for_link_issues

    for issue in inspect_backup_for_link_issues(dest):
        if notes is not None:
            notes.append(f"backup WARN: {issue}")

    register_session_backup(role, target, dest)
    if notes is not None:
        notes.append(f"backup: GUI → temp {dest} ({size} bytes)")
    return dest


async def _ssh_sysupgrade_backup(
    ssh,
    *,
    password: str,
    host: str,
    remote_path: str,
    local_path: Path,
) -> None:
    from utils.ip_test_flows import _run_local_cmd, _scp_from_ssh_host, _ssh_run

    create_cmd = f"sysupgrade -b {shlex.quote(remote_path)} 2>/dev/null && test -s {shlex.quote(remote_path)}"
    await _ssh_run(ssh, create_cmd, timeout=120)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    await _scp_from_ssh_host(host, password, remote_path, local_path)


async def take_device_backup(
    *,
    gui_page=None,
    ssh=None,
    device_creds: dict[str, str] | None = None,
    gui_ip: str = "",
    password: str = "",
    role: str = "BTS",
    cfg: dict[str, Any] | None = None,
    live_uci: dict[str, str] | None = None,
    notes: list[str] | None = None,
    force: bool = False,
) -> Path:
    """
    Take a fresh backup before factory reset / restore cycle.
    Prefers GUI download to temp; falls back to SSH sysupgrade -b.
    Never uses pre-baked config/BTS.tar.gz unless allow_stale_backup_archive is true.
    """
    cfg = cfg or {}
    min_bytes = int(cfg.get("backup_min_bytes", 1024))
    prefer_gui = bool(cfg.get("backup_via_gui", True))
    require_fresh = bool(cfg.get("ip15_require_fresh_backup", True))
    allow_stale = bool(cfg.get("allow_stale_backup_archive", False))
    target = normalize_ip(str(gui_ip or getattr(ssh, "host", "")).split("/")[0])

    if not force:
        cached = get_session_backup_path(role, target)
        if cached is not None and cached.stat().st_size >= min_bytes:
            if notes is not None:
                notes.append(f"backup: session temp {cached.name}")
            return cached

    if allow_stale and not require_fresh:
        stale = resolve_restore_archive_path(
            cfg.get("_profile") or {},
            role=role,
            cfg=cfg,
            repo_root=Path(__file__).resolve().parent.parent,
        )
        if stale is not None and stale.is_file():
            if notes is not None:
                notes.append(f"backup: using stale archive {stale.name}")
            return stale

    last_err = ""
    gui_target = gui_host_from_page(gui_page, target) if gui_page is not None else target
    if prefer_gui and gui_page is not None and device_creds and gui_target:
        try:
            path = await take_gui_device_backup(
                gui_page,
                device_creds=device_creds,
                gui_ip=gui_target,
                role=role,
                min_bytes=min_bytes,
                notes=notes,
                force=True,
            )
            if live_uci:
                from utils.ip_test_flows import _assert_backup_matches_live_uci

                _assert_backup_matches_live_uci(path, live_uci, notes=notes)
            return path
        except Exception as exc:
            last_err = str(exc)
            if notes is not None:
                notes.append(f"backup: GUI failed ({exc}); trying SSH sysupgrade -b")

    if ssh is None:
        raise RuntimeError(last_err or "backup: no GUI page and no SSH session")

    remote = str(cfg.get("backup_remote_path", "/tmp/ip_test_backup.tar.gz")).strip()
    local = temp_backup_path(role, prefix="ubr_ssh")
    host = target or normalize_ip(str(getattr(ssh, "host", "")))
    await _ssh_sysupgrade_backup(
        ssh,
        password=password,
        host=host,
        remote_path=remote,
        local_path=local,
    )
    if local.stat().st_size < min_bytes:
        raise RuntimeError(f"SSH backup too small ({local.stat().st_size} bytes)")
    if live_uci:
        from utils.ip_test_flows import _assert_backup_matches_live_uci

        _assert_backup_matches_live_uci(local, live_uci, notes=notes)
    register_session_backup(role, host, local)
    if notes is not None:
        notes.append(f"backup: SSH sysupgrade -b → temp {local} ({local.stat().st_size} bytes)")
    return local


async def ensure_session_device_backup(
    gui_page,
    *,
    profile: dict[str, Any],
    device_creds: dict[str, str],
    gui_ip: str,
    ssh=None,
    password: str = "",
) -> Path | None:
    """
    Session hook: capture GUI backup once at login when ip_tests.take_session_backup is true.
    """
    ip_cfg = profile.get("ip_tests", {}) or {}
    if not ip_cfg.get("take_session_backup", False):
        return get_session_backup_path("BTS", gui_ip)
    if get_session_backup_path("BTS", gui_ip):
        return get_session_backup_path("BTS", gui_ip)
    notes: list[str] = []
    try:
        path = await take_device_backup(
            gui_page=gui_page,
            ssh=ssh,
            device_creds=device_creds,
            gui_ip=gui_ip,
            password=password,
            role="BTS",
            cfg={**ip_cfg, "_profile": profile},
            notes=notes,
        )
        for line in notes:
            print(f"[backup] {line}")
        return path
    except Exception as exc:
        print(f"[backup] session backup skipped: {exc}")
        return None
