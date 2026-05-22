import re
from pages.locators import TopPanelLocators
from utils.validators import validate_param
from utils.parsers import extract_uci_value
from config.defaults import DEFAULT_VALUES


# =====================================================================
# GLOBAL HELPER: Auto-Accept Alerts
# =====================================================================
def attach_dialog_handler(gui_page):
    """Ensures Playwright auto-accepts 'Are you sure?' popups instead of cancelling them."""
    if not hasattr(gui_page, "_dialog_handler_attached"):
        async def handle_dialog(dialog):
            print(f"    -> [UI ALERT DETECTED] Auto-accepting popup: '{dialog.message}'")
            await dialog.accept()

        gui_page.on("dialog", handle_dialog)
        gui_page._dialog_handler_attached = True


# =====================================================================
# UNIVERSAL HELPER: The Triple-Apply Sequence
# =====================================================================
async def execute_triple_apply(gui_page, fallback_url):
    """Executes the sequence: Form Save -> Top Apply -> Super Apply"""
    print("    -> Executing Triple-Apply sequence...")

    # 1. Form Save (Universal)
    form_save = gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).first
    await form_save.scroll_into_view_if_needed()
    await form_save.wait_for(state="visible", timeout=5000)

    try:
        btn_text = await form_save.evaluate("el => el.value || el.innerText || el.textContent")
        print(f"    -> [DEBUG] About to click Save target: '{btn_text.strip()}'")
    except Exception:
        pass

    if await form_save.evaluate("el => el.disabled"):
        print("    -> [DEBUG] Target was DISABLED! Stripping attribute...")
        await form_save.evaluate("el => el.removeAttribute('disabled')")

    print("    -> [DEBUG] Waiting 1 second before Save click...")
    await gui_page.wait_for_timeout(1000)

    # NATIVE FORM SUBMISSION
    print("    -> [DEBUG] Firing click on Save target...")
    await form_save.click(force=True)

    try:
        await gui_page.wait_for_load_state("domcontentloaded", timeout=6000)
    except Exception:
        pass

    await gui_page.wait_for_timeout(3000)

    # 2. Top Panel Apply (Goes to pending changes screen)
    top_apply = gui_page.locator(TopPanelLocators.APPLY_BUTTON).first
    try:
        await top_apply.wait_for(state="visible", timeout=5000)
        print("    -> [DEBUG] Waiting 1 second before Top Apply click...")
        await gui_page.wait_for_timeout(1000)

        await top_apply.click(force=True)
        print("    -> [DEBUG] Top Apply button clicked.")
        try:
            await gui_page.wait_for_load_state("domcontentloaded", timeout=6000)
        except Exception:
            pass
        await gui_page.wait_for_timeout(2000)
    except Exception:
        print(
            "    -> [DEBUG-WARNING] Top Apply button not visible! The Save action failed or was ignored by the router.")

    # 3. Confirm Apply (Super Apply)
    super_apply = gui_page.locator(TopPanelLocators.SUPER_APPLY_BUTTON).first
    try:
        await super_apply.wait_for(state="visible", timeout=5000)
        print("    -> [DEBUG] Waiting 1 second before Super Apply click...")
        await gui_page.wait_for_timeout(1000)

        await super_apply.click(force=True)
        print("    -> [DEBUG] Super Apply button clicked! Committing to backend...")

        # CRITICAL FIX: Give the browser time to navigate to the Apply screen!
        await gui_page.wait_for_timeout(3000)

    except Exception:
        print("    -> [DEBUG] Super Apply button skipped (not found).")

    # 4. Wait for router to restart services and return
    try:
        await gui_page.wait_for_url(f"**{fallback_url}*", timeout=25000)
    except Exception:
        print(f"    -> WARNING: Did not automatically return. Forcing navigation to {fallback_url}")
        match = re.search(r'(https?://[^/]+/cgi-bin/luci/;stok=[^/]+)', gui_page.url)
        if match:
            base_url_with_token = match.group(1)
            full_target_url = base_url_with_token + fallback_url
            last_exc = None
            for attempt in range(4):
                try:
                    if attempt:
                        wait_ms = 10000 if attempt < 3 else 15000
                        print(f"    -> [DEBUG] Retry {attempt + 1}/4 after {wait_ms // 1000}s wait...")
                        await gui_page.wait_for_timeout(wait_ms)
                    await gui_page.goto(full_target_url, timeout=20000)
                    await gui_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    break
                except Exception as e:
                    last_exc = e
                    print("    -> [DEBUG] Navigation retry failed while services restarted.")
            else:
                print(f"    -> [DEBUG] Unable to recover page after apply: {last_exc}")


