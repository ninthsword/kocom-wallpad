"""Drive Home Assistant's own frontend like a user, assert its Korean texts and keep screenshots."""

from __future__ import annotations

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
DEVICES = (PARENT, THERMOSTAT, "KOCOM AIRCONDITIONER", "KOCOM", "KOCOM AIRQUALITY")
THERMOSTAT_ENTITIES = ("난방 0-0", "난방 1-0", "보일러 오류코드 0-0", "보일러 오류코드 1-0")
# The captured thermostat reports carry a current temperature of 20 degrees (wallpad.py).
CURRENT_TEMPERATURE = re.compile(r"현재 온도: ?20 ?°C")
ENTITY_COUNT = 18
LISTED_ENTITIES = ("엘리베이터 0-0", "움직임 감지기(현관) 0-0", "환기 0-0", "환기 CO2 0-0", "에어컨 2-0")
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
    port.fill("0")
    page.get_by_role("button", name=re.compile("^(확인|제출)$")).click()
    see_text(page, PORT_ERROR, f"{screen} with port 0")
    shots.take(page, "04-flow-port-error")
    # The form comes back with its defaults after an error, so both fields are filled again.
    host.fill(HOST)
    port.fill(str(wallpad_port))
    shots.take(page, "05-flow-user")
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
