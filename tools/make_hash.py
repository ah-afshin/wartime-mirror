"""Generate a password hash for ACCESS_PASSWORD_HASH:  python tools/make_hash.py"""
import getpass

from werkzeug.security import generate_password_hash

pw = getpass.getpass("Password: ")
if pw != getpass.getpass("Repeat:   "):
    raise SystemExit("Passwords differ.")
if len(pw) < 10:
    raise SystemExit("Use at least 10 characters.")
print(generate_password_hash(pw))
