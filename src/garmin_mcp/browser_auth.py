"""Browser-based authentication for Garmin Connect.

Opens a real Chromium browser for manual login, then captures
JWT and CSRF tokens for use with the MCP server.
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


CONNECT_URL = "https://connect.garmin.com"
DEFAULT_TOKEN_PATH = "~/.garminconnect"


def browser_login(token_path: str = None) -> bool:
    """Open browser for Garmin login, capture tokens, save them."""
    if token_path is None:
        token_path = os.getenv("GARMINTOKENS") or DEFAULT_TOKEN_PATH

    p = Path(token_path).expanduser()
    if p.is_dir() or not p.name.endswith(".json"):
        p = p / "garmin_tokens.json"
    p.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("Garmin MCP - Browser Authentication")
    print("=" * 60)
    print("\nA browser window will open.")
    print("1. Log in to Garmin Connect (including MFA)")
    print("2. Wait until you see the dashboard")
    print("3. Come back here and press ENTER\n")

    with sync_playwright() as pw:
        user_data_dir = str(Path("~/.garmin_browser_profile").expanduser())
        context = pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto(f"{CONNECT_URL}/modern/")

        input("Press ENTER once you are logged in to Garmin Connect...")

        # Navigate to the dashboard in THIS page to ensure we have the session
        print("\nCapturing tokens...")
        page.goto(f"{CONNECT_URL}/modern/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Extract tokens from cookies and page
        cookies = context.cookies()
        cookie_dict = {}
        jwt_web = None
        for c in cookies:
            if "garmin" in c["domain"]:
                cookie_dict[c["name"]] = c["value"]
                if c["name"] == "JWT_WEB":
                    jwt_web = c["value"]

        if not jwt_web:
            print("JWT_WEB cookie not found.", file=sys.stderr)
            context.close()
            return False

        # Get CSRF token from the page's meta tag or via fetch
        csrf_token = page.evaluate("""
            () => {
                // Try meta tag first
                const meta = document.querySelector('meta[name="csrf-token"]');
                if (meta) return meta.getAttribute('content');
                // Try window config
                if (window.__GARMIN_CONFIG__ && window.__GARMIN_CONFIG__.csrfToken)
                    return window.__GARMIN_CONFIG__.csrfToken;
                return null;
            }
        """)

        # If no CSRF from page, try the refresh endpoint
        if not csrf_token:
            result = page.evaluate("""
                async () => {
                    try {
                        const resp = await fetch('/services/auth/token/di-oauth/refresh', {
                            method: 'POST',
                            credentials: 'same-origin',
                            headers: { 'Accept': 'application/json', 'NK': 'NT' },
                        });
                        if (!resp.ok) return null;
                        const data = await resp.json();
                        return data.csrfToken || null;
                    } catch (e) { return null; }
                }
            """)
            csrf_token = result

        # If still no CSRF, try extracting from response headers via XHR
        if not csrf_token:
            csrf_token = page.evaluate("""
                async () => {
                    try {
                        const resp = await fetch('/gc-api/api/v1/userprofile-service/socialProfile', {
                            credentials: 'same-origin',
                            headers: { 'Accept': 'application/json', 'NK': 'NT' },
                        });
                        return resp.headers.get('x-csrf-token') || null;
                    } catch(e) { return null; }
                }
            """)

        print(f"  JWT_WEB: found ({len(jwt_web)} chars)", file=sys.stderr)
        print(f"  CSRF: {'found' if csrf_token else 'NOT FOUND (will try without)'}", file=sys.stderr)

        context.close()

    token_data = {
        "jwt_web": jwt_web,
        "csrf_token": csrf_token,
        "cookies": cookie_dict,
    }
    p.write_text(json.dumps(token_data))

    print(f"\n✓ Tokens saved to: {p}")
    print("✓ Browser authentication successful!")
    print(f"\nYou can now run the Garmin MCP server.")
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Authenticate with Garmin Connect via browser"
    )
    parser.add_argument(
        "--token-path", type=str, default=None,
        help="Custom token storage path (default: ~/.garminconnect/)",
    )
    args = parser.parse_args()
    success = browser_login(args.token_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