# =====================================================================
# UNIVERSAL HELPER: Discard Pending Changes
# =====================================================================
async def execute_super_revert(gui_page, fallback_url):
    """Clicks the Top Panel Apply, then hits Super Revert to discard staged changes."""
    print("    -> Discarding staged changes via Super Revert...")

    top_apply = gui_page.locator(TopPanelLocators.APPLY_BUTTON).first
    try:
        await top_apply.wait_for(state="visible", timeout=4000)
        await top_apply.click(force=True)
        await gui_page.wait_for_timeout(1500)
    except Exception:
        pass

    super_revert = gui_page.locator(TopPanelLocators.SUPER_REVERT_BUTTON).first
    try:
        await super_revert.wait_for(state="visible", timeout=4000)
        await super_revert.click(force=True)
    except Exception:
        pass

    try:
        await gui_page.wait_for_url(f"**{fallback_url}*", timeout=15000)
    except Exception:
        pass


# =====================================================================
# UNIVERSAL HELPER: Dropdown Validation & Reversion
# =====================================================================
async def validate_dropdown_lifecycle(
    gui_page,
    root_ssh,
    locator,
    expected_options,
    uci_cmd,
    param_name,
    fallback_url,
    parser=lambda x: x,
    test_all_options=False,
    skip_restore=False,
    *,
    use_gui_options=False,
):
    """Validates options, tests changes, applies, verifies, and auto-restores from config."""

    attach_dialog_handler(gui_page)

    element = gui_page.locator(locator).first
    await element.wait_for(state="visible", timeout=15000)

    # CRITICAL FIX: Wait for background AJAX to physically inject the <option> tags
    try:
        await element.locator("option").first.wait_for(state="attached", timeout=5000)
    except Exception:
        print(f"    -> [DEBUG] Warning: Dropdown options took too long to populate for {param_name}.")

    await gui_page.wait_for_timeout(2000)  # Give UI an extra 2 seconds to settle its data

    # 1. Fetch current backend state
    ssh_orig_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
    original_value = parser(ssh_orig_raw)

    print(f"    -> [DEBUG] Initial Raw SSH Output: '{ssh_orig_raw}'")
    print(f"    -> Initial Backend Value: {original_value}")

    restore_value = DEFAULT_VALUES.get(param_name, original_value)

    # 2. Validate GUI dropdown options (device may expose a subset, e.g. no 160 MHz on some radios)
    actual_options = await element.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
    assert actual_options, f"No dropdown options rendered for {param_name}."
    if use_gui_options:
        unknown = set(actual_options) - set(expected_options)
        assert not unknown, (
            f"Unexpected {param_name} options on GUI: {sorted(unknown)}. "
            f"Allowed catalog: {expected_options}"
        )
        options_pool = actual_options
        print(f"    -> {param_name} options from GUI: {actual_options}")
    else:
        assert set(actual_options) == set(
            expected_options
        ), f"Options mismatch for {param_name}! Expected: {expected_options}, Got: {actual_options}"
        options_pool = expected_options

    # 3. Determine test sequence
    filtered_options = [opt for opt in options_pool if opt.lower() != original_value.lower()]

    if test_all_options:
        values_to_test = filtered_options
        print(f"    -> Exhaustive testing enabled. Will test: {values_to_test}")
    else:
        values_to_test = filtered_options[:1] if filtered_options else []
        print(f"    -> Single change testing enabled. Will test: {values_to_test}")

    # 4. Loop through and test configurations
    last_tested_value = original_value
    for test_value in values_to_test:
        print(f"    -> Applying and Verifying: '{test_value}'")

        element = gui_page.locator(locator).first
        await element.wait_for(state="visible")

        # HUMAN KEYBOARD SIMULATION
        await element.focus()
        await element.click()
        await gui_page.wait_for_timeout(500)
        await gui_page.keyboard.type(test_value)
        await gui_page.wait_for_timeout(500)
        await gui_page.keyboard.press("Enter")
        await element.blur()

        print("    -> [DEBUG] Keyboard simulation complete. Waiting 1 second for DOM...")
        await gui_page.wait_for_timeout(1000)

        await execute_triple_apply(gui_page, fallback_url)

        # Verify Backend
        ssh_new_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
        print(f"    -> [DEBUG] Post-Apply Raw SSH Output: '{ssh_new_raw}'")
        new_backend_value = parser(ssh_new_raw)
        validate_param(f"Backend {param_name} Change ({test_value})", test_value.lower(), new_backend_value.lower())

        # Verify Frontend
        print("    -> [DEBUG] Waiting 3 seconds for frontend UI AJAX to repopulate selection...")
        await gui_page.wait_for_timeout(3000)
        element = gui_page.locator(locator).first
        await element.wait_for(state="visible", timeout=15000)

        # SMART FRONTEND EVALUATOR (Safe Fallbacks)
        gui_actual_value = await element.evaluate("""el => {
            if (el.selectedIndex >= 0 && el.options[el.selectedIndex]) {
                let text = el.options[el.selectedIndex].text.trim();
                if (text) return text;
            }
            let opt = Array.from(el.options).find(o => o.value === el.value);
            return opt ? opt.text.trim() : 'None Selected';
        }""")
        validate_param(f"Frontend {param_name} Change ({test_value})", test_value, gui_actual_value.strip())

        last_tested_value = test_value

    # 5. Restore Default State
    if skip_restore:
        print(f"    -> skip_restore=True. Leaving {param_name} at '{last_tested_value}'.")
    elif last_tested_value.lower() != restore_value.lower():
        print(f"    -> Restoring {param_name} to centralized default: '{restore_value}'")
        element = gui_page.locator(locator).first
        await element.wait_for(state="visible")

        # HUMAN KEYBOARD SIMULATION (For Restore)
        await element.focus()
        await element.click()
        await gui_page.wait_for_timeout(500)
        await gui_page.keyboard.type(restore_value)
        await gui_page.wait_for_timeout(500)
        await gui_page.keyboard.press("Enter")
        await element.blur()

        print("    -> [DEBUG] Option restored via keyboard. Waiting 1 second for DOM...")
        await gui_page.wait_for_timeout(1000)

        await execute_triple_apply(gui_page, fallback_url)

        # Verify Restore
        ssh_restore_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
        restored_backend_value = parser(ssh_restore_raw)
        validate_param(f"Final Default Restore ({param_name})", restore_value.lower(), restored_backend_value.lower())
    else:
        print(f"    -> {param_name} is already at centralized default '{restore_value}'. Skipping final apply.")

    print(f"    -> {param_name} successfully tested.")


