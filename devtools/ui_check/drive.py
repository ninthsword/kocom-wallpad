"""Drive Home Assistant's own frontend like a user, assert its Korean texts and keep screenshots."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import (
    APIRequestContext,
    Locator,
    Page,
    ViewportSize,
    expect,
    sync_playwright,
)

USER, PASSWORD = "verifier", "synthetic-pass-123"
HOST = "127.0.0.1"
PARENT = "Kocom Wallpad"
THERMOSTAT = "KOCOM THERMOSTAT"
DEVICES = (PARENT, THERMOSTAT, "KOCOM AIRCONDITIONER", "KOCOM", "KOCOM AIRQUALITY", "KOCOM LIGHT")
THERMOSTAT_ENTITIES = ("난방 0-0", "난방 1-0", "보일러 오류코드 0-0", "보일러 오류코드 1-0")
# The captured thermostat reports carry a current temperature of 20 degrees (wallpad.py).
CURRENT_TEMPERATURE = re.compile(r"현재 온도: ?20 ?°C")
ENTITY_COUNT = 19
LISTED_ENTITIES = ("엘리베이터 0-0", "움직임 감지기(현관) 0-0", "환기 0-0", "환기 CO2 0-0", "에어컨 2-0", "일괄소등 255-0")
CUTOFF_OPTION = "조명 명령 후 일괄소등 해제 추가 송신"
PORT_ERROR = "TCP 포트는 1부터 65535 사이의 값으로 입력하세요."
SEARCH = "공기질"
UNRELATED_ROW = "난방 1-0"  # visible in the entity list until the search filters it out
WAIT_MS = 30_000
DESKTOP: ViewportSize = {"width": 1440, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}
ADD_INTEGRATION = re.compile(r"통합 ?구성 ?요소 추가")


class StepError(RuntimeError):
    """A browser step did not reach its screen or a named text was not visible."""


def _post(
    api: APIRequestContext,
    path: str,
    *,
    data: dict[str, object] | None = None,
    form: dict[str, str | float | bool] | None = None,
    token: str | None = None,
) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if form is not None:
        response = api.post(path, form=form, headers=headers, timeout=WAIT_MS)
    else:
        response = api.post(path, data=data or {}, headers=headers, timeout=WAIT_MS)
    if not response.ok:
        raise StepError(f"onboarding: POST {path} answered {response.status}")
    body = response.json() if response.body() else {}
    return body if isinstance(body, dict) else {}


def onboard(api: APIRequestContext, base: str) -> None:
    """Create the first user and finish onboarding so the frontend shows its login page."""
    client = base + "/"
    user = _post(
        api,
        "/api/onboarding/users",
        data={
            "client_id": client,
            "name": "검증 사용자",
            "username": USER,
            "password": PASSWORD,
            "language": "ko",
        },
    )
    token = _post(
        api,
        "/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": str(user["auth_code"]),
            "client_id": client,
        },
    )
    access = str(token["access_token"])
    for step in ("core_config", "analytics"):
        _post(api, f"/api/onboarding/{step}", token=access)
    _post(
        api,
        "/api/onboarding/integration",
        data={"client_id": client, "redirect_uri": base + "/?auth_callback=1"},
        token=access,
    )


def see(locator: Locator, text: str, screen: str) -> None:
    """Fail the step with the text in the reason unless it becomes visible within 30 s."""
    try:
        expect(locator.filter(visible=True).first).to_be_visible(timeout=WAIT_MS)
    except AssertionError as err:
        raise StepError(f"{screen}: '{text}' not visible") from err


def unseen(locator: Locator, text: str, screen: str) -> None:
    """Fail the step with the text in the reason unless no match is visible within 30 s."""
    try:
        expect(locator.filter(visible=True)).to_have_count(0, timeout=WAIT_MS)
    except AssertionError as err:
        raise StepError(f"{screen}: '{text}' still visible") from err


def kept(field: Locator, value: str, label: str, screen: str) -> None:
    """Fail the step unless the field still holds the value the user typed, within 30 s."""
    try:
        expect(field).to_have_value(value, timeout=WAIT_MS)
    except AssertionError as err:
        raise StepError(
            f"{screen}: {label} holds '{field.input_value()}' instead of '{value}'"
        ) from err


def see_text(page: Page, text: str, screen: str, *, exact: bool = False) -> None:
    see(page.get_by_text(text, exact=exact), text, screen)


def see_field_value(page: Page, label: str, value: str, screen: str) -> None:
    """Wait until a visible field labelled `label` holds `value` (editable device names)."""
    deadline = time.monotonic() + WAIT_MS / 1000
    while time.monotonic() < deadline:
        for box in page.get_by_role("textbox", name=label).all():
            if box.is_visible() and box.input_value() == value:
                return
        page.wait_for_timeout(500)
    raise StepError(f"{screen}: '{value}' not visible")


class Shots:
    def __init__(self, out: Path) -> None:
        self.out = out

    def take(self, page: Page, name: str) -> None:
        page.wait_for_timeout(1000)
        page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)


def accept_http_config(page: Page) -> None:
    """Confirm HA 2026.10's new-HTTP-settings dialog when it appears; skip it otherwise."""
    title = page.get_by_text("새 HTTP 서버 구성 확인")
    try:
        title.wait_for(timeout=8000)
    except Exception:
        return
    page.get_by_role("button", name="확인", exact=True).click()
    title.wait_for(state="hidden", timeout=WAIT_MS)


