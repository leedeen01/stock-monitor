/**
 * What to select in IBKR before the link will work.
 *
 * This page is used with Client Portal open in another tab, so the settings
 * that actually matter are inline and the exhaustive field lists are folded
 * away. Sending someone off to a separate document mid-setup is how a step
 * gets skipped.
 *
 * Kept in step with docs/ibkr-flex-query.md, which is the contract the parser
 * is written against.
 */

const CELL = "px-3 py-2 align-top";
const HEAD =
  "px-3 py-2 text-left text-xs font-medium text-neutral-500 dark:text-neutral-400";

function Detail({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="rounded-md border border-neutral-200 px-3 py-2 dark:border-neutral-800">
      <summary className="cursor-pointer text-xs font-medium">{summary}</summary>
      <div className="mt-2 text-xs leading-relaxed text-neutral-600 dark:text-neutral-400">
        {children}
      </div>
    </details>
  );
}

export function FlexQuerySetup() {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-neutral-200 p-4 text-sm dark:border-neutral-800">
      <div>
        <h2 className="font-medium">Set up the query in IBKR</h2>
        <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
          Client Portal → <strong>Performance &amp; Reports → Flex Queries</strong> →
          the <strong>+</strong> on <strong>Activity Flex Query</strong>.
        </p>
      </div>

      {/* Query-level settings: the ones that break the sync if wrong. */}
      <div className="overflow-x-auto rounded-md border border-neutral-200 dark:border-neutral-800">
        <table className="w-full min-w-[26rem] text-xs">
          <thead className="bg-neutral-50 dark:bg-neutral-900/60">
            <tr>
              <th className={HEAD}>Setting</th>
              <th className={HEAD}>Value</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100 dark:divide-neutral-900">
            <tr>
              <td className={CELL}>Format</td>
              <td className={CELL}>
                <strong>XML</strong> — not CSV
              </td>
            </tr>
            <tr>
              <td className={CELL}>Period</td>
              <td className={CELL}>
                <strong>Last 365 Calendar Days</strong>
              </td>
            </tr>
            <tr>
              <td className={CELL}>Breakout by Day</td>
              <td className={CELL}>
                <strong>No</strong>
              </td>
            </tr>
            <tr>
              <td className={CELL}>Date Format</td>
              <td className={CELL}>
                <code>yyyyMMdd</code>
              </td>
            </tr>
            <tr>
              <td className={CELL}>Symbols filter</td>
              <td className={CELL}>leave empty</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Required
        </h3>
        <p className="mt-1.5 text-xs text-neutral-600 dark:text-neutral-400">
          One section, at <strong>Summary</strong> level of detail — not Lot.
        </p>
        <p className="mt-1.5 rounded-md bg-neutral-50 px-3 py-2 text-xs dark:bg-neutral-900/60">
          <strong>Open Positions</strong> — with at least{" "}
          <code>Symbol</code>, <code>Quantity</code>, <code>CostBasisMoney</code>,{" "}
          <code>ReportDate</code>, <code>AssetClass</code> and{" "}
          <code>CurrencyPrimary</code>.
        </p>
        <p className="mt-1.5 text-xs text-neutral-500 dark:text-neutral-500">
          That alone is a working link. Everything below adds a feature.
        </p>
      </div>

      <div>
        <h3 className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Worth adding
        </h3>
        <ul className="mt-1.5 space-y-1 text-xs text-neutral-600 dark:text-neutral-400">
          <li>
            <strong>Cash Report</strong> → cash %, and the denominator that turns
            position values into real weights
          </li>
          <li>
            <strong>Trades</strong> → entry dates, so your buys can sit on the
            price history. <em>Order</em> level, not Execution.
          </li>
          <li>
            <strong>Corporate Actions</strong> → an independent record of splits,
            checked against ours
          </li>
          <li>
            <strong>Cash Transactions</strong> → dividends received, net of
            withholding
          </li>
          <li>
            <strong>NAV in Base</strong> and{" "}
            <strong>Financial Instrument Information</strong> → portfolio total,
            and reliable symbol matching
          </li>
        </ul>
      </div>

      <Detail summary="Full field list, section by section">
        <p className="font-medium text-neutral-700 dark:text-neutral-300">
          Open Positions
        </p>
        <p>
          Account ID · Currency · FX Rate To Base · Asset Class · Symbol ·
          Description · Conid · ISIN · Listing Exchange · Report Date · Quantity ·
          Mark Price · Position Value · Cost Basis Price · Cost Basis Money ·
          Percent of NAV · Unrealized P/L · Side · Level of Detail · Open Date Time
        </p>

        <p className="mt-2 font-medium text-neutral-700 dark:text-neutral-300">
          Cash Report
        </p>
        <p>
          Tick <em>Exclude segments and MTD/YTD breakout</em> and{" "}
          <em>Base Currency Summary</em>. Fields: Account ID · Currency · From/To
          Date · Level of Detail · Starting Cash · Ending Cash · Ending Settled
          Cash · Deposit/Withdrawals · Net Trades Sales · Net Trades Purchases ·
          Dividends · Withholding Tax · Broker Interest Paid and Received
        </p>

        <p className="mt-2 font-medium text-neutral-700 dark:text-neutral-300">
          Trades
        </p>
        <p>
          Account ID · Currency · Asset Class · Symbol · Description · Conid ·
          Trade ID · Trade Date · Date/Time · Buy/Sell · Quantity · TradePrice ·
          Trade Money · Proceeds · IB Commission · Net Cash · Open/Close Indicator
          · Transaction Type · Exchange · Cost Basis · Realized P/L · Level Of
          Detail · Notes/Codes
        </p>

        <p className="mt-2 font-medium text-neutral-700 dark:text-neutral-300">
          Corporate Actions · Cash Transactions · NAV · Instrument Information
        </p>
        <p>
          The exhaustive lists for these, and the reasoning behind each choice,
          are in <code>docs/ibkr-flex-query.md</code> in the repository.
        </p>
      </Detail>

      <Detail summary="Why these particular fields">
        <p>
          <strong>Cost Basis Price</strong> is the one that matters most — it
          replaces the price this app currently records when you add a stock,
          which is a guess at what you paid.
        </p>
        <p className="mt-1.5">
          <strong>Trade ID</strong> is what stops a re-run double-counting
          trades. Without it the damage is silent: cost basis just drifts.
        </p>
        <p className="mt-1.5">
          <strong>Summary rather than Lot</strong> on Open Positions. A query
          emitting both would count each holding twice.
        </p>
        <p className="mt-1.5">
          <strong>Breakout by Day = No.</strong> Yes emits one row per position
          per day, turning a dozen rows into thousands.
        </p>
      </Detail>

      <div>
        <h3 className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Then
        </h3>
        <ol className="mt-1.5 list-decimal space-y-1 pl-4 text-xs text-neutral-600 dark:text-neutral-400">
          <li>
            Save. The numeric <strong>query id</strong> appears beside it in the
            list — that is the first field below.
          </li>
          <li>
            Right panel → <strong>Flex Web Service Configuration</strong> → the
            gear → enable → generate a token. It is shown once.
          </li>
        </ol>
      </div>

      <p className="text-xs text-neutral-500 dark:text-neutral-500">
        The token is read-only: it fetches statements and cannot place trades. It
        is encrypted before storage, never shown again and never sent to your
        browser. Flex data refreshes once overnight, so holdings here lag the
        market by a day by design.
      </p>
    </section>
  );
}
