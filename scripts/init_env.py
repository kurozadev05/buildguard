#!/usr/bin/env python3
"""Create .env.local from .env.example with freshly generated secrets. Never overwrites an existing file (unless --force)."""
import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="replace an existing .env.local (generates NEW secrets)")
    p.add_argument("--admin-email", help="address that becomes administrator when it registers")
    a = p.parse_args()
    target, example = ROOT / ".env.local", ROOT / ".env.example"
    if target.exists() and not a.force:
        text = target.read_text()
        print(f"✓ .env.local already exists: left unchanged.{'  ⚠ It still contains CHANGE_ME placeholders.' if 'CHANGE_ME' in text else ''}")
        return 0
    text = example.read_text()
    pw = secrets.token_urlsafe(18)
    text = text.replace("POSTGRES_PASSWORD=CHANGE_ME", f"POSTGRES_PASSWORD={pw}").replace("buildguard:CHANGE_ME@", f"buildguard:{pw}@")
    text = text.replace("JWT_SECRET=CHANGE_ME", f"JWT_SECRET={secrets.token_urlsafe(48)}")
    email = a.admin_email
    if email is None and sys.stdin.isatty():
        email = input("Email address that should become the ADMINISTRATOR when it registers (Enter to keep admin@example.com): ").strip() or None
    if email:
        if "@" not in email or " " in email:
            sys.exit("That does not look like an email address.")
        text = text.replace("ADMIN_EMAIL=admin@example.com", f"ADMIN_EMAIL={email}")
    target.write_text(text)
    try:
        os.chmod(target, 0o600)                     # owner-only on Unix; ignored on Windows
    except OSError:
        pass
    print("✓ Created .env.local with newly generated JWT_SECRET and database password (it is git-ignored: do not share it).")
    print(f"  Administrator email: {email or 'admin@example.com'}: register with this address to become admin.")
    print("  AI is OFF by default (AI_PROVIDER=none). See README → 'Turn on the AI assistant'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