async def validate_dropdown_value_lifecycle(
    gui_page,
    root_ssh,
    locator,
    uci_cmd,
    param_name,
    fallback_url,
    *,
    expected_options=None,
    test_options=None,
    skip_restore=False,
):
    """Validates dropdowns whose GUI text differs from stored backend option values."""

    attach_dialog_handler(gui_page)

    element = gui_page.locator(locator).first
    await element.wait_for(state="attached", timeout=15000)
    await gui_page.wait_for_timeout(1000)

    options = await element.evaluate(
        """
        el => Array.from(el.options).map(opt => ({
            text: (opt.textContent || "").trim(),
            value: opt.value,
            disabled: !!opt.disabled
        })).filter(opt => opt.text)
        """
    )
    actual_texts = [opt["text"] for opt in options if not opt["disabled"]]
    if expected_options is not None:
        assert set(actual_texts) == set(expected_options), (
            f"Options mismatch for {param_name}! Expected: {expected_options}, Got: {actual_texts}"
        )

    option_by_text = {opt["text"]: opt for opt in options}
    option_by_value = {opt["value"]: opt for opt in options}

    backend_original = extract_uci_value((await root_ssh.send_command(uci_cmd)).result.strip())

    def resolve_option(token):
        token = str(token)
        if token in option_by_value:
            return option_by_value[token]
        if token in option_by_text:
            return option_by_text[token]
        return None

    restore_token = DEFAULT_VALUES.get(param_name, backend_original)
    restore_option = resolve_option(restore_token) or resolve_option(backend_original)

    selected_sequence = test_options or [
        opt["text"] for opt in options if not opt["disabled"] and opt["value"] != backend_original
    ]
    selected_options = [resolve_option(token) for token in selected_sequence]
    selected_options = [opt for opt in selected_options if opt]

    last_option = resolve_option(backend_original) or restore_option
    for opt in selected_options:
        print(f"    -> Applying and Verifying: '{opt['text']}'")
        element = gui_page.locator(locator).first
        await element.wait_for(state="attached", timeout=15000)
        if await element.is_visible():
            await element.select_option(value=opt["value"])
        else:
            await element.evaluate(
                """
                (el, value) => {
                    el.value = value;
                    Array.from(el.options).forEach(opt => {
                        opt.selected = opt.value === value;
                    });
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                }
                """,
                opt["value"],
            )
        await gui_page.wait_for_timeout(1000)
        await execute_triple_apply(gui_page, fallback_url)
        await gui_page.wait_for_timeout(2000)

        backend_new = extract_uci_value((await root_ssh.send_command(uci_cmd)).result.strip())
        validate_param(f"Backend {param_name} Change ({opt['text']})", opt["value"], backend_new)

        element = gui_page.locator(locator).first
        await element.wait_for(state="attached", timeout=15000)
        gui_value = await element.evaluate(
            """
            el => {
                const selected = el.options[el.selectedIndex];
                return selected ? (selected.textContent || "").trim() : "";
            }
            """
        )
        validate_param(f"Frontend {param_name} Change ({opt['text']})", opt["text"], gui_value)
        last_option = opt

    if skip_restore or not restore_option or not last_option:
        print(f"    -> {param_name} restore skipped.")
        return

    if last_option["value"] != restore_option["value"]:
        print(f"    -> Restoring {param_name} to centralized default: '{restore_option['text']}'")
        element = gui_page.locator(locator).first
        await element.wait_for(state="attached", timeout=15000)
        if await element.is_visible():
            await element.select_option(value=restore_option["value"])
        else:
            await element.evaluate(
                """
                (el, value) => {
                    el.value = value;
                    Array.from(el.options).forEach(opt => {
                        opt.selected = opt.value === value;
                    });
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                }
                """,
                restore_option["value"],
            )
        await gui_page.wait_for_timeout(1000)
        await execute_triple_apply(gui_page, fallback_url)

        backend_restored = extract_uci_value((await root_ssh.send_command(uci_cmd)).result.strip())
        validate_param(
            f"Final Default Restore ({param_name})",
            restore_option["value"],
            backend_restored,
        )

    print(f"    -> {param_name} successfully tested.")