def keyboard_reach(page: Page, target: Locator, *, reverse: bool = False) -> None:
    """Reach a native control through the dialog's keyboard order."""
    for _ in range(25):
        page.keyboard.press("Shift+Tab" if reverse else "Tab")
        if target.evaluate("element => element.matches(':focus')"):
            expect(target).to_be_focused()
            assert target.evaluate("element => element.matches(':focus-visible')")
            return
    raise StepError("settings: keyboard could not reach the native control")


def contained_focus(
    dialog: Locator, phase: str, *, allow_browser_boundary: bool = False
) -> str:
    """Distinguish composed modal focus from a native outside-document boundary."""
    result = dialog.evaluate("""dialog => {
        let active = document.activeElement;
        while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
        const nativeModal = dialog instanceof HTMLDialogElement && dialog.open && dialog.matches(':modal');
        const path = [];
        let contained = false;
        for (let node = active; node; node = node.assignedSlot || node.parentNode || node.host) {
            path.push(node.nodeName);
            if (node === dialog) {
                contained = true;
                break;
            }
        }
        return {contained, path, focused: document.hasFocus(), nativeModal};
    }""")
    application_focus = result["contained"] and result["focused"] and result["nativeModal"]
    browser_boundary = (
        allow_browser_boundary
        and result["nativeModal"]
        and not result["focused"]
        and result["path"] == ["BODY", "HTML", "#document"]
    )
    category = "application" if application_focus else "browser_boundary" if browser_boundary else "invalid"
    print("Settings focus observation: " + json.dumps({"phase": phase, "category": category, **result}))
    assert application_focus or browser_boundary, f"settings {phase}: focus escaped the modal dialog: {result}"
    return category


def settings_captures(page: Page, shots: Shots, desktop: str, phone: str) -> None:
    for viewport, name in ((DESKTOP, desktop), (PHONE, phone)):
        page.set_viewport_size(viewport)
        expect(page.get_by_role("checkbox", name=CUTOFF_OPTION)).to_be_visible()
        shots.take(page, name)
    page.set_viewport_size(DESKTOP)


