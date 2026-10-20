"""The real UI (ai_switch/web/app) driven in Chromium against the backend with fake AIs."""
import pytest

pytest.importorskip("playwright")

from conftest import FakeAI  # noqa: E402

pytestmark = pytest.mark.browser


@pytest.fixture
def page(app_server, chromium_path):
    if not chromium_path:
        pytest.skip("no Chromium available")
    from playwright.sync_api import sync_playwright

    base, token, _ = app_server
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path, args=["--no-sandbox"])
        # 1366×768 laptop at 125 % Windows scaling ≈ 1093×614 CSS pixels inside the window
        pg = browser.new_page(viewport={"width": 1093, "height": 614})
        pg.goto(f"{base}/#token={token}")
        pg.wait_for_selector("#from-tiles .tile")
        yield pg
        browser.close()


def test_home_pickers_rules(page):
    assert page.locator("#from-tiles .tile").count() == 9
    assert page.inner_text("#version") == "v1.0.0"
    assert page.is_disabled("#start")
    page.click('#from-tiles [data-id="claude"]')
    assert page.is_disabled('#to-tiles [data-id="claude"]')  # can't pick the same AI twice
    page.click('#to-tiles [data-id="grok"]')
    assert page.is_enabled("#start")
    assert page.is_disabled("#chats")  # Grok can't import chat history
    assert "can't import chat history" in page.get_attribute("#chats-wrap", "title")
    page.click('#to-tiles [data-id="gemini"]')
    assert page.is_enabled("#chats")
    page.click("#swap")
    assert page.get_attribute('#from-tiles [data-id="gemini"]', "aria-checked") == "true"
    assert page.get_attribute('#to-tiles [data-id="claude"]', "aria-checked") == "true"


def test_keyboard_only(page):
    page.focus('#from-tiles [data-id="chatgpt"]')
    page.keyboard.press("Enter")
    page.keyboard.press("ArrowRight")  # moves selection within the radio group
    assert page.get_attribute('#from-tiles [data-id="claude"]', "aria-checked") == "true"
    page.focus('#to-tiles [data-id="gemini"]')
    page.keyboard.press("Space")
    assert page.is_enabled("#start")


def test_fits_small_laptop_and_dark_mode(page):
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    box = page.locator("#start").bounding_box()
    assert box["y"] + box["height"] <= 614  # Start is visible without scrolling
    page.emulate_media(color_scheme="dark")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg != "rgb(244, 245, 247)"


def test_full_flow_privacy_and_done(page, world):
    world["chatgpt"] = FakeAI(memory=["Lives in Austin.", "Email is alex.rivera@example.com", "Partner is Sam."])
    page.click('#from-tiles [data-id="chatgpt"]')
    page.click('#to-tiles [data-id="claude"]')
    page.click("#start")
    page.wait_for_selector("text=Check before sending", timeout=15000)
    assert page.locator(".finding").count() == 1
    assert "alex.rivera@example.com" not in page.inner_text(".findings")  # masked by default
    page.click(".finding .eye")
    assert "alex.rivera@example.com" in page.inner_text(".findings")  # eye reveals
    page.click(".preview summary")
    page.locator(".fact textarea").last.fill("Partner is Sam Rivera.")
    page.click("text=Remove selected & continue")
    page.wait_for_selector("#screen-done:not([hidden])", timeout=20000)
    assert page.inner_text("#done-title") == "Moved 3 of 3 facts"
    assert "Partner is Sam Rivera." in world["claude"].memory
    assert not any("alex.rivera" in m for m in world["claude"].memory)
    page.click("text=Move another")
    page.wait_for_selector("#screen-home:not([hidden])")
    page.click('.nav[data-go="history"]')
    page.wait_for_selector(".hist")
    assert "3/3 facts" in page.inner_text("#hist-list")


def test_signin_card_in_ui(page, world, app_server):
    _, _, services = app_server
    world["gemini"] = FakeAI(memory=["Lives in Austin."])
    world["mistral"] = FakeAI(logged_in=False)
    page.click('#from-tiles [data-id="gemini"]')
    page.click('#to-tiles [data-id="mistral"]')
    page.click("#start")
    page.wait_for_selector("text=Sign in to Le Chat", timeout=15000)
    page.click("#card-area button.primary")
    page.wait_for_selector("text=Check before sending", timeout=15000)
    assert services.accounts.signins == ["mistral"]


def test_error_screen_is_friendly(page, world):
    world["claude"] = FakeAI(memory=[])  # nothing to export → every automatic method fails
    page.click('#from-tiles [data-id="claude"]')
    page.click('#to-tiles [data-id="gemini"]')
    page.click("#start")
    page.wait_for_selector("#card-area .card, #screen-done:not([hidden])", timeout=15000)
    if page.is_visible("#card-area .card"):  # final manual fallback card — user gives up
        page.click("#cancel")
    page.wait_for_selector("#screen-done:not([hidden])", timeout=15000)
    title = page.inner_text("#done-title")
    assert "Traceback" not in title and "Error" not in title
    assert page.is_visible("text=Try again")
    assert not page.is_visible("#error-pre")  # details collapsed by default


def test_accounts_and_settings_screens(page, world):
    world["claude"] = FakeAI(logged_in=False)
    page.click('.nav[data-go="accounts"]')
    page.wait_for_selector(".acc")
    assert page.locator(".acc").count() == 9
    assert "Not signed in" in page.inner_text(".acc:has-text('Claude')")
    assert "Signed in ✓" in page.inner_text(".acc:has-text('ChatGPT')")
    page.click('.nav[data-go="settings"]')
    page.select_option("#set-browser", "edge")
    page.wait_for_timeout(300)
    page.reload()
    page.wait_for_selector("#from-tiles .tile")
    page.click('.nav[data-go="settings"]')
    page.wait_for_timeout(300)
    assert page.input_value("#set-browser") == "edge"
