"""Set up a newly registered account.

One entry point for everything a fresh user needs before the app is useful to
them: the default metric profiles, and the starter alert rules that reference
those profiles by name.

They belong together because the second depends on the first — a group-scoped
rule looks its group up by name, and seeding rules before groups exist silently
skips them. Two separate calls from the web layer would work most of the time
and fail in exactly that order-dependent way that is hard to notice.
"""

import argparse

import alerts
import db
import groups
import run_log


def provision(conn, user_id: int, verbose: bool = True) -> dict[str, int]:
    if verbose:
        print(f"provisioning user {user_id}")

    groups.seed(conn, user_id=user_id, verbose=verbose)
    group_count = conn.execute(
        "SELECT COUNT(*) AS c FROM metric_groups WHERE user_id IS ?", (user_id,)
    ).fetchone()["c"]

    # After groups, never before: the semiconductor rule resolves its scope by
    # group name and is skipped if the group is not there yet.
    rules_added = alerts.seed_default_rules(conn, user_id=user_id, verbose=verbose)

    if verbose:
        print(f"  {group_count} group(s), {rules_added} alert rule(s)")
    return {"groups": group_count, "rules": rules_added}


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a new account")
    parser.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    provision(conn, args.user_id)
    conn.close()
    return 0


if __name__ == "__main__":
    run_log.tee_stdio("provision")
    raise SystemExit(main())