def reconfigure_settings(page: Page, base: str, shots: Shots) -> None:
    """Save, reopen and cancel the native one-checkbox flow at both sizes."""
    page.goto(base + "/config/integrations/integration/kocom_wallpad")
    see_text(page, f"{len(DEVICES)}개 기기", "reconfigure integration")
    cutoff = page.evaluate("""async uniqueId => {
        const hass = document.querySelector('home-assistant').hass;
        const entries = await hass.callWS({type: 'config/entity_registry/list'});
        const matches = entries.filter(entry => entry.platform === 'kocom_wallpad'
            && entry.unique_id === uniqueId && entry.entity_id.startsWith('binary_sensor.'));
        if (matches.length !== 1) throw new Error('Synthetic cutoff registry identity is not unique');
        const entityId = matches[0].entity_id;
        const state = hass.states[entityId];
        return {entity_id: entityId, state: state?.state, friendly_name: state?.attributes.friendly_name};
    }""", f"2-255_0-0:{HOST}")
    cutoff_entity_id = cutoff["entity_id"]
    print("Settings availability target: " + json.dumps(cutoff, ensure_ascii=False))

    def open_settings() -> tuple[Locator, Locator]:
        trigger = page.locator("ha-config-entry-row").get_by_role("button", name="메뉴", exact=True).first
        see(trigger, "메뉴", "reconfigure")
        trigger.focus()
        trigger.press("Enter")
        action = page.get_by_role("menuitem", name="재구성", exact=True)
        see(action, "재구성", "reconfigure menu")
        # Native dropdowns keep their own active item; navigate that item
        # instead of sending Enter directly to an unfocused menu locator.
        for _ in range(20):
            if action.evaluate("element => element.matches(':focus-within')"):
                break
            page.keyboard.press("ArrowDown")
        else:
            raise StepError("reconfigure menu: keyboard could not reach 재구성")
        page.keyboard.press("Enter")
        checkbox = page.get_by_role("checkbox", name=CUTOFF_OPTION)
        see(checkbox, CUTOFF_OPTION, "reconfigure")
        dialog = page.get_by_role("dialog").filter(visible=True).last
        contained_focus(dialog, "entry")
        flow = page.locator("dialog-data-entry-flow")
        expect(flow.get_by_role("textbox")).to_have_count(0)
        expect(flow.get_by_role("spinbutton")).to_have_count(0)
        # Native modal navigation may reach an outside-document boundary;
        # the next real key must return to the modal, never its webpage background.
        for key in ("Tab", "Shift+Tab"):
            needs_return = False
            for index in range(8):
                page.keyboard.press(key)
                category = contained_focus(dialog, f"{key} {index + 1}", allow_browser_boundary=True)
                if needs_return:
                    assert category == "application", "settings: keyboard did not return to the modal"
                needs_return = category == "browser_boundary"
            if needs_return:
                page.keyboard.press(key)
                contained_focus(dialog, f"{key} return")
        keyboard_reach(page, checkbox)
        contained_focus(dialog, "checkbox keyboard target")
        return dialog, trigger

    def save(dialog: Locator) -> None:
        flow = page.locator("dialog-data-entry-flow")
        button = flow.get_by_role("button", name=re.compile("^(확인|제출)$"))
        keyboard_reach(page, button)
        contained_focus(dialog, "submit Enter activation")
        button.press("Enter")
        # Wait for the response before inspecting a possible native success page.
        expect(flow.get_by_role("checkbox", name=CUTOFF_OPTION)).not_to_be_visible(timeout=WAIT_MS)
        finish = flow.get_by_role("button", name=re.compile("^(완료|닫기)$")).filter(visible=True)
        if finish.count():
            finish.last.click()
        expect(dialog).not_to_be_visible(timeout=WAIT_MS)
        page.wait_for_function("""entityId => {
            const state = document.querySelector('home-assistant').hass.states[entityId];
            return state && state.state !== 'unavailable' && state.state !== 'unknown';
        }""", arg=cutoff_entity_id, timeout=WAIT_MS)
        state = page.evaluate("""entityId => {
            const state = document.querySelector('home-assistant').hass.states[entityId];
            return {entity_id: entityId, state: state.state, friendly_name: state.attributes.friendly_name};
        }""", cutoff_entity_id)
        print("Settings availability after reload: " + json.dumps(state, ensure_ascii=False))

    # Initial setup saved off. Save on, reopen, capture and cancel a local toggle.
    for viewport in (DESKTOP, PHONE):
        page.set_viewport_size(viewport)
        dialog, _trigger = open_settings()
        checkbox = page.get_by_role("checkbox", name=CUTOFF_OPTION)
        expect(checkbox).not_to_be_checked()
        expect(checkbox).to_be_focused()
        contained_focus(dialog, "checkbox Space activation")
        checkbox.press("Space")
        expect(checkbox).to_be_checked()
        save(dialog)
        dialog, trigger = open_settings()
        expect(checkbox).to_be_checked()
        shots.take(page, "17-reconfigure-on-1440" if viewport == DESKTOP else "17-reconfigure-on-390")
        expect(checkbox).to_be_focused()
        contained_focus(dialog, "checkbox Space activation before cancel")
        checkbox.press("Space")
        expect(checkbox).not_to_be_checked()
        page.keyboard.press("Escape")
        # Core protects dirty forms from accidental Escape dismissal.
        expect(dialog).to_be_visible()
        expect(checkbox).not_to_be_checked()
        close = page.locator("dialog-data-entry-flow").get_by_role("button", name="닫기", exact=True)
        keyboard_reach(page, close)
        contained_focus(dialog, "native Close Enter activation")
        close.press("Enter")
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        dialog, trigger = open_settings()
        expect(checkbox).to_be_checked()  # Closing discarded the unsaved local change.
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        dialog, _trigger = open_settings()
        expect(checkbox).to_be_checked()
        expect(checkbox).to_be_focused()
        contained_focus(dialog, "checkbox Space activation")
        checkbox.press("Space")
        expect(checkbox).not_to_be_checked()
        save(dialog)
        dialog, trigger = open_settings()
        expect(checkbox).not_to_be_checked()
        shots.take(page, "18-reconfigure-off-1440" if viewport == DESKTOP else "18-reconfigure-off-390")
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
    page.set_viewport_size(DESKTOP)


