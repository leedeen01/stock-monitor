import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def seed_user(conn, user_id: int = 1):
    """Give a fixture database an owner.

    Per-user tables carry a foreign key to users(id), so anything inserting a
    watchlist row, group or alert rule needs an account to hang it off. One
    helper beats repeating the insert in every test.
    """
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, email_lower, password_hash, "
        "role, created_at) VALUES (?, ?, ?, 'x', 'owner', '2024-01-01')",
        (user_id, f"u{user_id}@example.com", f"u{user_id}@example.com"),
    )
    conn.commit()
    return user_id
