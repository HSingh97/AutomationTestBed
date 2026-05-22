"""DDRS / ATPC page UI rules: dropdown contents and visibility vs DDRS + spatial stream."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from config.defaults import DEFAULT_VALUES
from pages.locators import RadioPropertiesLocators, UITimeouts
from utils.ui_helpers import execute_form_save, execute_triple_apply


def _log(role: str, message: str):
    print(f"[RADIO][{role}] {message}")


async def _dropdown_options(gui_page, locator: str) -> list[dict[str, str]]:
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    return await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )


def _resolve_dropdown_option(options: list[dict[str, str]], token: str) -> dict[str, str] | None:
    token = str(token)
    for option in options:
        if option["text"] == token or option["value"] == token:
            return option
    return None


async def _set_dropdown_value(gui_page, locator: str, option_value: str):
    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=UITimeouts.ELEMENT_WAIT_MS)
    if await element.is_visible():
        await element.select_option(value=option_value)
    else:
        await element.evaluate(
            """
            (el, value) => {
                el.value = value;
                Array.from(el.options).forEach(opt => {
                    opt.selected = opt.value === value;
                });
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
            """,
            option_value,
        )
    await gui_page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)

MCS_SINGLE = frozenset(range(0, 12))
MCS_DUAL = frozenset(range(12, 24))
DDRS_STATUS_OPTIONS = frozenset({"Enable", "Disable"})
SPATIAL_WITH_AUTO = frozenset({"Single", "Dual", "Auto"})
SPATIAL_WITHOUT_AUTO = frozenset({"Single", "Dual"})

_MCS_RE = re.compile(r"MCS\s*(\d+)", re.IGNORECASE)


def _norm_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def find_mcs_option_text(options: list[dict[str, str]], mcs_num: int) -> str | None:
    """Return dropdown label for a given MCS index (e.g. 12), regardless of Mbps suffix."""
    for opt in options:
        match = _MCS_RE.search(opt["text"])
        if match and int(match.group(1)) == mcs_num:
            return opt["text"]
    return None


def _mcs_numbers_from_option_texts(texts: list[str]) -> set[int]:
    found: set[int] = set()
    for text in texts:
        match = _MCS_RE.search(text)
        if match:
            found.add(int(match.group(1)))
    return found


def _classify_field_label(label: str) -> str | None:
    """Map a cbi-value title to a logical DDRS field key."""
    norm = _norm_label(label)
    if "data rate" in norm and "single" in norm:
        return "max_data_single"
    if "data rate" in norm and "dual" in norm:
        return "max_data_dual"
    if "min" in norm and "modulation" in norm:
        return "min_mod"
    if "max" in norm and "modulation" in norm:
        return "max_mod"
    if norm == "modulation index" or (
        "modulation index" in norm and "min" not in norm and "max" not in norm
    ):
        return "modulation_index"
    return None


async def _scan_ddrs_fields(page) -> list[dict[str, Any]]:
    """Return visible DDRS selects with label, selector, option texts, and field key."""
    rows = await page.evaluate(
        """
        () => {
            const out = [];
            for (const row of document.querySelectorAll('.cbi-value')) {
                const title = row.querySelector('.cbi-value-title');
                const select = row.querySelector('select');
                if (!title || !select) continue;
                const label = (title.textContent || '').trim();
                const rowStyle = window.getComputedStyle(row);
                const rowVisible =
                    rowStyle.display !== 'none' &&
                    rowStyle.visibility !== 'hidden' &&
                    row.offsetParent !== null;
                const options = Array.from(select.options)
                    .filter(opt => !opt.disabled && (opt.textContent || '').trim())
                    .map(opt => (opt.textContent || '').trim());
                const selector = select.id
                    ? `#${select.id}`
                    : `select[name="${select.name}"]`;
                out.push({ label, selector, rowVisible, options });
            }
            return out;
        }
        """
    )
    fields: list[dict[str, Any]] = []
    for row in rows:
        key = _classify_field_label(row["label"])
        if not key:
            continue
        fields.append(
            {
                "key": key,
                "label": row["label"],
                "selector": row["selector"],
                "visible": bool(row["rowVisible"]),
                "options": row["options"],
            }
        )
    return fields


def _field_map(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for entry in fields:
        by_key[entry["key"]] = entry
    return by_key


def _assert_mcs_range(field_key: str, label: str, options: list[str], expected: frozenset[int]):
    actual = _mcs_numbers_from_option_texts(options)
    assert actual == set(expected), (
        f"{field_key} ({label!r}): expected MCS{min(expected)}–MCS{max(expected)}, "
        f"got MCS indices {sorted(actual)} from {len(options)} option(s)."
    )


@dataclass(frozen=True)
class _DdrsUiScenario:
    ddrs: str
    spatial: str
    spatial_options: frozenset[str]
    visible: dict[str, frozenset[int]] = field(default_factory=dict)
    hidden_keys: frozenset[str] = field(default_factory=frozenset)


_SCENARIOS: tuple[_DdrsUiScenario, ...] = (
    _DdrsUiScenario(
        ddrs="Enable",
        spatial="Auto",
        spatial_options=SPATIAL_WITH_AUTO,
        visible={
            "max_data_single": MCS_SINGLE,
            "max_data_dual": MCS_DUAL,
        },
        hidden_keys=frozenset({"min_mod", "max_mod", "modulation_index"}),
    ),
    _DdrsUiScenario(
        ddrs="Enable",
        spatial="Single",
        spatial_options=SPATIAL_WITH_AUTO,
        visible={"min_mod": MCS_SINGLE, "max_mod": MCS_SINGLE},
        hidden_keys=frozenset({"max_data_single", "max_data_dual", "modulation_index"}),
    ),
    _DdrsUiScenario(
        ddrs="Enable",
        spatial="Dual",
        spatial_options=SPATIAL_WITH_AUTO,
        visible={"min_mod": MCS_DUAL, "max_mod": MCS_DUAL},
        hidden_keys=frozenset({"max_data_single", "max_data_dual", "modulation_index"}),
    ),
    _DdrsUiScenario(
        ddrs="Disable",
        spatial="Single",
        spatial_options=SPATIAL_WITHOUT_AUTO,
        visible={"modulation_index": MCS_SINGLE},
        hidden_keys=frozenset({"max_data_single", "max_data_dual", "min_mod", "max_mod"}),
    ),
    _DdrsUiScenario(
        ddrs="Disable",
        spatial="Dual",
        spatial_options=SPATIAL_WITHOUT_AUTO,
        visible={"modulation_index": MCS_DUAL},
        hidden_keys=frozenset({"max_data_single", "max_data_dual", "min_mod", "max_mod"}),
    ),
)


async def _assert_ddrs_status_dropdown(page):
    options = await _dropdown_options(page, RadioPropertiesLocators.DDRS_STATUS_DROPDOWN)
    texts = {opt["text"] for opt in options if not opt["disabled"]}
    assert texts == DDRS_STATUS_OPTIONS, (
        f"DDRS Status dropdown must be Enable/Disable; got {sorted(texts)}"
    )


async def _assert_spatial_stream_options(page, expected: frozenset[str]):
    options = await _dropdown_options(page, RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN)
    texts = {opt["text"] for opt in options if not opt["disabled"]}
    assert texts == expected, (
        f"Spatial Stream options expected {sorted(expected)}; got {sorted(texts)}"
    )


_KNOWN_LOCATORS: dict[str, str] = {
    "modulation_index": RadioPropertiesLocators.MODULATION_INDEX_DROPDOWN,
    "max_data_single": "#maxsinglemcs, select[name='txparam.ath1.maxsinglemcs']",
    "max_data_dual": "#maxdualmcs, select[name='txparam.ath1.maxdualmcs']",
    "min_mod": "#minrateid, select[name='txparam.ath1.ddrsminrate']",
    "max_mod": "#maxrateid, select[name='txparam.ath1.ddrsmaxrate']",
}


async def _augment_fields_from_known_locators(page, by_key: dict[str, dict[str, Any]], keys: frozenset[str]):
    """Use stable IDs when LuCI field titles differ slightly from expected wording."""
    for key in keys:
        if key in by_key:
            continue
        locator = _KNOWN_LOCATORS.get(key)
        if not locator:
            continue
        element = page.locator(locator).first
        if await element.count() == 0:
            continue
        try:
            await element.wait_for(state="attached", timeout=3000)
        except Exception:
            continue
        visible = await element.evaluate(
            """
            el => {
                const row = el.closest('.cbi-value');
                if (!row) return el.offsetParent !== null;
                const style = window.getComputedStyle(row);
                return (
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    row.offsetParent !== null
                );
            }
            """
        )
        options_raw = await _dropdown_options(page, locator)
        options = [o["text"] for o in options_raw if not o["disabled"]]
        by_key[key] = {
            "key": key,
            "label": key,
            "selector": locator,
            "visible": visible,
            "options": options,
        }


async def _set_dropdown_and_save(page, locator: str, token: str):
    """Change one dropdown and Save so dependent DDRS fields refresh visibility."""
    options = await _dropdown_options(page, locator)
    option = _resolve_dropdown_option(options, token)
    assert option, f"Unable to resolve '{token}' in {locator}."
    await _set_dropdown_value(page, locator, option["value"])
    await execute_form_save(page)


async def _set_ddrs_and_spatial(
    page,
    ddrs: str,
    spatial: str,
    *,
    radio_page=None,
):
    await _set_dropdown_and_save(page, RadioPropertiesLocators.DDRS_STATUS_DROPDOWN, ddrs)
    if radio_page is not None and spatial == "Auto":
        await radio_page.open_ddrs_atpc()
        spatial_opts = await _dropdown_options(page, RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN)
        spatial_texts = {o["text"] for o in spatial_opts if not o["disabled"]}
        assert spatial in spatial_texts, (
            f"Spatial '{spatial}' not available after DDRS={ddrs}; got {sorted(spatial_texts)}"
        )
    await _set_dropdown_and_save(page, RadioPropertiesLocators.SPATIAL_STREAM_DROPDOWN, spatial)


async def _validate_scenario(target: dict[str, Any], scenario: _DdrsUiScenario):
    page = target["page"]
    role = target["role"]
    _log(role, f"DDRS UI check: DDRS={scenario.ddrs}, Spatial={scenario.spatial}")

    await _set_ddrs_and_spatial(
        page, scenario.ddrs, scenario.spatial, radio_page=target["radio_page"]
    )

    await _assert_spatial_stream_options(page, scenario.spatial_options)

    fields = await _scan_ddrs_fields(page)
    by_key = _field_map(fields)
    needed = frozenset(scenario.visible) | scenario.hidden_keys
    await _augment_fields_from_known_locators(page, by_key, needed)
    discovered = ", ".join(f"{k}={v['label']!r}" for k, v in by_key.items()) or "(none)"

    for key, expected_mcs in scenario.visible.items():
        entry = by_key.get(key)
        assert entry, (
            f"Expected visible field '{key}' for DDRS={scenario.ddrs}, Spatial={scenario.spatial}; "
            f"discovered: {discovered}"
        )
        assert entry["visible"], (
            f"Field '{key}' ({entry['label']!r}) should be visible for "
            f"DDRS={scenario.ddrs}, Spatial={scenario.spatial}."
        )
        assert entry["options"], f"Field '{key}' has no dropdown options."
        _assert_mcs_range(key, entry["label"], entry["options"], expected_mcs)

    for key in scenario.hidden_keys:
        entry = by_key.get(key)
        if entry is None:
            continue
        assert not entry["visible"], (
            f"Field '{key}' ({entry['label']!r}) should be hidden for "
            f"DDRS={scenario.ddrs}, Spatial={scenario.spatial}."
        )


async def _restore_ddrs_baseline(target: dict[str, Any], ddrs_token: str, spatial_token: str):
    page = target["page"]
    fallback = target["radio_page"].RADIO_1_DDRS_URL_CHUNK
    await target["radio_page"].open_ddrs_atpc()

    # Auto is only listed when DDRS is Enable — matrix may end on Disable.
    if spatial_token == "Auto":
        ddrs_token = "Enable"

    await _set_ddrs_and_spatial(
        page, ddrs_token, spatial_token, radio_page=target["radio_page"]
    )

    await execute_triple_apply(page, fallback)
    await page.wait_for_timeout(UITimeouts.SHORT_WAIT_MS)


async def validate_ddrs_page(target: dict[str, Any]):
    """
    GUI_24: DDRS status has Enable/Disable, each DDRS+spatial combo shows the right
    dependent dropdowns after Save (no per-step triple-apply). One triple-apply at end.
    """
    await target["radio_page"].open_ddrs_atpc()
    await _assert_ddrs_status_dropdown(target["page"])

    for scenario in _SCENARIOS:
        await _validate_scenario(target, scenario)

    ddrs_restore = DEFAULT_VALUES.get("DDRS Status", "Enable")
    spatial_restore = DEFAULT_VALUES.get("Spatial Stream", "Auto")
    await _restore_ddrs_baseline(target, ddrs_restore, spatial_restore)
    _log(target["role"], "DDRS page validation complete.")


# Back-compat alias
validate_ddrs_ui_matrix = validate_ddrs_page