def steps(page: Page, base: str, shots: Shots, wallpad_port: int) -> None:
    page.goto(base + "/")
    user = page.get_by_label("사용자 이름")
    password = page.locator('input[type="password"]')
    see(user, "사용자 이름", "login")
    see(password, "비밀번호", "login")
    user.fill(USER)
    password.fill(PASSWORD)
    shots.take(page, "00-login")
    page.get_by_role("button", name="로그인").click()
    page.wait_for_url(lambda url: "auth" not in url, timeout=WAIT_MS)
    see_text(page, "설정", "home", exact=True)
    shots.take(page, "01-home")

    page.goto(base + "/config/integrations/dashboard")
    add = page.get_by_role("button", name=ADD_INTEGRATION)
    see(add, "통합 구성 요소 추가", "integrations")
    accept_http_config(page)
    shots.take(page, "02-integrations")

    add.click()
    page.wait_for_timeout(1500)
    page.keyboard.type("Kocom")
    see_text(page, "Kocom Wallpad", "integration search")
    shots.take(page, "03-search")
    page.get_by_text("Kocom Wallpad").filter(visible=True).first.click()

    screen = "config flow user step"
    see_text(page, "장치의 주소와 포트를 입력하세요.", screen)
    host = page.get_by_role("textbox", name="호스트")
    port = page.get_by_role("spinbutton", name="포트")
    see(host, "호스트", screen)
    see(port, "포트", screen)
    # The frontend replaces the host's description with its "필수" error when the empty field
    # loses focus (observed in a failed run), so the host is filled before the description is read.
    host.fill(HOST)
    see_text(page, "시리얼 통신인 경우 호스트에 입력하고 포트는 무시하세요.", screen)
    checkbox = page.get_by_role("checkbox", name=CUTOFF_OPTION)
    see(checkbox, CUTOFF_OPTION, screen)
    expect(checkbox).not_to_be_checked()
    port.focus()
    keyboard_reach(page, checkbox)
    checkbox.press("Space")
    expect(checkbox).to_be_checked()
    port.fill("0")
    page.get_by_role("button", name=re.compile("^(확인|제출)$")).click()
    see_text(page, PORT_ERROR, f"{screen} with port 0")
    # The form keeps what was typed after an error, so only the port is corrected.
    kept(host, HOST, "호스트", f"{screen} after the port error")
    kept(port, "0", "포트", f"{screen} after the port error")
    expect(checkbox).to_be_checked()
    settings_captures(page, shots, "04-flow-port-error", "04-flow-port-error-390")
    port.fill(str(wallpad_port))
    port.focus()
    keyboard_reach(page, checkbox, reverse=True)
    checkbox.press("Space")
    expect(checkbox).not_to_be_checked()
    settings_captures(page, shots, "05-flow-user", "05-flow-user-390")
    page.get_by_role("button", name=re.compile("^(확인|제출)$")).click()

    screen = "config flow device step"
    see_text(page, "이름 지정 및 할당", screen)
    see_field_value(page, "기기 이름", PARENT, screen)
    see_field_value(page, "기기 이름", THERMOSTAT, screen)
    finish = page.get_by_role("button", name="건너뛰고 완료")
    see(finish, "건너뛰고 완료", screen)
    shots.take(page, "06-flow-device")
    finish.click()

    page.goto(base + "/config/integrations/integration/kocom_wallpad")
    see_text(page, f"{len(DEVICES)}개 기기", "Kocom Wallpad integration page")
    see_text(page, f"{ENTITY_COUNT}개 엔티티", "Kocom Wallpad integration page")
    shots.take(page, "07-integration")

    page.goto(base + "/config/devices/dashboard")
    for name in DEVICES:
        see_text(page, name, "device list", exact=True)
    shots.take(page, "08-devices")

    screen = f"device page {THERMOSTAT}"
    page.get_by_text(THERMOSTAT, exact=True).filter(visible=True).first.click()
    page.wait_for_url(re.compile(r"/config/devices/device/"), timeout=WAIT_MS)
    see_text(page, THERMOSTAT, screen, exact=True)
    for name in THERMOSTAT_ENTITIES:
        see_text(page, name, screen, exact=True)
    see(page.get_by_text(CURRENT_TEMPERATURE), "현재 온도: 20 °C", screen)
    shots.take(page, "09-device")
    page.set_viewport_size(PHONE)
    see_text(page, THERMOSTAT, f"{screen} at 390 px", exact=True)
    shots.take(page, "10-device-390")
    page.set_viewport_size(DESKTOP)

    page.goto(base + "/config/entities?domain=kocom_wallpad")
    see_text(page, UNRELATED_ROW, "entity list", exact=True)
    for name in LISTED_ENTITIES:
        see_text(page, name, "entity list", exact=True)
    shots.take(page, "11-entities")
    search = page.get_by_placeholder(re.compile("엔티티 검색"))
    see(search, "엔티티 검색", "entity list")
    search.fill(SEARCH)
    see_text(page, f"{SEARCH} CO2 1-0", "entity search", exact=True)
    unseen(page.get_by_text(UNRELATED_ROW, exact=True), UNRELATED_ROW, "entity search")
    shots.take(page, "12-entity-search")


    page.goto(base + "/config/devices/dashboard")
    page.get_by_text("KOCOM", exact=True).filter(visible=True).first.click()
    page.get_by_text("환기 0-0", exact=True).filter(visible=True).first.click()
    fan = page.locator("ha-more-info-dialog")
    fan_dialog = page.get_by_role("dialog").filter(visible=True).last
    see(fan_dialog, "환기", "fan more-info")
    selector = fan.get_by_role("button", name=re.compile("모드"))
    see(selector, "모드", "fan preset selector")
    for viewport, name in ((DESKTOP, "13-fan-1440"), (PHONE, "14-fan-390")):
        page.set_viewport_size(viewport)
        selector.focus()
        selector.press("Enter")
        see_text(page, "자동", "fan preset options", exact=True)
        see_text(page, "환기", "fan preset options", exact=True)
        shots.take(page, name)
        page.keyboard.press("Escape")
        expect(selector).to_be_focused()
    page.keyboard.press("Escape")
    expect(fan_dialog).not_to_be_visible()

    page.set_viewport_size(DESKTOP)
    page.goto(base + "/config/devices/dashboard")
    page.get_by_text("KOCOM LIGHT", exact=True).filter(visible=True).first.click()
    page.get_by_text("일괄소등 255-0", exact=True).filter(visible=True).first.click()
    cutoff = page.locator("ha-more-info-dialog")
    cutoff_dialog = page.get_by_role("dialog").filter(visible=True).last
    see(cutoff_dialog, "일괄소등", "cutoff more-info")
    for viewport, name in ((DESKTOP, "15-cutoff-1440"), (PHONE, "16-cutoff-390")):
        page.set_viewport_size(viewport)
        see_text(page, "일괄소등", "cutoff more-info")
        for role in ("switch", "slider", "combobox"):
            expect(cutoff.get_by_role(role)).to_have_count(0)
        expect(cutoff.get_by_role("button", name=re.compile("켜기|끄기|토글"))).to_have_count(0)
        shots.take(page, name)
    page.keyboard.press("Escape")
    expect(cutoff_dialog).not_to_be_visible()
    reconfigure_settings(page, base, shots)


def run(base: str, out: Path, wallpad_port: int) -> None:
    """Onboard through the API, then drive the screens; raise on the first failed step."""
    shots = Shots(out)
    with sync_playwright() as p:
        api = p.request.new_context(base_url=base)
        try:
            onboard(api, base)
        finally:
            api.dispose()
        browser = p.chromium.launch()
        try:
            context = browser.new_context(
                viewport=DESKTOP,
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            page = context.new_page()
            try:
                steps(page, base, shots, wallpad_port)
            except BaseException:
                try:
                    page.screenshot(path=str(out / "failure.png"), full_page=True)
                except Exception:
                    pass
                raise
        finally:
            browser.close()