# =====================================================================
# UNIVERSAL HELPER: Input Range Validation & Reversion
# =====================================================================
async def validate_input_lifecycle(gui_page, root_ssh, locator, valid_val, invalid_val, uci_cmd, param_name,
                                   fallback_url, parser=lambda x: x, skip_restore=False):
    """Tests invalid boundary, applies valid boundary, verifies, and auto-restores from config."""

    attach_dialog_handler(gui_page)

    element = gui_page.locator(locator).first
    await element.wait_for(state="visible", timeout=15000)

    ssh_orig_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
    original_value = parser(ssh_orig_raw)

    restore_value = DEFAULT_VALUES.get(param_name, original_value)

    # Test Invalid Boundary
    print(f"    -> Testing Invalid Boundary: '{invalid_val}'")
    await element.clear()

    await element.focus()
    await gui_page.keyboard.type(invalid_val, delay=50)
    await element.blur()

    form_save = gui_page.locator(TopPanelLocators.FORM_SAVE_BUTTON).first
    await form_save.scroll_into_view_if_needed()

    if await form_save.evaluate("el => el.disabled"):
        await form_save.evaluate("el => el.removeAttribute('disabled')")

    await gui_page.wait_for_timeout(1000)
    await form_save.click(force=True)

    try:
        await gui_page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    await gui_page.wait_for_timeout(2000)

    if fallback_url not in gui_page.url:
        print("    -> WARNING: Invalid boundary allowed redirect to pending changes!")
        await execute_super_revert(gui_page, fallback_url)
        assert False, f"Validation failed! Router accepted invalid boundary '{invalid_val}' for {param_name}."
    else:
        print("    -> Invalid boundary correctly rejected by GUI.")
        await gui_page.reload()
        await element.wait_for(state="visible", timeout=15000)

    # Test Valid Boundary
    print(f"    -> Applying and Verifying Valid Config: '{valid_val}'")
    element = gui_page.locator(locator).first
    await element.clear()

    await element.focus()
    await gui_page.keyboard.type(valid_val, delay=50)
    await element.blur()

    await gui_page.wait_for_timeout(1000)

    await execute_triple_apply(gui_page, fallback_url)

    # Verify Backend
    ssh_new_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
    print(f"    -> [DEBUG] Post-Apply Raw SSH Output: '{ssh_new_raw}'")
    new_backend_value = parser(ssh_new_raw)
    validate_param(f"Backend {param_name} Change", valid_val, new_backend_value)

    # Verify Frontend
    print("    -> [DEBUG] Waiting 2 seconds for frontend UI to populate selection...")
    await gui_page.wait_for_timeout(2000)
    element = gui_page.locator(locator).first
    await element.wait_for(state="visible", timeout=15000)
    gui_actual_value = await element.input_value()
    validate_param(f"Frontend {param_name} Change", valid_val, gui_actual_value)

    # Restore Default State
    if skip_restore:
        print(f"    -> skip_restore=True. Leaving {param_name} at '{valid_val}'.")
    elif valid_val != restore_value:
        print(f"    -> Restoring {param_name} to centralized default: '{restore_value}'")
        element = gui_page.locator(locator).first
        await element.clear()

        await element.focus()
        await gui_page.keyboard.type(restore_value, delay=50)
        await element.blur()

        await gui_page.wait_for_timeout(1000)
        await execute_triple_apply(gui_page, fallback_url)

        ssh_restore_raw = (await root_ssh.send_command(uci_cmd)).result.strip()
        restored_backend_value = parser(ssh_restore_raw)
        validate_param(f"Final Default Restore ({param_name})", restore_value, restored_backend_value)
    else:
        print(f"    -> {param_name} is already at centralized default '{restore_value}'. Skipping final apply.")

    print(f"    -> {param_name} successfully tested.")