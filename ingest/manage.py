"""Watchlist maintenance: remove tickers, reassign groups, prune data."""

import argparse

import db

TICKER_TABLES = (
    "stock_groups", "ratios_daily", "prices", "share_splits",
    "dividends", "fundamentals", "ingest_log", "estimates",
)


def remove(conn, ticker: str, purge: bool = False) -> None:
    """Drop a ticker from the watchlist.

    By default the ingested history stays put, so re-adding is instant and does
    not re-hit EDGAR. `--purge` removes the data too.
    """
    ticker = ticker.upper()
    exists = conn.execute("SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)).fetchone()
    if not exists:
        print(f"{ticker}: not on the watchlist")
        return

    conn.execute("DELETE FROM stock_groups WHERE ticker = ?", (ticker,))
    conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))

    if purge:
        for table in TICKER_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE ticker = ?", (ticker,))
    conn.commit()
    print(f"{ticker}: removed{' and purged' if purge else ''}")


def remove_unsupported(conn, purge: bool = False) -> None:
    rows = conn.execute(
        "SELECT ticker, unsupported_reason FROM watchlist WHERE supported = 0"
    ).fetchall()
    if not rows:
        print("no unsupported tickers")
        return
    for row in rows:
        print(f"  ({row['unsupported_reason']})")
        remove(conn, row["ticker"], purge=purge)


def assign(conn, ticker: str, group_names: list[str], make_default: bool) -> None:
    ticker = ticker.upper()
    for name in group_names:
        row = conn.execute("SELECT id FROM metric_groups WHERE name = ?", (name,)).fetchone()
        if not row:
            print(f"no such group: {name}")
            continue
        conn.execute(
            "INSERT OR IGNORE INTO stock_groups (ticker, group_id) VALUES (?, ?)",
            (ticker, row["id"]),
        )
        if make_default:
            conn.execute(
                "UPDATE watchlist SET default_group_id = ? WHERE ticker = ?",
                (row["id"], ticker),
            )
            make_default = False
    conn.commit()
    print(f"{ticker}: assigned to {', '.join(group_names)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchlist maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    p_remove = sub.add_parser("remove", help="Remove tickers from the watchlist")
    p_remove.add_argument("tickers", nargs="+")
    p_remove.add_argument("--purge", action="store_true", help="Also delete ingested data")

    p_unsupported = sub.add_parser(
        "remove-unsupported", help="Remove every ticker the pipeline cannot derive")
    p_unsupported.add_argument("--purge", action="store_true")

    p_assign = sub.add_parser("assign", help="Assign a ticker to groups")
    p_assign.add_argument("ticker")
    p_assign.add_argument("groups", nargs="+")
    p_assign.add_argument("--default", action="store_true",
                          help="Make the first group the default view")

    args = parser.parse_args()
    conn = db.connect()

    if args.command == "remove":
        for ticker in args.tickers:
            remove(conn, ticker, purge=args.purge)
    elif args.command == "remove-unsupported":
        remove_unsupported(conn, purge=args.purge)
    elif args.command == "assign":
        assign(conn, args.ticker, args.groups, args.default)

    conn.close()


if __name__ == "__main__":
    main()
